#!/usr/bin/env python3
"""Escribe dist/maps-meta-<region>.json a partir de <region>.pmtiles, sus partes y map-assets.zip (solo biblioteca estándar).

Con --legacy escribe además dist/maps-meta.json idéntico (compatibilidad con apps ya instaladas).

Con --poly (uno por país) el meta lleva "areas" (los polígonos de Geofabrik) y "bounds" es su caja: la caja de los
datos del pbf no sirve, porque los extractos incluyen relaciones completas que llegan muy lejos del país.
Con --patch solo añade areas/bounds a un meta ya publicado (sin tocar partes ni sha256)."""
import argparse
import datetime
import glob
import hashlib
import json
import os


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def entry(path):
    return {"file": os.path.basename(path), "bytes": os.path.getsize(path), "sha256": sha256(path)}


def read_poly(path):
    """Secciones de un .poly de Osmosis: lista de anillos [[lon, lat], ...]. Se omiten los huecos ("!")."""
    with open(path, encoding="utf-8") as f:
        lines = [line.strip() for line in f]
    rings, i = [], 1
    while i < len(lines):
        name = lines[i]
        i += 1
        if not name or name == "END":
            continue
        ring = []
        while lines[i] != "END":
            lon, lat = (float(v) for v in lines[i].split())
            ring.append([round(lon, 3), round(lat, 3)])
            i += 1
        i += 1
        if not name.startswith("!"):
            rings.append(ring)
    return rings


def areas_and_bounds(polys):
    areas = [ring for path in polys for ring in read_poly(path)]
    lons = [p[0] for ring in areas for p in ring]
    lats = [p[1] for ring in areas for p in ring]
    return areas, [min(lons), min(lats), max(lons), max(lats)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default="dist")
    ap.add_argument("--name", default="iberia.pmtiles")
    ap.add_argument("--bbox", help="w,s,e,n (si no hay --poly)")
    ap.add_argument("--poly", action="append", default=[], help="polígono .poly de un país (repetible)")
    ap.add_argument("--patch", help="meta ya publicado al que solo se añaden areas y bounds (se reescribe)")
    ap.add_argument("--maxzoom", type=int, default=14)
    ap.add_argument("--region", default="iberia", help="id de la región; fichero de salida maps-meta-<region>.json")
    ap.add_argument("--assets", default=None, help="ruta de map-assets.zip (por defecto <dist>/map-assets.zip)")
    ap.add_argument("--legacy", action="store_true", help="escribe también maps-meta.json idéntico")
    args = ap.parse_args()
    if args.patch:
        if not args.poly:
            raise SystemExit("--patch necesita --poly")
        with open(args.patch, encoding="utf-8") as f:
            meta = json.load(f)
        meta["areas"], meta["bounds"] = areas_and_bounds(args.poly)
        with open(args.patch, "w", encoding="utf-8") as f:
            json.dump(meta, f, separators=(",", ":"))
        print(args.patch, meta["bounds"], len(meta["areas"]), "áreas")
        return
    whole = os.path.join(args.dist, args.name)
    parts = sorted(glob.glob(whole + ".part*"))
    if not parts:
        raise SystemExit("No hay partes")
    areas = None
    if args.poly:
        areas, bounds = areas_and_bounds(args.poly)
    elif args.bbox:
        bounds = [float(x) for x in args.bbox.strip().strip("()").replace(" ", "").split(",")]
    else:
        raise SystemExit("Hace falta --poly o --bbox")
    meta = {
        "v": 1,
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "maxzoom": args.maxzoom,
        "bounds": bounds,
        "totalBytes": os.path.getsize(whole),
        "sha256": sha256(whole),
        "parts": [entry(p) for p in parts],
        "assets": entry(args.assets or os.path.join(args.dist, "map-assets.zip")),
        "region": args.region,
    }
    if areas:
        meta["areas"] = areas
    if sum(p["bytes"] for p in meta["parts"]) != meta["totalBytes"]:
        raise SystemExit("Las partes no suman el tamaño total")
    names = [f"maps-meta-{args.region}.json"] + (["maps-meta.json"] if args.legacy else [])
    for name in names:
        with open(os.path.join(args.dist, name), "w", encoding="utf-8") as f:
            json.dump(meta, f, separators=(",", ":"))
    print(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
