"""Descarga los datos del proyecto en data/raw/.

Uso:
    uv run python scripts/descargar_datos.py                       # VIIRS S-NPP 2021 + distritos + rasters
    uv run python scripts/descargar_datos.py --anios 2019 2020 2021
    uv run python scripts/descargar_datos.py --sensor modis --anios 2005
    uv run python scripts/descargar_datos.py --nrt 7d              # últimos 7 días en vez de años completos

Los archivos que ya existen no se vuelven a bajar, salvo los de --nrt, que FIRMS
actualiza cada pocas horas.
"""
import argparse
import shutil
import sys
import urllib.request
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
RAW = RAIZ / "data" / "raw"

# Archivo anual por país, sin API key. Sensores y primer año disponible:
SENSORES = {"viirs-snpp": 2012, "viirs-jpss1": 2018, "modis": 2000}   # jpss1 = NOAA-20
FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/data/country/{sensor}/{anio}/{sensor}_{anio}_Peru.csv"

# El archivo anual tarda meses en publicarse. Para lo reciente FIRMS deja, sin API key,
# archivos en tiempo casi real (NRT) de las últimas 24h/48h/7d, pero por continente.
NRT = {
    "viirs-snpp": ("suomi-npp-viirs-c2", "SUOMI_VIIRS_C2"),
    "viirs-jpss1": ("noaa-20-viirs-c2", "J1_VIIRS_C2"),
    "modis": ("modis-c6.1", "MODIS_C6_1"),
}
NRT_URL = "https://firms.modaps.eosdis.nasa.gov/data/active_fire/{carpeta}/csv/{prefijo}_South_America_{periodo}.csv"

# Dataset del curso en Hugging Face: ruta remota -> carpeta local dentro de data/raw/
HF_URL = "https://huggingface.co/datasets/aquiro1994/ds-python-up/resolve/main/{ruta}"
CURSO = {
    **{f"_data/Folium/DISTRITOS.{ext}": "distritos" for ext in ("shp", "shx", "dbf", "prj", "cpg")},
    "Labs/Python_Notebooks/LAB7/Prec_raster_peru.tif": "rasters",
    "Labs/Python_Notebooks/LAB7/tmax_raster.tif": "rasters",
    "Labs/Python_Notebooks/LAB7/tmin_raster.tif": "rasters",
    "_data/VIIRS_NTL_Peru_YearlyComposite_2021.tif": "rasters",
}


def bajar(url: str, destino: Path) -> None:
    if destino.exists():
        print(f"  ya existe  {destino.relative_to(RAIZ)}")
        return
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_suffix(destino.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as resp, open(temporal, "wb") as f:
        shutil.copyfileobj(resp, f)
    temporal.rename(destino)        # así una descarga cortada no queda como archivo "bueno"
    print(f"  descargado {destino.relative_to(RAIZ)}  ({destino.stat().st_size / 1e6:.1f} MB)")


def bajar_nrt(sensor: str, periodo: str, destino: Path) -> None:
    """Baja el archivo NRT de Sudamérica y guarda solo los focos que caen en algún distrito."""
    # solo hacen falta aquí; el resto del script usa la librería estándar
    import geopandas as gpd
    import pandas as pd

    carpeta, prefijo = NRT[sensor]
    # dtype=str para escribir las filas tal cual vienen (p. ej. acq_time "0328", no 328)
    df = pd.read_csv(NRT_URL.format(carpeta=carpeta, prefijo=prefijo, periodo=periodo), dtype=str)
    puntos = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df["longitude"].astype(float), df["latitude"].astype(float)), crs="EPSG:4326"
    )
    distritos = gpd.read_file(RAW / "distritos" / "DISTRITOS.shp")[["geometry"]].to_crs(puntos.crs)
    # un recuadro alrededor del Perú se llevaría también Acre, Pando y el sur de Colombia
    en_peru = gpd.sjoin(puntos, distritos, predicate="within").index
    peru = df[df.index.isin(en_peru)]

    destino.parent.mkdir(parents=True, exist_ok=True)
    peru.to_csv(destino, index=False)
    fechas = f"{peru['acq_date'].min()} a {peru['acq_date'].max()}" if len(peru) else "sin focos"
    print(f"  descargado {destino.relative_to(RAIZ)}  ({len(peru)} focos de {len(df)} en Sudamérica; {fechas})")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sensor", choices=SENSORES, default="viirs-snpp")
    p.add_argument("--anios", type=int, nargs="+", default=[2021])
    p.add_argument("--nrt", choices=["24h", "48h", "7d"], help="últimas horas/días en vez de años completos")
    args = p.parse_args()

    primero = SENSORES[args.sensor]
    if not args.nrt and min(args.anios) < primero:
        sys.exit(f"{args.sensor} empieza en {primero}")

    # primero los distritos: --nrt los usa para recortar Sudamérica al Perú
    print("Distritos y rasters (dataset del curso)")
    for ruta, carpeta in CURSO.items():
        bajar(HF_URL.format(ruta=ruta), RAW / carpeta / Path(ruta).name)

    print("Focos de calor (NASA FIRMS)")
    if args.nrt:
        nombre = f"{args.sensor}_nrt_{args.nrt}_{date.today():%Y-%m-%d}_Peru.csv"
        bajar_nrt(args.sensor, args.nrt, RAW / "firms" / nombre)
        return
    for anio in args.anios:
        nombre = f"{args.sensor}_{anio}_Peru.csv"
        bajar(FIRMS_URL.format(sensor=args.sensor, anio=anio), RAW / "firms" / nombre)


if __name__ == "__main__":
    main()
