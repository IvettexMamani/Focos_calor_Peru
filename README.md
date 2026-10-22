# focos-calor-peru

Incendios forestales en el Perú, 2024–2026: focos de calor de NASA FIRMS y área quemada (MODIS MCD64A1)
cruzados con los 1 873 distritos del INEI, más la población cercana a los incendios activos (WorldPop 2020).

Página publicada: https://claude.ai/artifact/QPrUTk5QRi733F78JCNy58

## Contenido

| Ruta | Qué es |
|---|---|
| `notebooks/incendios_area_quemada.ipynb` | El análisis en 4 pasos: preparar el raster, limpiar los focos (fuera volcanes y fuentes fijas), cruzar con distritos y armar la presentación |
| `reports/presentacion_incendios.html` | Presentación de 12 diapositivas que genera el notebook |
| `reports/monitor_incendios_2026.html` | La página publicada: cifras, mapa interactivo, población cercana y políticas públicas |
| `src/focos/` | Funciones del análisis: lectura y limpieza de focos, cruces con rasters y distritos, presentación |
| `scripts/` | Descarga de datos y cálculo de la población cercana |
| `data/processed/` | Población a 5 km de cada incendio activo y sus zonas |

## Cómo reproducirlo

```
uv sync --system-certs
uv run --system-certs python scripts/descargar_datos.py --anios 2021 2025    # focos anuales + distritos + rasters del curso
uv run --system-certs python scripts/descargar_datos.py --nrt 7d             # focos de los últimos 7 días
uv run --system-certs python scripts/descargar_area_quemada.py --proyecto TU-PROYECTO --anios 2024 2025 2026
uv run --system-certs python scripts/poblacion_cerca.py --proyecto TU-PROYECTO
```

`--system-certs` hace falta si algo (antivirus o proxy) intercepta HTTPS. Los dos últimos scripts usan
Google Earth Engine: la primera vez abren el navegador para iniciar sesión, y `TU-PROYECTO` es el ID de un
proyecto de Google Cloud registrado en Earth Engine.

Los datos no se suben al repositorio porque los scripts los vuelven a bajar. La excepción es
`data/raw/firms/viirs-snpp_nrt_7d_2026-10-21_Peru.csv`: FIRMS publica solo los últimos 7 días, así que
los focos del 14 al 21 de octubre de 2026 ya no se pueden descargar de nuevo.
