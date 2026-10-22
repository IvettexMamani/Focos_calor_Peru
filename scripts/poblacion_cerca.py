"""Población a 5 km o menos de los incendios activos, con WorldPop 2020 vía Google Earth Engine.

Un incendio es un grupo de focos de calor a 1 km o menos entre sí (en cadena). Cuenta como
incendio activo si suma 3 focos o más en la semana y al menos uno cae en las últimas 48 h del
archivo NRT; un foco suelto puede ser una quema chica o una falsa alarma. Su zona es la unión
de círculos de 5 km alrededor de sus focos de la semana.

Uso:
    uv run python scripts/poblacion_cerca.py --proyecto ID-DE-TU-PROYECTO

Necesita los focos NRT (`descargar_datos.py --nrt 7d`) y los anuales 2021 y 2025, que sirven
para quitar las fuentes fijas. Guarda en data/processed/ una fila por incendio activo
(poblacion_cerca_incendios.csv) y sus zonas de 5 km (zonas_5km_activos.geojson).
"""
import argparse
from pathlib import Path

import truststore

truststore.inject_into_ssl()    # en esta PC algo intercepta HTTPS; así Python usa los certificados de Windows

import ee
import geopandas as gpd
import pandas as pd
from shapely.geometry import mapping

from focos import agrupar_incendios, es_fuente_fija, leer_focos

RAIZ = Path(__file__).resolve().parent.parent
RAW = RAIZ / "data" / "raw"
SALIDA = RAIZ / "data" / "processed"

# 2020, ~100 m, por sexo y grupos de edad; "constrained": solo reparte gente donde hay viviendas
WORLDPOP = "WorldPop/GP/100m/pop_age_sex_cons_unadj"
MAYORES = [f"{s}_{e}" for s in "MF" for e in (65, 70, 75, 80)]
NINOS = [f"{s}_{e}" for s in "MF" for e in (0, 1)]       # bandas 0 (menos de 1 año) y 1 (1 a 4)
RADIO_M = 5000
VENTANA_ACTIVO = pd.Timedelta("48h")
MIN_FOCOS = 3
CRS_METRICO = 24891


def imagen_poblacion() -> tuple[ee.Image, dict]:
    img = (ee.ImageCollection(WORLDPOP).filter(ee.Filter.eq("country", "PER"))
           .filter(ee.Filter.eq("year", 2020)).first())
    capas = ee.Image.cat([
        img.select("population").rename("personas"),
        img.select(MAYORES).reduce(ee.Reducer.sum()).rename("mayores_65"),
        img.select(NINOS).reduce(ee.Reducer.sum()).rename("menores_5"),
    ])
    # sumar en la malla nativa de WorldPop: a otra escala la suma de personas cambia
    return capas, img.select("population").projection().getInfo()


def geometria_ee(g) -> ee.Geometry:
    return ee.Geometry(mapping(g), None, False)


