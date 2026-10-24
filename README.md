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
| `reports/reel/` | Reel vertical de 59 s: guion, `guion.json` con citas del informe, lista de verificación, video (`reel.mp4`) y posts con enlaces (`posts.md`) |
| `scripts/reel_*.py` | El reel en tres pasos: guion con Qwen2.5-1.5B, voz con Qwen3-TTS y video con subtítulos de Whisper |

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

El reel sale de `reports/reel/guion.md` y de una grabación de voz en `data/` (cada script lleva sus
dependencias y `uv run` les arma un entorno aparte):

```
uv run --system-certs scripts/reel_guion.py     # guion.json y verificacion.md (Qwen2.5-1.5B local)
uv run --system-certs scripts/reel_voz.py       # reel.wav con la voz clonada (Qwen3-TTS 0.6B, ~10 min en CPU)
uv run --system-certs scripts/reel_video.py     # reel.mp4, reel.srt y descripcion.txt
```

Las cifras de `guion.json` llevan `"verificado": false` hasta revisarlas a mano con `verificacion.md`.
El video está en `reports/reel/reel.mp4` y los textos para publicarlo, en `reports/reel/posts.md`.

## Correcciones al guion

El guion (`reports/reel/guion.md`) sale del informe. `guion.json` lo armó Qwen2.5-1.5B en local: Qwen3.8
no corre en esta PC (el modelo abierto más chico tiene 27B). Esto es lo que hubo que corregir:

- **Locución:** en las 7 escenas Qwen no devolvió la frase del guion lista para leer. En cinco devolvió
  solo la cifra («41 000 hectáreas», «48 %», «61 481», «13 años», «95 %»), y en las otras dos dejó los
  números en cifras. Se reemplazó por el texto del guion con los números en letras, para que la voz los lea bien.
- **Pantalla:** se corrigieron 6 de 7 tarjetas. Había cifras sin unidad (41 000 sin «ha», 48 y 95 sin «%»,
  13 sin «años») y etiquetas que no dicen qué miden («personas», «porcentaje», «%», «años»). En la escena 1
  ponía «41 000 ha», un dato que esa escena no menciona, en vez de «×2».
- **Fuentes:** Qwen atribuyó mal varias cifras. Dio las 41 000 ha al Serfor (vía Actualidad Ambiental, 2022)
  cuando son de MODIS, el 48 % de los focos a Infobae cuando es de VIIRS (NASA FIRMS) y los 13 años a una nota
  de 2022 en vez del D. S. 009-2013-MINAGRI. También eligió citas del informe que no contienen la cifra.
  Quedan marcadas con ⚠ en `verificacion.md`; el pie del video usa la fuente que da el informe.
- **Duración:** con la voz clonada, el primer audio duró 77 s. Se acortaron cuatro frases sin quitar cifras:
  «septiembre y octubre» pasó a «los meses más fuertes», los departamentos de la escena 4 pasaron al mapa,
  y se sacaron «Mientras tanto» y «Datos y fuentes, en la descripción». Además, la voz se aceleró un 15 %
  sin cambiar el tono. El reel quedó en 59 s.
- **Subtítulos:** Whisper escribió «surfor» por «Serfor», partió «41 .000» e inventó un «Menos,» en una
  respiración. Los subtítulos usan los tiempos de Whisper con el texto del guion.
- **Hashtags:** #PrevencionesForestales, que propuso Qwen, se cambió por #PrevencionDeIncendios en `posts.md`.

Falta la revisión manual de cada cifra contra el informe y las noticias, con `verificacion.md`.

Los datos no se suben al repositorio porque los scripts los vuelven a bajar. La excepción es
`data/raw/firms/viirs-snpp_nrt_7d_2026-10-21_Peru.csv`: FIRMS publica solo los últimos 7 días, así que
los focos del 14 al 21 de octubre de 2026 ya no se pueden descargar de nuevo.
