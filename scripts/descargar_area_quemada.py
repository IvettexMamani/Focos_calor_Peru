"""Descarga de Google Earth Engine el área quemada del Perú (MODIS MCD64A1, mensual, ~463 m).

Guarda un GeoTIFF por año en data/raw/rasters/area_quemada_{anio}.tif con el mes en que se
quemó cada píxel por primera vez en el año (0 = no se quemó). Va en la proyección sinusoidal
de MODIS, que es de igual área: cada píxel vale lo mismo (~21.5 ha).

Uso:
    uv run python scripts/descargar_area_quemada.py --proyecto ID-DE-TU-PROYECTO
    uv run python scripts/descargar_area_quemada.py --proyecto ID-DE-TU-PROYECTO --anios 2021 2025 2026

La primera vez abre el navegador para iniciar sesión en Earth Engine. MCD64A1 se publica con
2 a 3 meses de retraso, así que el año en curso sale incompleto. Los años que ya existen no se
vuelven a bajar: borra el del año en curso para actualizarlo.
"""
import argparse
import math
from pathlib import Path

import truststore

truststore.inject_into_ssl()    # en esta PC algo intercepta HTTPS; así Python usa los certificados de Windows

import ee
import geopandas as gpd
import rasterio
from rasterio.io import MemoryFile

RAIZ = Path(__file__).resolve().parent.parent
RAW = RAIZ / "data" / "raw"

COLECCION = "MODIS/061/MCD64A1"
# Malla nativa de MODIS. Bajar alineado a ella evita remuestrear (y duplicar o perder) píxeles.
SINUSOIDAL = "+proj=sinu +lon_0=0 +x_0=0 +y_0=0 +R=6371007.181 +units=m +no_defs"
PIXEL = 463.3127165279165
ORIGEN_X, ORIGEN_Y = -20015109.354, 10007554.677


def malla_peru() -> dict:
    """Malla para computePixels que cubre el Perú, alineada a los píxeles de MODIS."""
    xmin, ymin, xmax, ymax = gpd.read_file(RAW / "distritos" / "DISTRITOS.shp").to_crs(SINUSOIDAL).total_bounds
    col0, col1 = math.floor((xmin - ORIGEN_X) / PIXEL), math.ceil((xmax - ORIGEN_X) / PIXEL)
    fil0, fil1 = math.floor((ORIGEN_Y - ymax) / PIXEL), math.ceil((ORIGEN_Y - ymin) / PIXEL)
    return {
        "crsCode": "SR-ORG:6974",
        "affineTransform": {
            "scaleX": PIXEL, "shearX": 0, "translateX": ORIGEN_X + col0 * PIXEL,
            "shearY": 0, "scaleY": -PIXEL, "translateY": ORIGEN_Y - fil0 * PIXEL,
        },
        "dimensions": {"width": col1 - col0, "height": fil1 - fil0},
    }


def mes_de_quema(anio: int) -> tuple[ee.Image, list[str]]:
    """Imagen con el primer mes del año en que se quemó cada píxel, y los meses publicados."""
    meses = ee.ImageCollection(COLECCION).filterDate(f"{anio}-01-01", f"{anio + 1}-01-01")

    def a_mes(img):
        mes = ee.Date(img.get("system:time_start")).get("month")
        return ee.Image(mes).toUint8().updateMask(img.select("BurnDate").gt(0))

    return meses.map(a_mes).min().unmask(0).toUint8(), meses.aggregate_array("system:index").getInfo()


def bajar_tif(imagen: ee.Image, malla: dict, destino: Path) -> None:
    datos = ee.data.computePixels({"expression": imagen, "fileFormat": "GEO_TIFF", "grid": malla})
    with MemoryFile(datos) as mem, mem.open() as src:
        banda = src.read(1)
        # 0 es "no se quemó", un dato válido: el archivo no declara nodata
        perfil = src.profile | {"crs": SINUSOIDAL, "compress": "deflate", "nodata": None}
    destino.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(destino, "w", **perfil) as dst:
        dst.write(banda, 1)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--proyecto", required=True, help="ID del proyecto de Google Cloud registrado en Earth Engine")
    p.add_argument("--anios", type=int, nargs="+", default=[2025, 2026])
    args = p.parse_args()

    ee.Authenticate(auth_mode="localhost")      # no hace nada si ya hay credenciales guardadas
    ee.Initialize(project=args.proyecto)
    malla = malla_peru()

    print("Área quemada (MODIS MCD64A1 vía Earth Engine)")
    for anio in args.anios:
        destino = RAW / "rasters" / f"area_quemada_{anio}.tif"
        if destino.exists():
            print(f"  ya existe  {destino.relative_to(RAIZ)}")
            continue
        imagen, meses = mes_de_quema(anio)
        if not meses:
            print(f"  {anio}: todavía no hay meses publicados")
            continue
        bajar_tif(imagen, malla, destino)
        print(f"  descargado {destino.relative_to(RAIZ)}  (meses {meses[0]} a {meses[-1]})")


if __name__ == "__main__":
    main()
