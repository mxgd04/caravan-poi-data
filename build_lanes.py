#!/usr/bin/env python3
"""Construye el dataset de carriles (turn:lanes) y carteles (destination, salidas) para CaravanNavigator.

Uso:
  python build_lanes.py --out dist \
    --region spain:in/spain.osm.pbf:in/spain.poly

Cada elemento:
  carriles: [id, lat, lon, rumbo, "L", turn:lanes]            (punto = final de la vía en el sentido de circulación)
  cartel:   [id, lat, lon, rumbo, "S", salida, refs, destinos, lat2, lon2]
            (punto = inicio de la vía en el sentido de circulación; lat2/lon2 = ~100 m más adelante)
"""
import argparse
import datetime
import gzip
import hashlib
import json
import os
import sys
import time
from collections import Counter

import osmium

from build_dataset import REGION_RE, tag_dict
from build_restrictions import bearing, dist_m

ROADS = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}
KINDS = {"L": "carriles", "S": "carteles"}
MAX_TEXT = 120
# Segundo punto del cartel: distingue ramales que salen del mismo nodo con rumbo parecido
SECOND_POINT_M = 100.0


def direction(tags):
    """+1 adelante, -1 atrás, 0 si la vía no es de sentido único."""
    oneway = tags.get("oneway", "").strip().lower()
    if oneway in ("yes", "true", "1"):
        return 1
    if oneway == "-1":
        return -1
    if not oneway and (tags.get("highway", "").endswith("_link") or tags.get("highway") == "motorway"
                       or tags.get("junction") in ("roundabout", "circular")):
        return 1
    return 0


def normalize_lanes(raw):
    """turn:lanes en minúsculas y sin espacios; None si hay < 2 carriles o son todos iguales."""
    if not raw:
        return None
    s = "".join(raw.lower().split())
    lanes = s.split("|")
    if len(lanes) < 2 or len(set(lanes)) == 1:
        return None
    return s


def read_junctions(path):
    """{id de nodo: ref} de los nodos highway=motorway_junction con ref."""
    found = {}
    fp = osmium.FileProcessor(path, osmium.osm.NODE).with_filter(osmium.filter.KeyFilter("highway"))
    for node in fp:
        tags = tag_dict(node)
        if tags.get("highway") == "motorway_junction" and tags.get("ref"):
            found[node.id] = tags["ref"].strip()
    return found


def point_ahead(pts, metres):
    """(lat, lon) a [metres] del inicio siguiendo la vía; el último nodo si es más corta."""
    walked = 0.0
    for (_, la1, lo1), (_, la2, lo2) in zip(pts, pts[1:]):
        seg = dist_m(la1, lo1, la2, lo2)
        if seg > 0 and walked + seg >= metres:
            t = (metres - walked) / seg
            return la1 + (la2 - la1) * t, lo1 + (lo2 - lo1) * t
        walked += seg
    return pts[-1][1], pts[-1][2]


def clip(text):
    return (text or "").strip()[:MAX_TEXT]


def read_region(path):
    """Devuelve {id: [lat, lon, rumbo, tipo, ...]} de un extracto."""
    junctions = read_junctions(path)
    items = {}
    fp = (osmium.FileProcessor(path, osmium.osm.NODE | osmium.osm.WAY)
          .with_locations("flex_mem")
          .with_filter(osmium.filter.KeyFilter("highway")))
    for way in fp:
        if not way.is_way():
            continue
        tags = tag_dict(way)
        hw = tags.get("highway")
        if hw not in ROADS:
            continue
        pts = [(n.ref, n.location.lat, n.location.lon) for n in way.nodes if n.location.valid()]
        if len(pts) < 2:
            continue
        d = direction(tags)

        # Carriles: solo en el sentido de circulación de vías de sentido único (o con sufijo explícito)
        for suffix, sign, tag in (("f", 1, "turn:lanes:forward"), ("b", -1, "turn:lanes:backward")):
            raw = tags.get(tag)
            if raw is None and d == sign:
                raw = tags.get("turn:lanes")
            lanes = normalize_lanes(raw)
            if lanes is None:
                continue
            if sign == 1:
                prev, last = pts[-2], pts[-1]
            else:
                prev, last = pts[1], pts[0]
            items[f"l{way.id}{suffix}"] = [round(last[1], 6), round(last[2], 6),
                                           bearing(prev[1], prev[2], last[1], last[2]), "L", lanes]

        # Carteles: solo sentido único
        if d == 0:
            continue
        first, second = (pts[0], pts[1]) if d == 1 else (pts[-1], pts[-2])
        refs = clip(tags.get("destination:ref"))
        dests = clip(tags.get("destination"))
        exit_ref = clip(tags.get("junction:ref"))
        if not exit_ref and hw.endswith("_link"):
            exit_ref = clip(junctions.get(first[0]))
        if not (refs or dests or exit_ref):
            continue
        ahead = point_ahead(pts if d == 1 else pts[::-1], SECOND_POINT_M)
        items[f"s{way.id}"] = [round(first[1], 6), round(first[2], 6),
                               bearing(first[1], first[2], second[1], second[2]), "S", exit_ref, refs, dests,
                               round(ahead[0], 6), round(ahead[1], 6)]
    print(f"  salidas con ref {len(junctions)}")
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist")
    ap.add_argument("--region", action="append", required=True, metavar="NOMBRE:PBF:POLY")
    args = ap.parse_args()

    t0 = time.time()
    merged = {}
    for spec in args.region:
        m = REGION_RE.match(spec)
        if not m:
            sys.exit(f"--region inválida: {spec}")
        name, pbf, _poly = m.groups()
        t = time.time()
        found = read_region(pbf)
        before = len(merged)
        for key, value in found.items():
            merged.setdefault(key, value)
        print(f"[{name}] {len(found)} elementos ({len(merged) - before} nuevos), {time.time() - t:.1f} s")

    entries = [[key] + merged[key] for key in sorted(merged)]
    counts = Counter(e[4] for e in entries)
    generated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(args.out, exist_ok=True)
    gz_path = os.path.join(args.out, "lanes-iberia.json.gz")

    def dump(o):
        return json.dumps(o, ensure_ascii=False, separators=(",", ":"))

    with gzip.open(gz_path, "wt", encoding="utf-8", compresslevel=9) as f:
        f.write('{"v":1,"generated":%s,"items":[' % dump(generated))
        f.write(",".join(dump(e) for e in entries))
        f.write("]}")

    with open(gz_path, "rb") as f:
        raw = f.read()
    meta = {
        "v": 1, "generated": generated, "count": len(entries), "kinds": dict(counts),
        "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "file": "lanes-iberia.json.gz",
    }
    with open(os.path.join(args.out, "lanes-iberia.meta.json"), "w", encoding="utf-8") as f:
        f.write(dump(meta))

    print("Por tipo:")
    for kind, n in sorted(counts.items()):
        print(f"  {kind} ({KINDS[kind]}): {n}")
    print(f"Total: {len(entries)} elementos, {len(raw) / 1e6:.2f} MB gz, {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
