#!/usr/bin/env python3
"""Genera los recursos de estilo para el mapa sin conexión (solo biblioteca estándar).

Para cada estilo de OpenFreeMap (liberty, bright, positron, dark):
  - descarga el JSON y se queda con la fuente vectorial principal (la usada por más capas);
  - esa fuente pasa a {"type":"vector","url":"{{PMTILES}}"} y se eliminan las otras fuentes y sus capas;
  - el sprite y las tipografías (glyphs) se descargan y se reescriben a {{ASSETS}}/...
Escribe dist/map-assets.zip con styles/<n>.json, sprites/<n>/sprite*.{json,png} y fonts/<stack>/<rango>.pbf.
La app sustituye {{PMTILES}} y {{ASSETS}} al cargar el estilo.
"""
import argparse
import collections
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

STYLES = ["liberty", "bright", "positron", "dark"]
STYLE_URL = "https://tiles.openfreemap.org/styles/{}"
RANGES = ["0-255", "256-511", "512-767", "768-1023", "8192-8447"]
SPRITE_FILES = ["sprite.json", "sprite.png", "sprite@2x.json", "sprite@2x.png"]
UA = {"User-Agent": "caravan-poi-data/1.0 (+https://github.com/mxgd04/caravan-poi-data)"}
# Operadores de expresión que nunca son nombres de tipografía
EXPR_OPS = {"literal", "get", "case", "match", "step", "interpolate", "coalesce", "concat", "let", "var", "to-string"}


def fetch(url, quiet404=False):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if quiet404 and e.code == 404:
            return None
        raise


def main_vector_source(style):
    """Fuente vectorial referenciada por más capas."""
    vector = {k for k, v in style["sources"].items() if v.get("type") == "vector"}
    counts = collections.Counter(l.get("source") for l in style["layers"] if l.get("source") in vector)
    if not counts:
        raise SystemExit("El estilo no tiene fuente vectorial")
    return counts.most_common(1)[0][0]


def font_stacks(value, out):
    """Recoge los font stacks de un valor de text-font (lista literal o expresión)."""
    if isinstance(value, str):
        out.add(value)
    elif isinstance(value, list):
        if value and all(isinstance(x, str) for x in value) and value[0] not in EXPR_OPS:
            out.add(",".join(value))
        elif value and value[0] == "literal" and len(value) == 2 and isinstance(value[1], list):
            font_stacks(value[1], out)
        else:
            for item in value:
                if isinstance(item, list):
                    font_stacks(item, out)


def process_style(name, out_dir, stacks):
    style = json.loads(fetch(STYLE_URL.format(name)))
    keep = main_vector_source(style)
    style["sources"] = {keep: {"type": "vector", "url": "{{PMTILES}}"}}
    # Capas de fondo (sin fuente) se conservan; las de otras fuentes se descartan
    style["layers"] = [l for l in style["layers"] if l.get("source") in (None, keep)]
    sprite_url = style["sprite"]
    glyphs_url = style["glyphs"]
    style["sprite"] = "{{ASSETS}}/sprites/%s/sprite" % name
    style["glyphs"] = "{{ASSETS}}/fonts/{fontstack}/{range}.pbf"
    for layer in style["layers"]:
        tf = layer.get("layout", {}).get("text-font")
        if tf is not None:
            font_stacks(tf, stacks[glyphs_url])
    (out_dir / "styles").mkdir(parents=True, exist_ok=True)
    (out_dir / "styles" / f"{name}.json").write_text(json.dumps(style, ensure_ascii=False, separators=(",", ":")), "utf-8")
    sdir = out_dir / "sprites" / name
    sdir.mkdir(parents=True, exist_ok=True)
    for f in SPRITE_FILES:
        url = sprite_url + f[len("sprite"):]
        (sdir / f).write_bytes(fetch(url))
    print(f"{name}: fuente '{keep}', {len(style['layers'])} capas")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist")
    ap.add_argument("--work", default="build-assets", help="carpeta temporal")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(args.work)
    stacks = collections.defaultdict(set)  # url de glyphs -> font stacks
    for name in STYLES:
        process_style(name, work, stacks)
    fetched = 0
    for glyphs_url, names in stacks.items():
        for stack in sorted(names):
            fdir = work / "fonts" / stack
            fdir.mkdir(parents=True, exist_ok=True)
            for rng in RANGES:
                url = glyphs_url.replace("{fontstack}", urllib.parse.quote(stack)).replace("{range}", rng)
                data = fetch(url, quiet404=True)
                if data is not None:
                    (fdir / f"{rng}.pbf").write_bytes(data)
                    fetched += 1
    print(f"tipografías: {sum(len(s) for s in stacks.values())} stacks, {fetched} ficheros")
    zip_path = out / "map-assets.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(work.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(work).as_posix())
    print(f"{zip_path}: {zip_path.stat().st_size} bytes")


if __name__ == "__main__":
    sys.exit(main())