def sumar(img: ee.Image, proj: dict, geometria) -> dict:
    return img.reduceRegion(ee.Reducer.sum(), geometria_ee(geometria), crs=proj["crs"],
                            crsTransform=proj["transform"], maxPixels=1e10).getInfo()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--proyecto", required=True, help="ID del proyecto de Google Cloud registrado en Earth Engine")
    args = p.parse_args()

    nrt = max((RAW / "firms").glob("viirs-snpp_nrt_7d_*_Peru.csv"))
    focos = leer_focos(nrt)
    anuales = [RAW / "firms" / f"viirs-snpp_{a}_Peru.csv" for a in (2021, 2025)]
    focos = focos[~es_fuente_fija(focos, [r for r in anuales if r.exists()]).values].reset_index(drop=True)
    hora = pd.to_timedelta(focos["acq_time"] // 100 * 60 + focos["acq_time"] % 100, unit="min")
    focos["momento"] = focos["acq_date"] + hora
    focos["incendio"] = agrupar_incendios(focos)

    distritos = gpd.read_file(RAW / "distritos" / "DISTRITOS.shp")
    con_lugar = gpd.sjoin(focos, distritos[["DEPARTAMEN", "PROVINCIA", "DISTRITO", "geometry"]], how="left", predicate="within")
    con_lugar = con_lugar[~con_lugar.index.duplicated()]

    limite = focos["momento"].max() - VENTANA_ACTIVO
    resumen = con_lugar.groupby("incendio").agg(
        focos=("momento", "size"),
        focos_48h=("momento", lambda m: int((m >= limite).sum())),
        primer_foco=("momento", "min"),
        ultimo_foco=("momento", "max"),
        frp_max=("frp", "max"),
        # el lugar del incendio es el distrito donde cae la mayoría de sus focos
        distrito=("DISTRITO", lambda s: s.mode().iat[0] if s.notna().any() else None),
        provincia=("PROVINCIA", lambda s: s.mode().iat[0] if s.notna().any() else None),
        departamento=("DEPARTAMEN", lambda s: s.mode().iat[0] if s.notna().any() else None),
    )
    recientes = resumen[resumen["focos_48h"] > 0]
    activos = recientes[recientes["focos"] >= MIN_FOCOS]

    metrico = focos.to_crs(epsg=CRS_METRICO)
    zonas = (gpd.GeoDataFrame({"incendio": focos["incendio"]}, geometry=metrico.buffer(RADIO_M), crs=metrico.crs)
             .dissolve("incendio").to_crs(4326))
    zonas["geometry"] = zonas.simplify(0.0015, preserve_topology=True)      # ~150 m, de sobra para círculos de 5 km
    zonas_activas = zonas.loc[activos.index]

    ee.Authenticate(auth_mode="localhost")
    ee.Initialize(project=args.proyecto)
    img, proj = imagen_poblacion()
    fc = ee.FeatureCollection([ee.Feature(geometria_ee(g), {"incendio": int(i)}) for i, g in zonas_activas.geometry.items()])
    por_incendio = img.reduceRegions(collection=fc, reducer=ee.Reducer.sum(), crs=proj["crs"],
                                     crsTransform=proj["transform"]).getInfo()
    conteo = pd.DataFrame([f["properties"] for f in por_incendio["features"]]).set_index("incendio")
    activos = activos.join(conteo).sort_values("personas", ascending=False)

    # las zonas de incendios vecinos se pisan: el total se cuenta sobre su unión, sin repetir personas
    total_activos = sumar(img, proj, zonas_activas.union_all())
    total_recientes = sumar(img, proj, zonas.loc[recientes.index].union_all())
    total_semana = sumar(img, proj, zonas.union_all())

    SALIDA.mkdir(parents=True, exist_ok=True)
    activos.round(0).to_csv(SALIDA / "poblacion_cerca_incendios.csv")
    zonas_activas.join(activos[["personas", "mayores_65", "menores_5", "focos", "distrito", "departamento"]]).to_file(
        SALIDA / "zonas_5km_activos.geojson", driver="GeoJSON")

    print(f"Focos de calor del {focos['momento'].min():%d/%m %H:%M} al {focos['momento'].max():%d/%m %H:%M} (UTC): {len(focos):,}")
    print(f"Incendios en la semana: {len(resumen):,}   con foco en las últimas 48 h: {len(recientes):,}   "
          f"activos ({MIN_FOCOS} focos o más): {len(activos):,}")
    print(f"A 5 km o menos de un incendio activo: {total_activos['personas']:,.0f} personas, "
          f"{total_activos['mayores_65']:,.0f} de 65 años o más y {total_activos['menores_5']:,.0f} menores de 5")
    print(f"A 5 km o menos de cualquier foco de las últimas 48 h: {total_recientes['personas']:,.0f} personas")
    print(f"A 5 km o menos de algún foco de la semana: {total_semana['personas']:,.0f} personas")
    print(activos.head(10)[["distrito", "departamento", "focos", "focos_48h", "personas", "mayores_65"]].round(0).to_string())


if __name__ == "__main__":
    main()
