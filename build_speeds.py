#!/usr/bin/env python3
"""Construye el dataset de límites de velocidad (maxspeed) para CaravanNavigator.

Uso:
  python build_speeds.py --out dist \
    --region spain:in/spain.osm.pbf:in/spain.poly

Cada muestra: [lat, lon, rumbo, velocidad, flags, largo]
  - se toma cada SAMPLE_M metros a lo largo de la vía, en el sentido de circulación
  - rumbo = el del tramo donde cae la muestra; largo = metros hasta la siguiente muestra o el final de la vía
  - flags: bit0 = vale en los dos sentidos, bit1 = estimada (no figura en OSM)
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
from build_lanes import direction
from build_restrictions import bearing, dist_m

ROADS = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}
SAMPLE_M = 200.0
COUNTRIES = {"spain": "ES", "portugal": "PT", "andorra": "AD"}
MIN_KMH, MAX_KMH = 5, 150
BIDIRECTIONAL, ESTIMATED = 1, 2

# Límites implícitos por tipo de vía (maxspeed=ES:urban, maxspeed:type=PT:rural...)
IMPLICIT = {
    "ES:urban": 50, "PT:urban": 50,
    "ES:rural": 90, "PT:rural": 90,
    "ES:motorway": 120, "PT:motorway": 120,
    "ES:trunk": 100,
    "ES:zone30": 30, "ES:urban:zone30": 30,
    "ES:living_street": 20, "PT:living_street": 20,
}


def parse_speed(raw):
    """Km/h de un valor maxspeed (número o código implícito); None si no es utilizable."""
    if raw is None:
        return None
    s = raw.strip()
    if not s or "mph" in s.lower():
        return None
    token = s.replace("|", ";").split(";")[0].strip()
    if token.isdigit():
        v = int(token)
        return v if MIN_KMH <= v <= MAX_KMH else None
    return IMPLICIT.get(token)


def estimate(country, hw, tags):
    """Límite supuesto cuando OSM no lo dice; None si no se estima nada."""
    if country in ("ES", "PT"):
        if hw == "motorway":
            return 120
        if hw == "living_street":
            return 20
    if country == "ES" and hw == "trunk" and tags.get("oneway", "").strip().lower() in ("yes", "true", "1"):
        return 120
    return None


def way_speeds(tags, country):
    """((km/h adelante, estimada), (km/h atrás, estimada)); km/h None si no hay dato."""
    hw = tags.get("highway")
    implicit = parse_speed(tags.get("maxspeed:type")) or parse_speed(tags.get("source:maxspeed"))
    est = estimate(country, hw, tags)
    out = []
    for suffix in ("forward", "backward"):
        v = parse_speed(tags.get("maxspeed:" + suffix))
        if v is None:
            v = parse_speed(tags.get("maxspeed"))
        if v is None:
            v = implicit
        if v is not None:
            out.append((v, False))
        elif est is not None:
            out.append((est, True))
        else:
            out.append((None, False))
    return out[0], out[1]


def walk(pts, speed, flags):
    """Muestras cada SAMPLE_M m siguiendo [pts] ((lat, lon) en orden de marcha)."""
    segs = []
    total = 0.0
    for (la1, lo1), (la2, lo2) in zip(pts, pts[1:]):
        d = dist_m(la1, lo1, la2, lo2)
        if d > 0:
            segs.append((total, d, la1, lo1, la2, lo2))
            total += d
    if not segs:
        return []
    out = []
    pos = 0.0
    i = 0
    while pos < total:
        while i < len(segs) - 1 and segs[i][0] + segs[i][1] <= pos:
            i += 1
        start, d, la1, lo1, la2, lo2 = segs[i]
        t = min(max((pos - start) / d, 0.0), 1.0)
        nxt = min(pos + SAMPLE_M, total)
        out.append([round(la1 + (la2 - la1) * t, 6), round(lo1 + (lo2 - lo1) * t, 6),
                    bearing(la1, lo1, la2, lo2), speed, flags, max(1, int(round(nxt - pos)))])
        pos += SAMPLE_M
    return out


def read_region(path, country, stats):
    items = []
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
        pts = [(n.location.lat, n.location.lon) for n in way.nodes if n.location.valid()]
        if len(pts) < 2:
            continue
        (fv, fe), (bv, be) = way_speeds(tags, country)
        d = direction(tags)
        made = []
        if d == 0 and fv is not None and fv == bv and fe == be:
            made += walk(pts, fv, BIDIRECTIONAL | (ESTIMATED if fe else 0))
        else:
            if d >= 0 and fv is not None:
                made += walk(pts, fv, ESTIMATED if fe else 0)
            if d <= 0 and bv is not None:
                made += walk(pts[::-1], bv, ESTIMATED if be else 0)
        if made:
            stats[hw] += len(made)
            items += made
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist")
    ap.add_argument("--region", action="append", required=True, metavar="NOMBRE:PBF:POLY")
    args = ap.parse_args()

    t0 = time.time()
    items = []
    stats = Counter()
    for spec in args.region:
        m = REGION_RE.match(spec)
        if not m:
            sys.exit(f"--region inválida: {spec}")
        name, pbf, _poly = m.groups()
        t = time.time()
        found = read_region(pbf, COUNTRIES.get(name.lower()), stats)
        items += found
        print(f"[{name}] {len(found)} muestras, {time.time() - t:.1f} s")

    # Una vía partida entre regiones puede aparecer dos veces: se descartan las muestras idénticas
    seen = set()
    unique = []
    for it in items:
        key = tuple(it)
        if key not in seen:
            seen.add(key)
            unique.append(it)
    unique.sort(key=lambda it: (it[0], it[1], it[2]))

    generated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(args.out, exist_ok=True)
    gz_path = os.path.join(args.out, "speeds-iberia.json.gz")

    def dump(o):
        return json.dumps(o, ensure_ascii=False, separators=(",", ":"))

    with gzip.open(gz_path, "wt", encoding="utf-8", compresslevel=9) as f:
        f.write('{"v":1,"generated":%s,"items":[' % dump(generated))
        f.write(",".join(dump(e) for e in unique))
        f.write("]}")

    with open(gz_path, "rb") as f:
        raw = f.read()
    estimated = sum(1 for it in unique if it[4] & ESTIMATED)
    meta = {
        "v": 1, "generated": generated, "count": len(unique), "estimated": estimated,
        "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "file": "speeds-iberia.json.gz",
    }
    with open(os.path.join(args.out, "speeds-iberia.meta.json"), "w", encoding="utf-8") as f:
        f.write(dump(meta))

    print("Por clase de vía:")
    for hw, n in sorted(stats.items()):
        print(f"  {hw}: {n}")
    print(f"Total: {len(unique)} muestras ({estimated} estimadas), {len(raw) / 1e6:.2f} MB gz, {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
