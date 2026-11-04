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
