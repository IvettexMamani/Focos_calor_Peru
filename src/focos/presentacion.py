"""Presentación HTML de un solo archivo, con las figuras incrustadas: se abre en cualquier navegador."""
import base64
import html
import io
import re
from pathlib import Path


def armar_presentacion(titulo: str, diapositivas: list[dict], ruta) -> Path:
    """Escribe la presentación en `ruta` y devuelve la ruta.

    Cada diapositiva es un dict con `titulo` y, opcionales: `paso` (rótulo chico arriba),
    `subtitulo`, `puntos` (frases; admiten **negrita** y `código`), `cifras` (pares
    (valor, etiqueta)), `figura` (una figura de matplotlib), `fuente` y `portada=True`.
    """
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    cuerpo = "\n".join(_diapositiva(d) for d in diapositivas)
    ruta.write_text(
        _PLANTILLA.replace("{{titulo}}", html.escape(titulo)).replace("{{diapositivas}}", cuerpo),
        encoding="utf-8",
    )
    return ruta


def _png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight", facecolor="white")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _texto(s: str) -> str:
    s = html.escape(str(s))
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", s)


def _diapositiva(d: dict) -> str:
    partes = []
    if d.get("paso"):
        partes.append(f'<p class="paso">{_texto(d["paso"])}</p>')
    etiqueta = "h1" if d.get("portada") else "h2"
    partes.append(f'<{etiqueta}>{_texto(d["titulo"])}</{etiqueta}>')
    if d.get("subtitulo"):
        partes.append(f'<p class="subtitulo">{_texto(d["subtitulo"])}</p>')
    if d.get("puntos"):
        partes.append("<ul>" + "".join(f"<li>{_texto(p)}</li>" for p in d["puntos"]) + "</ul>")
    if d.get("cifras"):
        partes.append('<div class="cifras">' + "".join(
            f'<div class="cifra"><span class="valor">{_texto(v)}</span><span class="etiqueta">{_texto(e)}</span></div>'
            for v, e in d["cifras"]
        ) + "</div>")
    texto = f'<div class="texto">{"".join(partes)}</div>'

    figura = ""
    if d.get("figura") is not None:
        figura = f'<figure><img src="{_png(d["figura"])}" alt="{html.escape(d["titulo"])}"></figure>'
    fuente = f'<p class="fuente">{_texto(d["fuente"])}</p>' if d.get("fuente") else ""
    clase = "slide portada" if d.get("portada") else ("slide con-figura" if figura else "slide")
    return f'<section class="{clase}" hidden><div class="contenido">{texto}{figura}</div>{fuente}</section>'


_PLANTILLA = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{titulo}}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@500&family=Libre+Franklin:wght@600;700&family=Source+Sans+3:wght@400;600&display=swap">
<style>
/* Diapositivas de 64em x 36em que escalan con la ventana; las figuras son PNG de fondo blanco. */
:root {
  --papel: #f3f4f3;
  --hoja: #ffffff;
  --tinta: #1d2125;
  --tenue: #5b666e;
  --linea: #d3d8db;
  --brasa: #c1663f;
  --titulo: "Libre Franklin", "Segoe UI", Arial, sans-serif;
  --cuerpo: "Source Sans 3", "Segoe UI", Arial, sans-serif;
  --dato: "IBM Plex Mono", Consolas, monospace;
  color-scheme: light;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; background: var(--tinta); }
