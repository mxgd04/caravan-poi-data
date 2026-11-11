#!/usr/bin/env python3
"""Construye el dataset de POIs para CaravanNavigator a partir de extractos .osm.pbf.

Uso:
  python build_dataset.py --out dist \
    --region spain:in/spain.osm.pbf:in/spain.poly \
    --region portugal:in/portugal.osm.pbf:in/portugal.poly

Selecciona exactamente lo mismo que la consulta Overpass de la app
(PoiRepository.buildQuery, conjunto ALL) y calcula las teselas z10 cubiertas.
"""
import argparse
import bisect
import collections
import datetime
import gzip
import hashlib
import json
import math
import os
import re
import sys
import time

import osmium

# Índice de nodos de osmium (--locations). Con "sparse_file_array,<ruta>" va a disco; se borra el fichero
# antes de cada pasada para no reutilizar un índice de otro extracto.
LOCATIONS = "flex_mem"


def locations():
    if LOCATIONS.startswith("sparse_file_array,"):
        try:
            os.remove(LOCATIONS.split(",", 1)[1])
        except FileNotFoundError:
            pass
    return LOCATIONS

ZOOM = 10
N = 1 << ZOOM
MAX_LAT = 85.0

YES_DESIGNATED = {"yes", "designated"}
TOURISM = {"caravan_site", "camp_site"}
AMENITY = {"sanitary_dump_station", "drinking_water", "water_point", "toilets", "fuel"}
HIGHWAY = {"rest_area", "services"}
SPEED_CAMERA = "speed_camera"  # solo nodos

# Pares clave/valor que preseleccionan en C++; el resto de condiciones se comprueban en matches().
TAG_PAIRS = (
    [("tourism", v) for v in TOURISM]
    + [("amenity", v) for v in AMENITY | {"parking"}]
    + [("sanitary_dump_station", "yes")]
    + [("highway", v) for v in HIGHWAY | {SPEED_CAMERA}]
)

# Claves que lee la app (PoiClassifier y PoiDetails).
KEEP_KEYS = [
    "name", "tourism", "amenity", "highway", "parking", "motorhome", "caravan",
    "sanitary_dump_station", "drinking_water", "water_point", "power_supply", "electricity",
    "toilets", "shower", "wifi", "internet_access", "fee", "charge", "website", "contact:website",
    "phone", "contact:phone", "opening_hours", "maxstay", "capacity", "access", "description",
    # Primera impresión en la ficha
    "stars", "image", "wikimedia_commons", "capacity:motorhomes", "capacity:caravans", "dog",
    "maxlength", "reservation", "surface",
]
# Solo para radares fijos (no se añaden al resto de POIs para no cambiar su salida)
CAMERA_KEYS = ["maxspeed", "direction", "enforcement", "section", "section_id"]
# Relaciones type=enforcement: semáforo (radar de semáforo) y velocidad media (radar de tramo)
RED_LIGHT = {"traffic_signals", "red_light_camera"}
AVERAGE_SPEED = "average_speed"
FIRST_VALUE_KEYS = {"website", "contact:website", "phone", "contact:phone"}
MAX_DESCRIPTION = 300

REGION_RE = re.compile(r"^([^:]+):((?:[A-Za-z]:)?[^:]+):((?:[A-Za-z]:)?[^:]+)$")


def matches(tags, is_node=False):
    amenity = tags.get("amenity")
    return (
        (is_node and tags.get("highway") == SPEED_CAMERA)
        or
        tags.get("tourism") in TOURISM
        or amenity in AMENITY
        or tags.get("sanitary_dump_station") == "yes"
        or tags.get("highway") in HIGHWAY
        or (amenity == "parking"
            and (tags.get("motorhome") in YES_DESIGNATED or tags.get("caravan") in YES_DESIGNATED))
    )


