#!/usr/bin/env python3
"""Escribe dist/maps-meta-<region>.json a partir de <region>.pmtiles, sus partes y map-assets.zip (solo biblioteca estándar).

Con --legacy escribe además dist/maps-meta.json idéntico (compatibilidad con apps ya instaladas)."""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default="dist")
    ap.add_argument("--name", default="iberia.pmtiles")
    ap.add_argument("--bbox", required=True, help="w,s,e,n")
    ap.add_argument("--maxzoom", type=int, default=14)
    ap.add_argument("--region", default="iberia", help="id de la región; fichero de salida maps-meta-<region>.json")
    ap.add_argument("--assets", default=None, help="ruta de map-assets.zip (por defecto <dist>/map-assets.zip)")
    ap.add_argument("--legacy", action="store_true", help="escribe también maps-meta.json idéntico")
    args = ap.parse_args()
    whole = os.path.join(args.dist, args.name)
    parts = sorted(glob.glob(whole + ".part*"))
    if not parts:
        raise SystemExit("No hay partes")
    w, s, e, n = (float(x) for x in args.bbox.strip().strip("()").replace(" ", "").split(","))
    meta = {
        "v": 1,
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "maxzoom": args.maxzoom,
        "bounds": [w, s, e, n],
        "totalBytes": os.path.getsize(whole),
        "sha256": sha256(whole),
        "parts": [entry(p) for p in parts],
        "assets": entry(args.assets or os.path.join(args.dist, "map-assets.zip")),
        "region": args.region,
    }
    if sum(p["bytes"] for p in meta["parts"]) != meta["totalBytes"]:
        raise SystemExit("Las partes no suman el tamaño total")
    names = [f"maps-meta-{args.region}.json"] + (["maps-meta.json"] if args.legacy else [])
    for name in names:
        with open(os.path.join(args.dist, name), "w", encoding="utf-8") as f:
            json.dump(meta, f, separators=(",", ":"))
    print(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