body { display: grid; place-items: center; overflow: hidden; font-family: var(--cuerpo); color: var(--tinta); }
.deck { font-size: min(100vw / 64, 100vh / 36); width: 64em; height: 36em; position: relative; }
.slide { position: absolute; inset: 0; background: var(--papel); padding: 2.4em 3em 2em; display: flex; flex-direction: column; }
.slide[hidden] { display: none; }
.contenido { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 1em; max-width: 44em; }
.con-figura .contenido { display: grid; grid-template-columns: minmax(0, 0.8fr) minmax(0, 1.45fr); gap: 2em; max-width: none; align-items: center; }
.texto { display: flex; flex-direction: column; gap: 0.9em; min-width: 0; }
.paso { margin: 0; font: 500 0.62em/1 var(--dato); letter-spacing: 0.12em; text-transform: uppercase; color: var(--brasa); }
h1, h2 { margin: 0; font-family: var(--titulo); font-weight: 700; line-height: 1.08; text-wrap: balance; letter-spacing: -0.01em; }
h2 { font-size: 1.95em; }
h1 { font-size: 3.4em; max-width: 14em; }
.subtitulo { margin: 0; font-size: 1.15em; line-height: 1.35; color: var(--tenue); max-width: 34em; }
ul { margin: 0; padding-left: 1.1em; display: flex; flex-direction: column; gap: 0.55em; font-size: 0.98em; line-height: 1.38; }
li::marker { color: var(--brasa); }
strong { font-weight: 600; }
code { font: 500 0.88em var(--dato); background: var(--hoja); border: 1px solid var(--linea); border-radius: 0.2em; padding: 0 0.25em; }
.cifras { display: flex; flex-wrap: wrap; gap: 0.6em 1.8em; padding-top: 0.6em; border-top: 1px solid var(--linea); }
.cifra { display: flex; flex-direction: column; gap: 0.15em; min-width: 7em; }
.valor { font: 600 1.9em/1.05 var(--titulo); font-variant-numeric: tabular-nums; }
.etiqueta { font-size: 0.72em; line-height: 1.3; color: var(--tenue); max-width: 13em; }
figure { margin: 0; height: 100%; min-height: 0; background: var(--hoja); border: 1px solid var(--linea); border-radius: 0.3em; padding: 0.8em; display: flex; align-items: center; justify-content: center; }
figure img { max-width: 100%; max-height: 100%; object-fit: contain; display: block; }
.fuente { margin: 0.9em 0 0; font-size: 0.6em; color: var(--tenue); padding-right: 6em; }
.portada { justify-content: center; }
.portada .contenido { flex: 0; gap: 1.2em; }
.portada h1::after { content: ""; display: block; width: 2.2em; height: 0.08em; margin-top: 0.35em; background: var(--brasa); }
.contador { position: absolute; right: 3em; bottom: 1.6em; font: 500 0.6em var(--dato); color: var(--tenue); z-index: 2; }
.avance { position: absolute; left: 0; bottom: 0; height: 0.18em; background: var(--brasa); z-index: 2; transition: width 0.2s; }
@media (prefers-reduced-motion: reduce) { .avance { transition: none; } }
@media print {
  @page { size: 1280px 720px; margin: 0; }
  html, body { height: auto; overflow: visible; background: var(--papel); display: block; }
  .deck { font-size: 20px; height: auto; }
  .slide, .slide[hidden] { position: relative; display: flex; height: 36em; break-after: page; }
  .contador, .avance { display: none; }
}
</style>
</head>
<body>
<main class="deck" aria-live="polite">
{{diapositivas}}
<span class="contador"></span>
<span class="avance"></span>
</main>
<script>
const diapos = [...document.querySelectorAll('.slide')];
const contador = document.querySelector('.contador');
const avance = document.querySelector('.avance');
let actual = 0;
function mostrar(n) {
  actual = Math.max(0, Math.min(n, diapos.length - 1));
  diapos.forEach((d, k) => { d.hidden = k !== actual; });
  contador.textContent = (actual + 1) + ' / ' + diapos.length;
  avance.style.width = ((actual + 1) / diapos.length * 100) + '%';
  history.replaceState(null, '', '#' + (actual + 1));
}
document.addEventListener('keydown', (e) => {
  if (['ArrowRight', 'ArrowDown', 'PageDown', ' ', 'Enter'].includes(e.key)) { e.preventDefault(); mostrar(actual + 1); }
  else if (['ArrowLeft', 'ArrowUp', 'PageUp', 'Backspace'].includes(e.key)) { e.preventDefault(); mostrar(actual - 1); }
  else if (e.key === 'Home') mostrar(0);
  else if (e.key === 'End') mostrar(diapos.length - 1);
});
document.addEventListener('click', (e) => { mostrar(e.clientX > innerWidth / 3 ? actual + 1 : actual - 1); });
mostrar((parseInt(location.hash.slice(1), 10) || 1) - 1);
</script>
</body>
</html>
"""