def clean_tags(tags):
    out = {}
    keys = KEEP_KEYS + CAMERA_KEYS if tags.get("highway") == SPEED_CAMERA else KEEP_KEYS
    for key in keys:
        value = tags.get(key)
        if value is None:
            continue
        if key in FIRST_VALUE_KEYS:
            value = value.split(";")[0].strip()
        elif key == "description":
            value = value[:MAX_DESCRIPTION]
        if value:
            out[key] = value
    return out


def tag_dict(obj):
    return {t.k: t.v for t in obj.tags}


def way_center(way):
    """Media de las posiciones de los nodos; en vías cerradas se descarta el último nodo repetido."""
    nodes = list(way.nodes)
    if len(nodes) > 1 and nodes[0].ref == nodes[-1].ref:
        nodes.pop()
    lats = lons = 0.0
    count = 0
    for node in nodes:
        loc = node.location
        if not loc.valid():
            continue
        lats += loc.lat
        lons += loc.lon
        count += 1
    return (lats / count, lons / count) if count else None


def read_region(path):
    """Devuelve {id: (lat, lon, tags)} de un extracto."""
    found = {}
    rel_members = {}  # id de relación -> (tags, [ids de vías])
    fp = (osmium.FileProcessor(path, osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION)
          .with_locations(locations())
          .with_filter(osmium.filter.TagFilter(*TAG_PAIRS)))
    for obj in fp:
        tags = tag_dict(obj)
        if not matches(tags, obj.is_node()):
            continue
        if obj.is_node():
            if obj.location.valid():
                found[f"node/{obj.id}"] = (obj.location.lat, obj.location.lon, tags)
        elif obj.is_way():
            center = way_center(obj)
            if center:
                found[f"way/{obj.id}"] = (center[0], center[1], tags)
        elif obj.is_relation():
            ways = [m.ref for m in obj.members if m.type == "w"]
            if ways:
                rel_members[obj.id] = (tags, ways)

    if rel_members:
        # Segunda pasada: solo las vías miembro, para calcular su posición
        wanted = {w for _, ways in rel_members.values() for w in ways}
        centers = {}
        fp2 = (osmium.FileProcessor(path, osmium.osm.NODE | osmium.osm.WAY)
               .with_locations(locations())
               .with_filter(osmium.filter.IdFilter(wanted)))
        for way in fp2:
            # IdFilter también deja pasar nodos cuyo id coincide con el de una vía (espacios de ids distintos)
            if not way.is_way():
                continue
            center = way_center(way)
            if center:
                centers[way.id] = center
        for rid, (tags, ways) in rel_members.items():
            pts = [centers[w] for w in ways if w in centers]
            if pts:
                found[f"relation/{rid}"] = (
                    sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts), tags)
    return found


