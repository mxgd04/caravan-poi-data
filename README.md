# caravan-poi-data

Dataset semanal de puntos de interés para caravanas y autocaravanas (áreas, campings, vaciados, agua, aseos, áreas de descanso, gasolineras y parkings) de España, Portugal y Andorra. Lo consume la app [CaravanNavigator](https://github.com/mxgd04/CaravanNavigator) para no depender de los servidores públicos de Overpass.

## Licencia

Datos © colaboradores de OpenStreetMap, disponibles bajo la ODbL 1.0 — https://www.openstreetmap.org/copyright

## Qué contiene

Una GitHub Action (`.github/workflows/build.yml`) se ejecuta cada lunes, descarga los extractos de Geofabrik (España, Portugal y Andorra), filtra los objetos con la misma selección que usa la app en Overpass y publica dos ficheros en la release `latest`:

- `pois-iberia.json.gz`: dataset comprimido.
- `pois-iberia.meta.json`: `{"v","generated","count","tiles","bytes","sha256","file"}`; la app lo consulta primero para saber si hay una versión nueva y para verificar la descarga.

URLs fijas:

- https://github.com/mxgd04/caravan-poi-data/releases/download/latest/pois-iberia.json.gz
- https://github.com/mxgd04/caravan-poi-data/releases/download/latest/pois-iberia.meta.json

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

`build_restrictions.py` (mismos argumentos) genera `dist/restrictions-iberia.json.gz` y `restrictions-iberia.meta.json`: restricciones que ORS no aplica (alto/ancho/peso/largo en nodos, vías físicamente estrechas), como `[id, lat, lon, rumbo, tipo, valor]` con tipo H/W/N/T/L.

`build_lanes.py` (mismos argumentos) genera `dist/lanes-iberia.json.gz` y `lanes-iberia.meta.json`: carriles (`turn:lanes`, tipo `L`, en el final de la vía) y carteles (`destination`, `destination:ref`, salida; tipo `S`, en el inicio de la vía), como `[id, lat, lon, rumbo, tipo, ...]`.

`build_speeds.py` (mismos argumentos) genera `dist/speeds-iberia.json.gz` y `speeds-iberia.meta.json`: límites de velocidad (`maxspeed`, incluidos los códigos implícitos `ES:urban`, `ES:rural`...) como muestras `[lat, lon, rumbo, km/h, flags, largo]` cada 200 m en el sentido de circulación. `flags`: bit 0 = vale en los dos sentidos, bit 1 = estimado (autopistas, vías living_street y, en España, troncales de sentido único sin dato en OSM). Los atributos con `mph` o sin valor numérico se ignoran.

## Mapa sin conexión

Un segundo workflow (`.github/workflows/maps.yml`, "Construir mapa sin conexión") se ejecuta el día 1 de cada mes a las 04:00 UTC (también a mano desde Actions). Descarga los extractos de Geofabrik de España, Portugal y Andorra, los une con osmium y genera con [Planetiler](https://github.com/onthegomap/planetiler) (v0.10.2, fijada) un único `iberia.pmtiles` con el esquema OpenMapTiles hasta el zoom 14, el mismo que usan los estilos de OpenFreeMap. Tarda entre 1 y 2 horas.

Se publica en la release `maps-latest`:

- `iberia.pmtiles.part00`, `part01`, ...: el mapa partido en trozos de 1,9 GB (límite de GitHub: 2 GB por fichero). La app los descarga en orden y los concatena.
- `map-assets.zip`: estilos (`styles/<n>.json`, con los marcadores `{{PMTILES}}` y `{{ASSETS}}`), sprites y tipografías necesarios para dibujar el mapa sin conexión. Lo genera `build_map_assets.py` a partir de los estilos liberty, bright, positron y dark de OpenFreeMap.
- `maps-meta.json`: `{"v","generated","maxzoom","bounds":[w,s,e,n],"totalBytes","sha256","parts":[{"file","bytes","sha256"}],"assets":{"file","bytes","sha256"}}`. Se sube el último, así la app nunca ve un meta que apunte a partes inexistentes. Las partes que ya no se generan se borran de la release.

URLs fijas: `https://github.com/mxgd04/caravan-poi-data/releases/download/maps-latest/<fichero>`.

En local (Python 3.11, Java 21 y osmium-tool):

```
python build_map_assets.py --out dist
java -Xmx12g -jar planetiler.jar --osm-path=in/iberia.osm.pbf --output=dist/iberia.pmtiles --download --force --maxzoom=14
split -b 1900M -d -a 2 dist/iberia.pmtiles dist/iberia.pmtiles.part
python build_maps_meta.py --dist dist --bbox "w,s,e,n"
```

### Licencia del mapa

Mapa © OpenMapTiles © colaboradores de OpenStreetMap (ODbL) — https://openmaptiles.org/ y https://www.openstreetmap.org/copyright
