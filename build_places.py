#!/usr/bin/env python3
"""Construye el dataset de lugares (ciudades, pueblos, aldeas, barrios) para la búsqueda sin conexión de CaravanNavigator.

Uso:
  python build_places.py --out dist --name iberia \
    --region spain:in/spain.osm.pbf:in/spain.poly

Solo se leen NODOS con place=* y name, así que no hace falta el índice de nodos de osmium.
Cada lugar: {"n": nombre, "a": [alternativos], "t": tipo, "p": población, "y": lat, "x": lon, "c": contexto}
  - c: para village/suburb/hamlet/quarter/neighbourhood, la city o town más cercana a <= 25 km (vacío si no hay);
    para city/town siempre vacío.
"""
import argparse
import datetime
import gzip
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict

import osmium

from build_dataset import REGION_RE, crossings, inside, parse_poly, tag_dict
from build_restrictions import dist_m

PLACE_TYPES = {"city", "town", "village", "suburb", "hamlet", "quarter", "neighbourhood"}
BIG = {"city", "town"}
ALT_KEYS = ["name:es", "name:fr", "name:pt", "name:ca", "name:eu", "name:gl"]
MAX_ALTS = 4
CONTEXT_KM = 25.0
CELL = 0.25  # grados de la rejilla de ciudades/pueblos


def alternatives(tags, name):
    """Hasta MAX_ALTS nombres distintos de [name], sin duplicados (sin distinguir mayúsculas)."""
    raw = [tags.get(k, "") for k in ALT_KEYS]
    raw += tags.get("alt_name", "").split(";")
    raw.append(tags.get("official_name", ""))
    seen = {name.lower()}
    out = []
    for v in raw:
        v = v.strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
            if len(out) == MAX_ALTS:
                break
    return out


def population(tags):
    s = tags.get("population", "").strip()
    return int(s) if s.isdigit() else 0


def read_region(path):
    places = []
    fp = (osmium.FileProcessor(path, osmium.osm.NODE)
          .with_filter(osmium.filter.KeyFilter("place")))
    for node in fp:
        if not node.is_node() or not node.location.valid():
            continue
        tags = tag_dict(node)
        t = tags.get("place")
        name = tags.get("name", "").strip()
        if t not in PLACE_TYPES or not name:
            continue
        places.append({
            "n": name, "a": alternatives(tags, name), "t": t, "p": population(tags),
            "y": round(node.location.lat, 5), "x": round(node.location.lon, 5), "c": "",
        })
    return places


def in_bbox(poly_path, places):
    """Descarta lugares fuera del polígono del .poly. Los extractos traen relaciones completas (límites
    administrativos que tocan el país) con sus nodos de capital: sin esto Toulouse o Perpiñán entrarían en iberia."""
    outer, holes = parse_poly(poly_path)
    if not outer:
        return places
    rings = outer + holes
    cache = {}
    kept = []
    for p in places:
        lat = p["y"]
        xs = cache.get(lat)
        if xs is None:
            xs = cache[lat] = crossings(rings, lat)
        if inside(xs, p["x"]):
            kept.append(p)
    return kept


def assign_context(places):
    """Rellena "c" con la city/town más cercana a <= CONTEXT_KM, usando una rejilla de CELL grados."""
    grid = defaultdict(list)
    for p in places:
        if p["t"] in BIG:
            grid[(math.floor(p["y"] / CELL), math.floor(p["x"] / CELL))].append(p)
    for p in places:
        if p["t"] in BIG:
            continue
        # 25 km en grados de longitud crecen con la latitud: se calcula el alcance en celdas
        dlat = CONTEXT_KM / 111.0
        dlon = CONTEXT_KM / (111.0 * max(math.cos(math.radians(p["y"])), 0.1))
        r0, r1 = math.floor((p["y"] - dlat) / CELL), math.floor((p["y"] + dlat) / CELL)
        c0, c1 = math.floor((p["x"] - dlon) / CELL), math.floor((p["x"] + dlon) / CELL)
        best, best_d = None, CONTEXT_KM * 1000.0
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                for q in grid.get((r, c), ()):
                    d = dist_m(p["y"], p["x"], q["y"], q["x"])
                    if d <= best_d:
                        best, best_d = q, d
        if best is not None:
            p["c"] = best["n"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist")
    ap.add_argument("--name", default="iberia", help="sufijo de los ficheros de salida")
    ap.add_argument("--locations", default="flex_mem", help="ignorado (solo se leen nodos); por compatibilidad")
    ap.add_argument("--region", action="append", required=True, metavar="NOMBRE:PBF:POLY")
    args = ap.parse_args()

    t0 = time.time()
    places = []
    for spec in args.region:
        m = REGION_RE.match(spec)
        if not m:
            sys.exit(f"--region inválida: {spec}")
        name, pbf, poly = m.groups()
        t = time.time()
        found = in_bbox(poly, read_region(pbf))
        places += found
        print(f"[{name}] {len(found)} lugares, {time.time() - t:.1f} s")

    # Un lugar en la frontera puede venir de dos regiones: se descartan los idénticos
    seen = set()
    unique = []
    for p in places:
        key = (p["n"], p["t"], p["y"], p["x"])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    unique.sort(key=lambda p: (p["n"], p["y"], p["x"]))
    assign_context(unique)

    generated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(args.out, exist_ok=True)
    gz_path = os.path.join(args.out, f"places-{args.name}.json.gz")

    def dump(o):
        return json.dumps(o, ensure_ascii=False, separators=(",", ":"))

    with gzip.open(gz_path, "wt", encoding="utf-8", compresslevel=9) as f:
        f.write('{"v":1,"generated":%s,"places":[' % dump(generated))
        f.write(",".join(dump(p) for p in unique))
        f.write("]}")

    with open(gz_path, "rb") as f:
        raw = f.read()
    meta = {
        "v": 1, "generated": generated, "count": len(unique),
        "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "file": f"places-{args.name}.json.gz",
    }
    with open(os.path.join(args.out, f"places-{args.name}.meta.json"), "w", encoding="utf-8") as f:
        f.write(dump(meta))

    print("Por tipo:")
    for t, n in sorted(Counter(p["t"] for p in unique).items()):
        print(f"  {t}: {n}")
    print(f"Total: {len(unique)} lugares, {len(raw) / 1e6:.2f} MB gz, {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