def bearing_deg(a, b):
    """Rumbo inicial entero (0 = norte) del punto a=(lat, lon) hacia b."""
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    d_lon = math.radians(b[1] - a[1])
    y = math.sin(d_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    return int(round((math.degrees(math.atan2(y, x)) + 360) % 360)) % 360


def read_enforcement(path, stats):
    """Radares de semáforo y de tramo (relaciones type=enforcement) como {id: (lat, lon, tags)}.

    Solo se usan miembros nodo: device (la cámara), from/to (inicio y fin) y force. Dos pasadas:
    relaciones (ids de nodos necesarios) y nodos (posición y tags).
    """
    rels = {}  # id -> (tags, {rol: id de nodo})
    fp = (osmium.FileProcessor(path, osmium.osm.RELATION)
          .with_filter(osmium.filter.TagFilter(("type", "enforcement"))))
    for rel in fp:
        tags = tag_dict(rel)
        kind = tags.get("enforcement")
        if kind not in RED_LIGHT and kind != AVERAGE_SPEED:
            continue
        roles = {}
        for m in rel.members:
            if m.type == "n" and m.role in ("device", "from", "to", "force"):
                roles.setdefault(m.role, m.ref)
        rels[rel.id] = (tags, roles)
    wanted = {n for _, roles in rels.values() for n in roles.values()}
    locs = {}  # id de nodo -> (lat, lon, tags)
    if wanted:
        fp2 = (osmium.FileProcessor(path, osmium.osm.NODE).with_filter(osmium.filter.IdFilter(wanted)))
        for node in fp2:
            if node.id in wanted and node.location.valid():
                locs[node.id] = (node.location.lat, node.location.lon, tag_dict(node))

    def point(roles, role):
        n = roles.get(role)
        return locs.get(n) if n is not None else None

    found = {}
    for rid, (tags, roles) in rels.items():
        kind = tags["enforcement"]
        device, start, end, force = (point(roles, r) for r in ("device", "from", "to", "force"))
        maxspeed = tags.get("maxspeed") or (device[2].get("maxspeed") if device else None)
        if kind == AVERAGE_SPEED:
            if not start or not end:
                stats["tramo omitido: falta from/to"] += 1
                continue
            base = {"highway": SPEED_CAMERA, "enforcement": AVERAGE_SPEED, "section_id": f"relation/{rid}"}
            if maxspeed:
                base["maxspeed"] = maxspeed
            found[f"relation/{rid}/start"] = (start[0], start[1], {**base, "section": "start"})
            found[f"relation/{rid}/end"] = (end[0], end[1], {**base, "section": "end"})
            stats["tramo"] += 1
        else:
            at = device or start
            if not at:
                stats["semáforo omitido: falta device/from"] += 1
                continue
            out = {"highway": SPEED_CAMERA, "enforcement": "traffic_signals"}
            origin = start or at
            target = next((p for p in (end, force, device) if p and (p[0], p[1]) != (origin[0], origin[1])), None)
            if target:
                out["direction"] = str(bearing_deg(origin[:2], target[:2]))
            if maxspeed:
                out["maxspeed"] = maxspeed
            found[f"relation/{rid}"] = (at[0], at[1], out)
            stats["semáforo"] += 1
    return found


# ---- Teselas (misma fórmula que PoiTiles en la app) ----

def tile_x(lon):
    return min(max(int(math.floor((lon + 180.0) / 360.0 * N)), 0), N - 1)


def tile_y(lat):
    rad = math.radians(max(-MAX_LAT, min(MAX_LAT, lat)))
    merc = math.log(math.tan(rad) + 1.0 / math.cos(rad))
    return min(max(int(math.floor((1.0 - merc / math.pi) / 2.0 * N)), 0), N - 1)


def lon_at(x):
    return x / N * 360.0 - 180.0


def lat_at(y):
    return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / N))))


