"""Lectura y limpieza de los focos de calor de NASA FIRMS (archivos anuales por país y NRT)."""
import geopandas as gpd
import pandas as pd

# Columna `type`: 0 = incendio de vegetación, 1 = volcán activo,
# 2 = otra fuente fija en tierra (industria, mecheros), 3 = costa afuera
INCENDIO_VEGETACION = 0

# MODIS reporta la confianza en % (0-100); por debajo de 30 FIRMS la considera baja
UMBRAL_MODIS = 30


def leer_focos(ruta, solo_vegetacion: bool = True, sin_baja_confianza: bool = True) -> gpd.GeoDataFrame:
    """Lee un CSV de FIRMS y lo devuelve como puntos en EPSG:4326."""
    df = pd.read_csv(ruta, parse_dates=["acq_date"])
    # los archivos NRT no traen `type`: ahí no se pueden separar volcanes ni mecheros
    if solo_vegetacion and "type" in df:
        df = df[df["type"] == INCENDIO_VEGETACION]
    if sin_baja_confianza:
        df = df[~es_baja_confianza(df["confidence"])]
    df = df.reset_index(drop=True)
    return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["longitude"], df["latitude"]), crs="EPSG:4326")


def es_baja_confianza(confianza: pd.Series) -> pd.Series:
    # VIIRS usa letras (l, n, h) en los anuales y palabras (low, nominal, high) en NRT; MODIS, un número
    if pd.api.types.is_numeric_dtype(confianza):
        return confianza < UMBRAL_MODIS
    return confianza.isin(["l", "low"])


def es_fuente_fija(focos: gpd.GeoDataFrame, rutas_anuales, max_distancia_m: float = 1000) -> pd.Series:
    """Marca los focos que caen cerca de un volcán, industria o plataforma conocida.

    Los focos NRT no traen `type`; una fuente fija no se mueve, así que se usan como
    referencia los focos con `type` distinto de 0 de los archivos anuales.
    """
    fijos = pd.concat([pd.read_csv(r) for r in rutas_anuales])
    fijos = fijos[fijos["type"] != INCENDIO_VEGETACION]
    fijos = gpd.GeoDataFrame(geometry=gpd.points_from_xy(fijos["longitude"], fijos["latitude"]), crs="EPSG:4326")
    # distancias en metros: EPSG:24891, el CRS métrico que usa el curso
    cerca = gpd.sjoin_nearest(focos.to_crs(epsg=24891), fijos.to_crs(epsg=24891), max_distance=max_distancia_m, how="left")
    return cerca["index_right"].notna().groupby(level=0).any().reindex(focos.index)
