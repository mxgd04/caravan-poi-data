#!/usr/bin/env python3
"""Construye el dataset de restricciones viarias (alto, ancho, peso, longitud) para CaravanNavigator.

Uso:
  python build_restrictions.py --out dist \
    --region spain:in/spain.osm.pbf:in/spain.poly

Cubre lo que ORS no usa: límites en NODOS (gálibos, barreras), anchura física de las vías y `narrow=yes`.
Cada elemento: [id, lat, lon, rumbo, tipo, valor]; tipo H/W/N/T/L (ver KINDS); rumbo = de la vía (0-359).
"""
import argparse
import datetime
import gzip
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter

import osmium

from build_dataset import REGION_RE, tag_dict

DRIVABLE = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "residential", "service", "unclassified",
    "living_street", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}
KINDS = {"H": "alto (m)", "W": "ancho (m)", "N": "vía estrecha", "T": "peso (t)", "L": "largo (m)"}
NODE_KEYS = ["maxheight", "maxheight:physical", "maxwidth", "maxwidth:physical", "maxweight", "maxlength"]
SAMPLE_M = 25.0
NARROW_WIDTH_M = 3.0
MIN_WIDTH_M = 1.0
R = 6_371_000.0

_METRIC = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(m|t|kg)?$")
_FEET = re.compile(r"^(\d+)\s*'\s*(?:(\d+(?:\.\d+)?)\s*(?:\"|''))?$")


def parse_value(raw, unit):
    """Valor en m (unit 'm') o t (unit 't'); None si no se entiende ('none', 'default', 'below_default'...)."""
    if raw is None:
        return None
    s = raw.strip().lower()
    m = _METRIC.match(s)
    if m:
        value = float(m.group(1).replace(",", "."))
        suffix = m.group(2) or ""
        if unit == "t":
            if suffix == "kg":
                value /= 1000.0
            elif suffix == "m":
                return None
        elif suffix in ("t", "kg"):
            return None
        return value if value > 0 else None
    f = _FEET.match(s)
    if f and unit == "m":
        metres = (int(f.group(1)) * 12 + float(f.group(2) or 0)) * 0.0254
        return metres if metres > 0 else None
    return None


def node_items(tags):
    """[(tipo, valor)] de un nodo con restricciones; vacío si no tiene ninguna."""
    out = []
    height_keys = ("maxheight", "maxheight:physical")
    for kind, keys, unit in (
        ("H", height_keys, "m"),
        ("W", ("maxwidth", "maxwidth:physical"), "m"),
        ("T", ("maxweight",), "t"),
        ("L", ("maxlength",), "m"),
    ):
        values = [v for v in (parse_value(tags.get(k), unit) for k in keys) if v is not None]
        if values:
            out.append((kind, min(values)))
    return out


def bearing(lat1, lon1, lat2, lon2):
    dx = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    dy = math.radians(lat2 - lat1)
    return int(round(math.degrees(math.atan2(dx, dy)))) % 360


def dist_m(lat1, lon1, lat2, lon2):
    dx = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    dy = math.radians(lat2 - lat1)
    return math.hypot(dx, dy) * R


def read_candidate_nodes(path):
    """{id de nodo: [(tipo, valor)]} de los nodos con alguna restricción numérica."""
    found = {}
    fp = osmium.FileProcessor(path, osmium.osm.NODE).with_filter(osmium.filter.KeyFilter(*NODE_KEYS))
    for node in fp:
        items = node_items(tag_dict(node))
        if items:
            found[node.id] = items
    return found


def samples(points):
    """Puntos (lat, lon, rumbo) cada SAMPLE_M a lo largo de la polilínea, más el primero y el último."""
    out = []
    last = None
    carry = 0.0  # metros hasta la próxima muestra, medidos desde el inicio del segmento actual
    for (la1, lo1), (la2, lo2) in zip(points, points[1:]):
        seg = dist_m(la1, lo1, la2, lo2)
        if seg == 0:
            continue
        b = bearing(la1, lo1, la2, lo2)
        if not out:
            out.append((la1, lo1, b))
            carry = SAMPLE_M
        pos = carry
        while pos < seg:
            t = pos / seg
            out.append((la1 + (la2 - la1) * t, lo1 + (lo2 - lo1) * t, b))
            pos += SAMPLE_M
        carry = pos - seg
        last = (la2, lo2, b)
    if out and last and (abs(out[-1][0] - last[0]) > 1e-9 or abs(out[-1][1] - last[1]) > 1e-9):
        out.append(last)
    return out


def read_region(path):
    """Devuelve {id: [lat, lon, rumbo, tipo, valor]} de un extracto."""
    cand = read_candidate_nodes(path)
    pending = set(cand)
    items = {}
    fp = (osmium.FileProcessor(path, osmium.osm.NODE | osmium.osm.WAY)
          .with_locations("flex_mem")
          .with_filter(osmium.filter.KeyFilter("highway")))
    for way in fp:
        if not way.is_way():
            continue
        tags = tag_dict(way)
        if tags.get("highway") not in DRIVABLE:
            continue
        pts = [(n.ref, n.location.lat, n.location.lon) for n in way.nodes if n.location.valid()]
        if len(pts) < 2:
            continue
        for i, (ref, lat, lon) in enumerate(pts):
            if ref not in pending:
                continue
            pending.discard(ref)  # solo la primera vía transitable que lo contiene
            a = pts[i - 1] if i > 0 else pts[i]
            b = pts[i + 1] if i + 1 < len(pts) else pts[i]
            brg = bearing(a[1], a[2], b[1], b[2])
            entries = cand[ref]
            for kind, value in entries:
                # Un nodo con varias restricciones necesita ids distintos
                key = f"n{ref}" if len(entries) == 1 else f"n{ref}{kind}"
                items[key] = [round(lat, 6), round(lon, 6), brg, kind, round(value, 2)]

        width = None
        for k in ("width", "est_width"):
            width = parse_value(tags.get(k), "m")
            if width is not None:
                break
        if width is not None and MIN_WIDTH_M <= width < NARROW_WIDTH_M:
            kind, value = "W", width
        elif width is None and tags.get("narrow") == "yes":
            kind, value = "N", 0.0
        else:
            continue
        for j, (lat, lon, brg) in enumerate(samples([(p[1], p[2]) for p in pts])):
            items[f"w{way.id}:{j}"] = [round(lat, 6), round(lon, 6), brg, kind, round(value, 2)]
    print(f"  nodos candidatos {len(cand)}, sin vía transitable {len(pending)}")
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
        print(f"[{name}] {len(found)} restricciones ({len(merged) - before} nuevas), {time.time() - t:.1f} s")

    entries = [[key] + merged[key] for key in sorted(merged)]
    counts = Counter(e[4] for e in entries)
    generated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(args.out, exist_ok=True)
    gz_path = os.path.join(args.out, "restrictions-iberia.json.gz")

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
        "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "file": "restrictions-iberia.json.gz",
    }
    with open(os.path.join(args.out, "restrictions-iberia.meta.json"), "w", encoding="utf-8") as f:
        f.write(dump(meta))

    print("Por tipo:")
    for kind, n in sorted(counts.items()):
        print(f"  {kind} ({KINDS[kind]}): {n}")
    print(f"Total: {len(entries)} restricciones, {len(raw) / 1e6:.2f} MB gz, {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
