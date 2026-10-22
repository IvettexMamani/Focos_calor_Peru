"""Cruces entre focos de calor (puntos), rasters y distritos (polígonos)."""
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterstats import zonal_stats

# Las áreas se calculan en un CRS métrico: en EPSG:4326 la unidad es el grado.
# 24891 (PSAD56 / Peru west zone) es el que usa el curso en raster_aplicado.ipynb.
CRS_AREAS = 24891


def valor_en_focos(focos: gpd.GeoDataFrame, ruta_raster, banda: int = 1) -> np.ndarray:
    """Valor de la celda del raster donde cae cada foco; NaN si cae fuera o en nodata."""
    with rasterio.open(ruta_raster) as src:
        pts = focos.to_crs(src.crs)
        muestras = src.sample(zip(pts.geometry.x, pts.geometry.y), indexes=banda, masked=True)
        return np.array([np.nan if np.ma.is_masked(m) else float(m[0]) for m in muestras])


def focos_por_distrito(focos: gpd.GeoDataFrame, distritos: gpd.GeoDataFrame) -> pd.DataFrame:
    """Cuenta los focos de cada distrito y los normaliza por área (focos cada 100 km²)."""
    unidos = gpd.sjoin(focos.to_crs(distritos.crs), distritos[["geometry"]], predicate="within")
    n_focos = unidos.groupby("index_right").size().reindex(distritos.index, fill_value=0)
    area_km2 = distritos.to_crs(epsg=CRS_AREAS).area / 1e6
    return pd.DataFrame({"n_focos": n_focos, "area_km2": area_km2, "focos_100km2": n_focos / area_km2 * 100})


def media_por_distrito(distritos: gpd.GeoDataFrame, ruta_raster, bandas=(1,)) -> pd.Series:
    """Media del raster dentro de cada distrito; con varias bandas, promedia las medias."""
    with rasterio.open(ruta_raster) as src:
        poligonos = distritos.to_crs(src.crs)
        # los rasters del curso no declaran nodata; -9999 no aparece en ellos y evita el aviso de rasterstats
        nodata = src.nodata if src.nodata is not None else -9999
    medias = [
        [s["mean"] for s in zonal_stats(poligonos, ruta_raster, stats=["mean"], band=b, nodata=nodata)]
        for b in bandas
    ]
    return pd.Series(np.nanmean(np.array(medias, dtype=float), axis=0), index=distritos.index)


def area_quemada_por_distrito(distritos: gpd.GeoDataFrame, ruta_raster) -> pd.DataFrame:
    """Hectáreas quemadas por distrito (filas) y mes (columnas 1 a 12).

    El raster guarda el mes en que se quemó cada píxel (0 = no se quemó) en una proyección
    de igual área, así que todos los píxeles valen las mismas hectáreas.
    """
    with rasterio.open(ruta_raster) as src:
        poligonos = distritos.to_crs(src.crs)
        ha_pixel = abs(src.transform.a * src.transform.e) / 1e4
    conteos = zonal_stats(poligonos, ruta_raster, categorical=True, nodata=0)   # [{mes: n° de píxeles}, ...]
    ha = pd.DataFrame(conteos, index=distritos.index).fillna(0) * ha_pixel
    return ha.reindex(columns=range(1, 13), fill_value=0)


def agrupar_incendios(focos: gpd.GeoDataFrame, distancia_m: float = 1000) -> pd.Series:
    """Número de incendio de cada foco: focos a `distancia_m` o menos (en cadena) son el mismo incendio."""
    metrico = focos.to_crs(epsg=CRS_AREAS)
    # dos focos a menos de distancia_m tienen buffers de distancia_m / 2 que se tocan
    union = metrico.buffer(distancia_m / 2).union_all()
    partes = union.geoms if union.geom_type == "MultiPolygon" else [union]
    manchas = gpd.GeoDataFrame(geometry=list(partes), crs=metrico.crs)
    unidos = gpd.sjoin(metrico[["geometry"]], manchas, predicate="within")
    return unidos["index_right"].groupby(level=0).first().reindex(focos.index)