def parse_poly(path):
    """Formato .poly de Geofabrik: devuelve (anillos exteriores, agujeros); anillo = lista de (lon, lat)."""
    outer, holes = [], []
    ring, is_hole = None, False
    with open(path, encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    for ln in lines[1:]:  # la primera línea es el nombre
        if ln.upper() == "END":
            if ring is not None:
                (holes if is_hole else outer).append(ring)
                ring = None
            continue
        if ring is None:
            is_hole = ln.startswith("!")
            ring = []
            continue
        lon, lat = ln.split()[:2]
        ring.append((float(lon), float(lat)))
    return outer, holes


def crossings(rings, lat):
    """Longitudes donde la recta horizontal a `lat` corta los anillos, ordenadas."""
    xs = []
    for ring in rings:
        for i in range(len(ring)):
            (x1, y1), (x2, y2) = ring[i], ring[(i + 1) % len(ring)]
            if (y1 <= lat < y2) or (y2 <= lat < y1):
                xs.append(x1 + (lat - y1) * (x2 - x1) / (y2 - y1))
    xs.sort()
    return xs


def inside(xs, lon):
    """Trazado de rayo: dentro si el número de cortes a la izquierda es impar."""
    return bisect.bisect_left(xs, lon) % 2 == 1


def tiles_for_poly(outer, holes):
    """Teselas z10 cuyo centro cae dentro de algún anillo exterior y fuera de los agujeros."""
    pts = [p for ring in outer for p in ring]
    if not pts:
        return set()
    y0 = tile_y(max(p[1] for p in pts))
    y1 = tile_y(min(p[1] for p in pts))
    x0 = tile_x(min(p[0] for p in pts))
    x1 = tile_x(max(p[0] for p in pts))
    result = set()
    for y in range(y0, y1 + 1):
        lat = (lat_at(y) + lat_at(y + 1)) / 2
        outer_xs = crossings(outer, lat)
        if not outer_xs:
            continue
        hole_xs = crossings(holes, lat)
        for x in range(x0, x1 + 1):
            lon = (lon_at(x) + lon_at(x + 1)) / 2
            if inside(outer_xs, lon) and not inside(hole_xs, lon):
                result.add((x, y))
    return result


def sort_key(poi_id):
    kind, num, *rest = poi_id.split("/")
    return kind, int(num), rest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist")
    ap.add_argument("--name", default="iberia", help="sufijo de los ficheros de salida")
    ap.add_argument("--locations", default="flex_mem",
                    help="índice de nodos de osmium: flex_mem o sparse_file_array,<ruta>")
    ap.add_argument("--region", action="append", required=True, metavar="NOMBRE:PBF:POLY")
    args = ap.parse_args()
    global LOCATIONS
    LOCATIONS = args.locations

    t0 = time.time()
    pois = {}
    tiles = set()
    names = []
    for spec in args.region:
        m = REGION_RE.match(spec)
        if not m:
            sys.exit(f"--region inválida: {spec}")
        name, pbf, poly = m.groups()
        t = time.time()
        found = read_region(pbf)
        enf_stats = collections.Counter()
        found.update(read_enforcement(pbf, enf_stats))
        if enf_stats:
            print(f"[{name}] radares por relación: {dict(enf_stats)}")
        before = len(pois)
        for pid, value in found.items():
            pois.setdefault(pid, value)
        region_tiles = tiles_for_poly(*parse_poly(poly))
        tiles |= region_tiles
        names.append(name)
        print(f"[{name}] {len(found)} objetos ({len(pois) - before} nuevos), "
              f"{len(region_tiles)} teselas, {time.time() - t:.1f} s")

    counts = collections.Counter()
    entries = []
    for pid in sorted(pois, key=sort_key):
        lat, lon, tags = pois[pid]
        for key in ("tourism", "amenity", "highway", "sanitary_dump_station"):
            if key in tags:
                counts[f"{key}={tags[key]}"] += 1
        entries.append([pid, round(lat, 6), round(lon, 6), clean_tags(tags)])

    generated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sorted_tiles = [f"{ZOOM}/{x}/{y}" for x, y in sorted(tiles)]
    os.makedirs(args.out, exist_ok=True)
    gz_path = os.path.join(args.out, f"pois-{args.name}.json.gz")

    def dump(o):
        return json.dumps(o, ensure_ascii=False, separators=(",", ":"))

    with gzip.open(gz_path, "wt", encoding="utf-8", compresslevel=9) as f:
        # "tiles" antes que "pois": la app borra las teselas cubiertas antes de insertar
        f.write('{"v":1,"generated":%s,"regions":%s,"tiles":%s,"pois":[' %
                (dump(generated), dump(names), dump(sorted_tiles)))
        f.write(",".join(dump(e) for e in entries))
        f.write("]}")

    with open(gz_path, "rb") as f:
        raw = f.read()
    meta = {
        "v": 1, "generated": generated, "count": len(entries), "tiles": len(sorted_tiles),
        "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "file": f"pois-{args.name}.json.gz",
    }
    with open(os.path.join(args.out, f"pois-{args.name}.meta.json"), "w", encoding="utf-8") as f:
        f.write(dump(meta))

    print("Por categoría:")
    for key, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {key}: {n}")
    print(f"Total: {len(entries)} POIs, {len(sorted_tiles)} teselas, {len(raw) / 1e6:.2f} MB gz, "
          f"{time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
