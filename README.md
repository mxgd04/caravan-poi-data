# caravan-poi-data

Dataset semanal de puntos de interés para caravanas y autocaravanas (áreas, campings, vaciados, agua, aseos, áreas de descanso, gasolineras y parkings) de España, Portugal, Andorra, Francia y Mónaco. Lo consume la app [CaravanNavigator](https://github.com/mxgd04/CaravanNavigator) para no depender de los servidores públicos de Overpass.

## Licencia

Datos © colaboradores de OpenStreetMap, disponibles bajo la ODbL 1.0 — https://www.openstreetmap.org/copyright

## Qué contiene

Una GitHub Action (`.github/workflows/build.yml`) se ejecuta cada lunes, construye dos paquetes regionales en paralelo (matriz `pack`): `iberia` (España, Portugal y Andorra) y `france` (Francia y Mónaco). Descarga los extractos de Geofabrik, filtra los objetos con la misma selección que usa la app en Overpass y publica en la release `latest`, por cada paquete:

- `pois-<paquete>.json.gz`: dataset comprimido.
- `pois-<paquete>.meta.json`: `{"v","generated","count","tiles","bytes","sha256","file"}`; la app lo consulta primero para saber si hay una versión nueva y para verificar la descarga.

`<paquete>` es `iberia` o `france`. Igual para `restrictions-`, `lanes-`, `speeds-` y `places-` (cada uno con su `.json.gz` y su `.meta.json`). En la península los nombres no han cambiado. Regiones en la cabecera de los POIs: `spain`, `portugal`, `andorra` (iberia) y `france`, `monaco` (france).

URLs fijas:

- https://github.com/mxgd04/caravan-poi-data/releases/download/latest/pois-iberia.json.gz
- https://github.com/mxgd04/caravan-poi-data/releases/download/latest/pois-iberia.meta.json
- https://github.com/mxgd04/caravan-poi-data/releases/download/latest/pois-france.json.gz
- https://github.com/mxgd04/caravan-poi-data/releases/download/latest/pois-france.meta.json

## Formato

```json
{"v":1,"generated":"2026-11-02T03:12:45Z","regions":["spain","portugal","andorra"],
 "tiles":["10/501/386", "..."],
 "pois":[["node/123",40.123456,-3.123456,{"tourism":"camp_site","name":"..."}], "..."]}
```

- `tiles`: teselas slippy z10 cuyo centro está dentro de algún polígono (`.poly`) de las regiones; son las que la app da por cubiertas y ya no pide a Overpass. Va antes de `pois`.
- `pois`: `[id OSM, lat, lon, tags]`, con coordenadas a 6 decimales. Nodos con su coordenada; vías y relaciones con la media de las posiciones de sus nodos. Solo se conservan las etiquetas que lee la app (name, tourism, amenity, highway, parking, motorhome, caravan, sanitary_dump_station, drinking_water, water_point, power_supply, toilets, shower, fee, charge, website, phone, opening_hours, maxstay, capacity, access, etc.); en teléfono y web solo el primer valor separado por `;`.
- Selección: `tourism` = caravan_site | camp_site; `amenity` = sanitary_dump_station | drinking_water | water_point | toilets | fuel; `sanitary_dump_station=yes`; `highway` = rest_area | services; `amenity=parking` con `motorhome` o `caravan` = yes | designated.
- Radares: nodos `highway=speed_camera` y relaciones `type=enforcement`. Con `enforcement=traffic_signals` o `red_light_camera` se emite un radar de semáforo `relation/<id>` en el nodo `device` (`direction` = rumbo de `from` a `to`/`force`). Con `enforcement=average_speed` se emiten dos puntos, `relation/<id>/start` (nodo `from`) y `relation/<id>/end` (nodo `to`), con `section`, `section_id` y `maxspeed`. Solo se usan miembros nodo; se omiten las relaciones sin los nodos necesarios. El formato y la versión no cambian: una app antigua los trata como radares normales.

## Ejecutarlo a mano

En GitHub: pestaña Actions, workflow "Construir dataset de POIs", botón Run workflow.

En local (Python 3.11 o superior):

```
pip install -r requirements.txt
mkdir in
# descarga los .osm.pbf y .poly de https://download.geofabrik.de/europe/ a in/
python build_dataset.py --out dist \
  --region spain:in/spain.osm.pbf:in/spain.poly \
  --region portugal:in/portugal.osm.pbf:in/portugal.poly \
  --region andorra:in/andorra.osm.pbf:in/andorra.poly
```

Genera `dist/pois-iberia.json.gz` y `dist/pois-iberia.meta.json`.

Todos los scripts admiten `--name <paquete>` (por defecto `iberia`; sustituye `iberia` en los nombres de salida y en el campo `file` del meta) y `--locations` (índice de nodos de osmium; por defecto `flex_mem`). Para Francia, que no cabe en 16 GB con `flex_mem`, el workflow usa un índice en disco: `--name france --locations sparse_file_array,<ruta>/nodes.idx` (el script borra el fichero antes de cada pasada).

`build_restrictions.py` (mismos argumentos) genera `dist/restrictions-iberia.json.gz` y `restrictions-iberia.meta.json`: restricciones que ORS no aplica (alto/ancho/peso/largo en nodos, vías físicamente estrechas), como `[id, lat, lon, rumbo, tipo, valor]` con tipo H/W/N/T/L.

`build_lanes.py` (mismos argumentos) genera `dist/lanes-iberia.json.gz` y `lanes-iberia.meta.json`: carriles (`turn:lanes`, tipo `L`, en el final de la vía) y carteles (`destination`, `destination:ref`, salida; tipo `S`, en el inicio de la vía), como `[id, lat, lon, rumbo, tipo, ...]`.

`build_speeds.py` (mismos argumentos) genera `dist/speeds-iberia.json.gz` y `speeds-iberia.meta.json`: límites de velocidad (`maxspeed`, incluidos los códigos implícitos `ES:urban`, `ES:rural`...) como muestras `[lat, lon, rumbo, km/h, flags, largo]` cada 200 m en el sentido de circulación. `flags`: bit 0 = vale en los dos sentidos, bit 1 = estimado (autopistas, vías living_street y, en España, troncales de sentido único sin dato en OSM). Los atributos con `mph` o sin valor numérico se ignoran.

`build_places.py` (mismos argumentos; `--locations` se ignora porque solo lee nodos) genera `dist/places-iberia.json.gz` y `places-iberia.meta.json`: lugares para la búsqueda sin conexión de la app. Nodos `place` = city | town | village | suburb | hamlet | quarter | neighbourhood con `name`, dentro de la caja del `.poly` de su región, como `{"n","a","t","p","y","x","c"}`: nombre, hasta 4 nombres alternativos (`name:es/fr/pt/ca/eu/gl`, `alt_name`, `official_name`), tipo OSM, población (0 si falta), lat y lon a 5 decimales y contexto (la city/town más cercana a 25 km como máximo; vacío en city/town). Cabecera `{"v":1,"generated":...,"places":[...]}`.

## Mapa sin conexión

Un segundo workflow (`.github/workflows/maps.yml`, "Construir mapa sin conexión") se ejecuta el día 1 de cada mes a las 04:00 UTC (también a mano desde Actions). Un primer job (`assets`) construye y publica `map-assets.zip`; después un job en matriz (`region`, en paralelo) descarga los extractos de Geofabrik, los une con osmium y genera con [Planetiler](https://github.com/onthegomap/planetiler) (v0.10.2, fijada) un `.pmtiles` por región con el esquema OpenMapTiles hasta el zoom 14, el mismo que usan los estilos de OpenFreeMap:

- `iberia`: España, Portugal y Andorra (1 a 2 horas).
- `france`: Francia y Mónaco (varias horas; usa `/mnt` como directorio de trabajo si tiene más espacio libre).

Se publica en la release `maps-latest`:

- `iberia.pmtiles.part00`, `part01`, ... y `france.pmtiles.part00`, ...: cada mapa partido en trozos de 1,9 GB (límite de GitHub: 2 GB por fichero). La app los descarga en orden y los concatena.
- `map-assets.zip`: compartido por todas las regiones. Estilos (`styles/<n>.json`, con los marcadores `{{PMTILES}}` y `{{ASSETS}}`), sprites y tipografías necesarios para dibujar el mapa sin conexión. Lo genera `build_map_assets.py` a partir de los estilos liberty, bright, positron y dark de OpenFreeMap.
- `maps-meta-iberia.json` y `maps-meta-france.json`: `{"v","generated","maxzoom","bounds":[w,s,e,n],"totalBytes","sha256","parts":[{"file","bytes","sha256"}],"assets":{"file","bytes","sha256"},"region"}`. Cada meta se sube el último de su región, así la app nunca ve un meta que apunte a partes inexistentes. Las partes que ya no se generan se borran de la release, solo las de la misma región.
- `maps-meta.json`: copia exacta de `maps-meta-iberia.json`, para las apps ya instaladas que solo conocen el mapa de la península.

URLs fijas: `https://github.com/mxgd04/caravan-poi-data/releases/download/maps-latest/<fichero>`.

En local (Python 3.11, Java 21 y osmium-tool), por ejemplo para Francia:

```
python build_map_assets.py --out dist
osmium merge in/france.osm.pbf in/monaco.osm.pbf -o in/france-all.osm.pbf
java -Xmx10g -jar planetiler.jar --osm-path=in/france-all.osm.pbf --output=dist/france.pmtiles --download --force --maxzoom=14
split -b 1900M -d -a 2 dist/france.pmtiles dist/france.pmtiles.part
python build_maps_meta.py --dist dist --name france.pmtiles --region france --bbox="w,s,e,n"
```

Para la península, `--name iberia.pmtiles --region iberia --legacy` (esta última opción escribe además `maps-meta.json`). `--assets` indica otra ruta de `map-assets.zip`.

### Licencia del mapa

Mapa © OpenMapTiles © colaboradores de OpenStreetMap (ODbL) — https://openmaptiles.org/ y https://www.openstreetmap.org/copyright
