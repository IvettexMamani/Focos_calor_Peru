# /// script
# requires-python = "==3.12.*"
# dependencies = [
#   "faster-whisper==1.2.1",
#   "msvc-runtime==14.51.36231",
#   "numpy>=1.26",
#   "pillow>=10.4",
#   "soundfile>=0.12",
#   "soxr>=0.3",
#   "imageio-ffmpeg>=0.5",
# ]
# ///
"""Arma el reel vertical (1080×1920) con la voz de reports/reel/reel.wav y subtítulos de Whisper.

Cada escena muestra lo que guion.json pone en "pantalla" y un gráfico con los datos del informe
(reports/monitor_incendios_2026.html): área quemada por año, focos de la semana día por día, mapa
de departamentos y población cercana. Los subtítulos salen de transcribir reel.wav con Whisper
(faster-whisper large-v3-turbo) con tiempos por palabra; la palabra que suena se resalta. Al final
hay una tarjeta con las noticias de referencia y sus enlaces.

Uso:
    uv run scripts/reel_video.py
    uv run scripts/reel_video.py --cuadros 2 12 30     # solo guarda esos instantes como PNG
    uv run scripts/reel_video.py --retranscribir

Guarda en reports/reel/: reel.mp4, reel.srt, whisper_palabras.json y descripcion.txt (texto
para publicar, con los enlaces completos).
"""
import argparse
import ctypes
import difflib
import html
import json
import math
import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


def cargar_runtime_msvc() -> None:
    """CTranslate2 se compila con MSVC 17.10+ y se cae con un msvcp140.dll anterior a 14.40; el de
    Windows en esta PC es 14.34. Se cargan primero las DLL del paquete msvc-runtime."""
    if sys.platform != "win32":
        return
    for nombre in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        ruta = Path(sys.prefix) / "Scripts" / nombre
        if ruta.exists():
            ctypes.WinDLL(str(ruta))


cargar_runtime_msvc()

import imageio_ffmpeg
import numpy as np
import soundfile as sf
import soxr
from PIL import Image, ImageDraw, ImageFilter, ImageFont

RAIZ = Path(__file__).resolve().parent.parent
INFORME = RAIZ / "reports" / "monitor_incendios_2026.html"
REEL = RAIZ / "reports" / "reel"
MODELO_WHISPER = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
# nombres que Whisper escribe mal sin contexto
VOCABULARIO = "Ucayali, Junín, Huánuco, Puno, Cusco, Arequipa, Serfor, focos de calor, hectáreas, Política Forestal."
MONITOR_URL = "https://claude.ai/artifact/QPrUTk5QRi733F78JCNy58"

W, H, FPS = 1080, 1920, 30
CIERRE_S = 3.5          # la tarjeta de noticias sigue en pantalla después de la voz (reel_voz.MAX_S cuenta con esto)
FUNDIDO_S = 0.3
MARGEN = 80

FONDO = (18, 15, 13)
TEXTO = (246, 241, 236)
GRIS = (163, 155, 148)
TENUE = (92, 84, 78)
FUEGO = (255, 108, 46)
BRASA = (255, 196, 64)
DEP_RELLENO = (36, 31, 28)
DEP_BORDE = (78, 70, 64)
SELVA = ("Ucayali", "Junín", "Huánuco")
SIERRA = ("Puno", "Cusco", "Arequipa")
FUENTES = {"negra": "seguibl.ttf", "bold": "segoeuib.ttf", "semi": "seguisb.ttf", "normal": "segoeui.ttf"}


@lru_cache(None)
def fuente(tipo: str, tam: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / FUENTES[tipo]), tam)


def suave(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def miles(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


def envolver(d: ImageDraw.ImageDraw, texto: str, f: ImageFont.FreeTypeFont, ancho: int) -> list[str]:
    lineas, actual = [], ""
    for palabra in texto.split():
        prueba = f"{actual} {palabra}".strip()
        if actual and d.textlength(prueba, font=f) > ancho:
            lineas.append(actual)
            actual = palabra
        else:
            actual = prueba
    return lineas + [actual] if actual else lineas


def recortar(d: ImageDraw.ImageDraw, texto: str, f: ImageFont.FreeTypeFont, ancho: int) -> str:
    if d.textlength(texto, font=f) <= ancho:
        return texto
    while d.textlength(texto + "…", font=f) > ancho:
        texto = texto[:-1]
    return texto + "…"


def con_alfa(capa: Image.Image, alfa: float) -> Image.Image:
    if alfa >= 1:
        return capa
    capa = capa.copy()
    capa.putalpha(capa.getchannel("A").point(lambda a: int(a * alfa)))
    return capa


def datos_informe() -> dict:
    s = INFORME.read_text(encoding="utf-8")
    D = json.loads(re.search(r"const D = (\{.*?\});\s*\n", s, re.S).group(1))
    # hectáreas de enero a agosto tal como las muestra el informe (la suma de D["mensual"] da 40 999)
    texto = re.sub(r"[   ]", " ", html.unescape(re.sub(r"<[^>]+>", " ", s)))
    D["ha_ene_ago"] = {int(a): float(v.replace(" ", ""))
                       for a, v in re.findall(r"\b(202[4-6])\s+(\d{1,3}(?: \d{3})+)\s+ha\b", texto)[:3]}
    return D


class Mapa:
    """Departamentos y focos en una proyección equirectangular ajustada a la latitud media del Perú."""
    LON0, LON1, LAT0, LAT1 = -81.4, -68.6, 0.1, -18.45

    def __init__(self, D: dict, caja: tuple[int, int, int, int]):
        x0, y0, x1, y1 = caja
        self.k = math.cos(math.radians(9.2))
        ancho, alto = (self.LON1 - self.LON0) * self.k, self.LAT0 - self.LAT1
        self.esc = min((x1 - x0) / ancho, (y1 - y0) / alto)
        self.ox = x0 + ((x1 - x0) - ancho * self.esc) / 2
        self.oy = y0 + ((y1 - y0) - alto * self.esc) / 2
        self.deps = {f["properties"]["n"]: f["geometry"] for f in D["deps"]["features"]}
        self.puntos = D["puntos"]
        self.dias = sorted({p[2] for p in self.puntos})

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        return self.ox + (lon - self.LON0) * self.k * self.esc, self.oy + (self.LAT0 - lat) * self.esc

    def anillos(self, nombre: str):
        g = self.deps[nombre]
        for poligono in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]):
            yield [self.xy(*c) for c in poligono[0]]

    @lru_cache(None)
    def base(self, resaltar: tuple = ()) -> Image.Image:
        capa = Image.new("RGBA", (W, H))
        d = ImageDraw.Draw(capa)
        for n in self.deps:
            for a in self.anillos(n):
                d.polygon(a, fill=DEP_RELLENO, outline=DEP_BORDE, width=2)
        if resaltar:
            tinte = Image.new("RGBA", (W, H))
            dt = ImageDraw.Draw(tinte)
            for n, color in resaltar:
                for a in self.anillos(n):
                    dt.polygon(a, fill=color + (60,))
            for n, color in resaltar:
                for a in self.anillos(n):
                    dt.line(a + a[:1], fill=color + (255,), width=4, joint="curve")
            capa.alpha_composite(tinte)
        return capa

    @lru_cache(None)
    def focos(self, hasta: int = 99) -> Image.Image:
        """Focos acumulados hasta el día `hasta` (0 = 14 oct), con un halo difuso."""
        pts = [self.xy(p[0], p[1]) for p in self.puntos if self.dias.index(p[2]) <= hasta]
        halo = Image.new("RGBA", (W, H))
        dh = ImageDraw.Draw(halo)
        for x, y in pts:
            dh.ellipse((x - 6, y - 6, x + 6, y + 6), fill=FUEGO + (150,))
        halo = halo.filter(ImageFilter.GaussianBlur(5))
        dn = ImageDraw.Draw(halo)
        for x, y in pts:
            dn.ellipse((x - 2.2, y - 2.2, x + 2.2, y + 2.2), fill=BRASA + (255,))
        return halo


class Reel:
    def __init__(self, guion: dict, tiempos: list[dict], palabras: list[dict], dur_voz: float):
        self.D = datos_informe()
        self.guion, self.tiempos, self.palabras = guion, tiempos, palabras
        self.escenas = {e["id"]: e for e in guion["escenas"]}
        self.fin_voz = dur_voz
        self.total = dur_voz + CIERRE_S
        self.grupos = agrupar(palabras)
        self.mapa_chico = Mapa(self.D, (190, 610, 890, 1250))
        self.mapa = Mapa(self.D, (150, 610, 930, 1260))
        self.mapa_grande = Mapa(self.D, (120, 540, 960, 1255))
        self.fondo = self.dibujar_fondo()
        self.visuales = [self.v_gancho, self.v_cuanto, self.v_semana, self.v_donde,
                         self.v_afecta, self.v_politicas, self.v_cierre]
        cierre = self.tiempos[-1]
        self.inicio_tarjeta = self.momento(cierre["inicio"], cierre["fin"] + 1, r"^datos") or self.fin_voz

    # ---------- piezas comunes ----------
    def dibujar_fondo(self) -> Image.Image:
        yy, xx = np.mgrid[0:H, 0:W]
        img = np.array(FONDO, dtype=float) * (1.12 - 0.3 * yy / H)[..., None]       # degradado vertical
        r = np.hypot((xx - W / 2) / W, (yy - H * 0.95) / H)
        img += np.clip(1 - r / 0.55, 0, 1)[..., None] ** 2 * np.array([70, 26, 4])    # brasa abajo
        fondo = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
        d = ImageDraw.Draw(fondo)
        d.rectangle((MARGEN, 118, MARGEN + 8, 152), fill=FUEGO)
        d.text((MARGEN + 26, 135), "INCENDIOS FORESTALES · PERÚ 2026", font=fuente("bold", 34), fill=FUEGO, anchor="lm")
        corte = self.guion.get("corte_datos", "2026-10-21")
        d.text((MARGEN + 26, 186), f"Datos al {int(corte[8:])} de octubre de {corte[:4]}", font=fuente("normal", 30),
               fill=GRIS, anchor="lm")
        return fondo

    def momento(self, desde: float, hasta: float, patron: str) -> float | None:
        """Instante en que la voz dice la primera palabra que calza con `patron` dentro del intervalo."""
        for w in self.palabras:
            if desde - 0.2 <= w["ini"] <= hasta and re.search(patron, w["t"].lower()):
                return w["ini"]
        return None

    def titular(self, d: ImageDraw.ImageDraw, esc: dict, t: float, y: int = 270, tam: int = 210) -> int:
        p = esc.get("pantalla", {})
        cifra = p.get("cifra", "")
        m = re.fullmatch(r"(\D*?)(\d{1,3}(?: \d{3})+|\d+)(.*)", cifra.replace("\u202f", " ").replace("\u00a0", " "))
        if m and float(m.group(2).replace(" ", "")) >= 100:     # las cifras grandes cuentan hacia arriba
            n = float(m.group(2).replace(" ", ""))
            cifra = m.group(1) + miles(n * suave(t / 0.9)) + m.group(3)
        f = fuente("negra", tam)
        while d.textlength(cifra, font=f) > W - 2 * MARGEN and tam > 90:
            tam -= 10
            f = fuente("negra", tam)
        d.text((W / 2, y + tam * 0.55), cifra, font=f, fill=FUEGO, anchor="mm")
        y += int(tam * 1.12)
        for linea in envolver(d, p.get("etiqueta", ""), fuente("bold", 50), W - 2 * MARGEN)[:2]:
            d.text((W / 2, y), linea, font=fuente("bold", 50), fill=TEXTO, anchor="mt")
            y += 62
        for linea in envolver(d, p.get("detalle", ""), fuente("normal", 34), W - 2 * MARGEN)[:2]:
            d.text((W / 2, y + 8), linea, font=fuente("normal", 34), fill=GRIS, anchor="mt")
            y += 44
        return y + 20

    def tarjeta(self, d: ImageDraw.ImageDraw, caja: tuple, grande: str, texto: str, color=FUEGO) -> None:
        x0, y0, x1, y1 = caja
        d.rounded_rectangle(caja, radius=22, fill=(32, 27, 24, 235), outline=(70, 62, 56, 255), width=2)
        d.rectangle((x0, y0 + 22, x0 + 8, y1 - 22), fill=color)
        d.text((x0 + 40, y0 + 26), grande, font=fuente("negra", 76), fill=color, anchor="lt")
        yy = y0 + 122
        for linea in envolver(d, texto, fuente("semi", 34), x1 - x0 - 80)[:3]:
            d.text((x0 + 40, yy), linea, font=fuente("semi", 34), fill=TEXTO, anchor="lt")
            yy += 44

    # ---------- escenas ----------
    def v_gancho(self, capa, d, t, esc, ini, fin):
        capa.alpha_composite(con_alfa(self.mapa_chico.base(), 0.8))
        capa.alpha_composite(con_alfa(self.mapa_chico.focos(), suave((t - 0.3) / 1.2)))
        self.titular(d, esc, t)

    def v_cuanto(self, capa, d, t, esc, ini, fin):
        y = self.titular(d, esc, t, tam=180) + 40
        ha = dict(sorted(self.D["ha_ene_ago"].items()))
        d.text((MARGEN, y), "Área quemada de enero a agosto", font=fuente("semi", 34), fill=GRIS, anchor="lt")
        y += 70
        maximo = max(ha.values())
        for i, (anio, v) in enumerate(ha.items()):
            avance = suave((t - 0.4 - 0.25 * i) / 0.9)
            largo = (W - 2 * MARGEN - 150) * v / maximo * avance
            color = FUEGO if anio == 2026 else TENUE
            d.text((MARGEN, y + 42), str(anio), font=fuente("bold", 44), fill=TEXTO if anio == 2026 else GRIS, anchor="lm")
            d.rounded_rectangle((MARGEN + 140, y, MARGEN + 140 + max(largo, 12), y + 84), radius=10, fill=color)
            etiqueta = f"{miles(v * avance)} ha"
            ancho = d.textlength(etiqueta, font=fuente("bold", 40))
            dentro = largo > ancho + 40
            d.text((MARGEN + 140 + (largo - 20 if dentro else largo + 18), y + 42), etiqueta, font=fuente("bold", 40),
                   fill=FONDO if dentro else TEXTO, anchor="rm" if dentro else "lm")
            y += 120
        d.text((MARGEN, y + 20), "Septiembre y octubre todavía no se publican.", font=fuente("normal", 32), fill=GRIS, anchor="lt")

    def v_semana(self, capa, d, t, esc, ini, fin):
        avance = suave(t / max((fin - ini) * 0.8, 0.1))
        dia = min(int(avance * len(self.mapa.dias)), len(self.mapa.dias) - 1)
        capa.alpha_composite(self.mapa.base())
        capa.alpha_composite(self.mapa.focos(dia))
        self.titular(d, esc, t, tam=170)
        diario = self.D["diario"]
        d.text((W - MARGEN, 600), f"{diario[dia]['dia']}", font=fuente("negra", 56), fill=BRASA, anchor="rt")
        acumulado = sum(x["y2026"] for x in diario[:dia + 1])
        d.text((W - MARGEN, 668), f"{miles(acumulado)} focos", font=fuente("semi", 32), fill=TEXTO, anchor="rt")
        d.text((W - MARGEN, 712), f"2025: {miles(sum(x['y2025'] for x in diario[:dia + 1]))}", font=fuente("normal", 30),
               fill=GRIS, anchor="rt")

    def v_donde(self, capa, d, t, esc, ini, fin):
        resaltar = tuple((n, FUEGO) for n in SELVA) + tuple((n, BRASA) for n in SIERRA)
        capa.alpha_composite(self.mapa_grande.base(resaltar))
        capa.alpha_composite(con_alfa(self.mapa_grande.focos(), 0.85))
        self.titular(d, esc, t, tam=130)
        n_selva = sum(p[5] in SELVA for p in self.D["puntos"])
        ha_sierra = sum(x["ea2026"] for x in self.D["departamentos"] if x["dep"] in SIERRA)
        ha_total = sum(x["ea2026"] for x in self.D["departamentos"])
        sierra_en = self.momento(ini, fin, r"sierra") or ini + (fin - ini) / 2
        leyenda = [(FUEGO, "Selva central", ", ".join(SELVA), f"{round(100 * n_selva / len(self.D['puntos']))} % de los focos", 0),
                   (BRASA, "Sierra sur", ", ".join(SIERRA), f"{round(100 * ha_sierra / ha_total)} % del área quemada",
                    sierra_en - ini)]
        y = 1010           # en el océano, al suroeste de la costa
        for color, nombre, deps, valor, desde in leyenda:
            a = suave((t - desde) / 0.4)
            if a > 0:
                c = tuple(int(v * a + f * (1 - a)) for v, f in zip(color, FONDO))
                d.rounded_rectangle((MARGEN, y, MARGEN + 30, y + 30), radius=6, fill=c)
                d.text((MARGEN + 46, y + 15), nombre, font=fuente("bold", 34), fill=c, anchor="lm")
                d.text((MARGEN + 46, y + 54), deps, font=fuente("normal", 27), fill=GRIS, anchor="lm")
                d.text((MARGEN + 46, y + 88), valor, font=fuente("semi", 27), fill=TEXTO, anchor="lm")
            y += 125

    def v_afecta(self, capa, d, t, esc, ini, fin):
        pob = self.D["pob"]
        y = self.titular(d, esc, t, tam=180) + 20
        mitad = (W - 2 * MARGEN - 30) // 2
        for i, (v, txt) in enumerate([(pob["mayores"], "de 65 años o más"), (pob["menores"], "menores de 5 años")]):
            x0 = MARGEN + i * (mitad + 30)
            a = suave((t - 0.6 - 0.2 * i) / 0.4)
            d.rounded_rectangle((x0, y, x0 + mitad, y + 170), radius=20, fill=(32, 27, 24, int(235 * a)))
            d.text((x0 + 30, y + 26), miles(v), font=fuente("negra", 64), fill=TEXTO + (int(255 * a),), anchor="lt")
            d.text((x0 + 30, y + 112), txt, font=fuente("semi", 32), fill=GRIS + (int(255 * a),), anchor="lt")
        desde = (self.momento(ini, fin, r"fallec|diez") or ini + (fin - ini) / 2) - ini - 0.3
        a = suave((t - desde) / 0.4)
        if a > 0:
            tarjeta = Image.new("RGBA", (W, H))
            # redacción y fuente de la tarjeta «Fallecidos» del informe
            self.tarjeta(ImageDraw.Draw(tarjeta), (MARGEN, y + 210, W - MARGEN, y + 450), "10 fallecidos",
                         "en 2026; ocho eran mayores de 70 años · COEN vía Mongabay, 13/10")
            capa.alpha_composite(con_alfa(tarjeta, a))

    def v_politicas(self, capa, d, t, esc, ini, fin):
        y = self.titular(d, esc, t, tam=180) + 30
        # nombres de las normas como figuran en la tabla de políticas del informe
        tarjetas = [(0, "2013", "Política Nacional Forestal y de Fauna Silvestre (D. S. 009-2013-MINAGRI): vigente, desactualizada"),
                    ((self.momento(ini, fin, r"serfor|plan") or ini + (fin - ini) / 2) - ini - 0.3, "2022",
                     "venció el Plan de Prevención y Reducción de Riesgos de Incendios Forestales 2019–2022 del Serfor")]
        for desde, grande, texto in tarjetas:
            a = suave((t - desde) / 0.4)
            if a > 0:
                capa_t = Image.new("RGBA", (W, H))
                self.tarjeta(ImageDraw.Draw(capa_t), (MARGEN, y, W - MARGEN, y + 270), grande, texto)
                capa.alpha_composite(con_alfa(capa_t, a))
            y += 295

    def v_cierre(self, capa, d, t, esc, ini, fin):
        self.titular(d, esc, t, tam=230)

    def tarjeta_noticias(self, capa, d, t):
        d.text((MARGEN, 260), "Noticias de referencia", font=fuente("negra", 60), fill=TEXTO, anchor="lt")
        y = 370
        for i, n in enumerate(self.guion.get("noticias", [])[:5]):
            a = suave((t - 0.15 * i) / 0.35)
            if a <= 0:
                break
            al = int(255 * a)
            fecha = "/".join(reversed(n["fecha"].split("-")))
            d.text((MARGEN, y), f"{n['medio'].upper()} · {fecha}", font=fuente("bold", 28), fill=FUEGO + (al,), anchor="lt")
            y += 40
            for linea in envolver(d, n["titular"], fuente("semi", 34), W - 2 * MARGEN)[:3]:
                d.text((MARGEN, y), linea, font=fuente("semi", 34), fill=TEXTO + (al,), anchor="lt")
                y += 43
            url = re.sub(r"^https?://(www\.)?", "", n["url"])
            d.text((MARGEN, y + 4), recortar(d, url, fuente("normal", 26), W - 2 * MARGEN), font=fuente("normal", 26),
                   fill=GRIS + (al,), anchor="lt")
            y += 66
        a = suave((t - 1.0) / 0.4)
        if a > 0:
            d.line((MARGEN, y, W - MARGEN, y), fill=TENUE + (int(255 * a),), width=2)
            d.text((MARGEN, y + 24), "Monitor completo, datos y método:", font=fuente("semi", 30), fill=GRIS + (int(255 * a),), anchor="lt")
            d.text((MARGEN, y + 64), MONITOR_URL.removeprefix("https://"), font=fuente("bold", 34), fill=BRASA + (int(255 * a),), anchor="lt")
            d.text((MARGEN, y + 120), "Enlaces completos en la descripción", font=fuente("normal", 30), fill=GRIS + (int(255 * a),), anchor="lt")

    # ---------- subtítulos y pie ----------
    def subtitulo(self, d: ImageDraw.ImageDraw, t: float) -> None:
        for i, g in enumerate(self.grupos):
            siguiente = self.grupos[i + 1][0]["ini"] if i + 1 < len(self.grupos) else g[-1]["fin"] + 0.6
            if g[0]["ini"] - 0.05 <= t < min(g[-1]["fin"] + 0.25, siguiente):
                break
        else:
            return
        f = fuente("negra", 64)
        lineas = [[]]
        for w in g:
            prueba = " ".join(x["t"] for x in lineas[-1] + [w])
            if lineas[-1] and d.textlength(prueba, font=f) > W - 2 * MARGEN:
                lineas.append([])
            lineas[-1].append(w)
        y = 1365 - (len(lineas) - 1) * 40
        espacio = d.textlength(" ", font=f)
        for linea in lineas:
            ancho = sum(d.textlength(w["t"], font=f) for w in linea) + espacio * (len(linea) - 1)
            x = (W - ancho) / 2
            for w in linea:
                color = BRASA if w["ini"] <= t < w["fin"] + 0.05 else TEXTO
                d.text((x, y), w["t"], font=f, fill=color, anchor="lm", stroke_width=6, stroke_fill=(0, 0, 0))
                x += d.textlength(w["t"], font=f) + espacio
            y += 80

    def pie(self, d: ImageDraw.ImageDraw, esc: dict) -> None:
        # la fuente que el informe le da a cada cita válida, no la que eligió el modelo
        validas = [a for a in esc.get("afirmaciones", [])
                   if a.get("cita_respalda_cifra") is not False and a.get("dato_en_la_escena") is not False]
        fuentes = list(dict.fromkeys(re.sub(r"\s*\(.*?\)", "", f) for a in validas for f in a.get("fuente_segun_informe", [])))
        if fuentes:
            texto = recortar(d, "Fuente: " + " · ".join(fuentes), fuente("semi", 28), W - 2 * MARGEN)
            d.text((W / 2, 1505), texto, font=fuente("semi", 28), fill=GRIS, anchor="mm")
        texto_fuentes = " ".join(fuentes).lower()
        noticias = [n for n in self.guion.get("noticias", []) if n["medio"].split()[0].lower() in texto_fuentes]
        if noticias:
            n = noticias[0]
            fecha = "/".join(reversed(n["fecha"].split("-")))
            texto = f"NOTICIA   {n['medio']} · {fecha}"
            ancho = d.textlength(texto, font=fuente("semi", 28)) + 48
            d.rounded_rectangle(((W - ancho) / 2, 1540, (W + ancho) / 2, 1592), radius=26, outline=FUEGO, width=2)
            d.text((W / 2, 1566), texto, font=fuente("semi", 28), fill=TEXTO, anchor="mm")

    # ---------- cuadro ----------
    def cuadro(self, t: float) -> Image.Image:
        img = self.fondo.copy()
        capa = Image.new("RGBA", (W, H))
        d = ImageDraw.Draw(capa)
        if t >= self.inicio_tarjeta:
            local = t - self.inicio_tarjeta
            self.tarjeta_noticias(capa, d, local)
            img.alpha_composite(con_alfa(capa, suave(local / FUNDIDO_S)))
        else:
            i = max([k for k, x in enumerate(self.tiempos) if x["inicio"] <= t] or [0])
            tramo = self.tiempos[i]
            fin = self.tiempos[i + 1]["inicio"] if i + 1 < len(self.tiempos) else self.inicio_tarjeta
            esc = self.escenas[tramo["id"]]
            local = t - tramo["inicio"] if i or t >= tramo["inicio"] else 0.0
            visual = self.visuales[i] if i < len(self.visuales) else self.v_cierre
            visual(capa, d, local, esc, tramo["inicio"], fin)
            img.alpha_composite(con_alfa(capa, suave(local / FUNDIDO_S) if i else 1.0))
            dp = ImageDraw.Draw(img)
            self.pie(dp, esc)
        dp = ImageDraw.Draw(img)
        if t < self.inicio_tarjeta:     # la tarjeta final ya dice «enlaces completos en la descripción»
            self.subtitulo(dp, t)
        dp.rectangle((0, 0, W * t / self.total, 8), fill=FUEGO)
        return img.convert("RGB")


def agrupar(palabras: list[dict], max_car: int = 26, pausa: float = 0.35) -> list[list[dict]]:
    """Junta las palabras de Whisper en frases cortas: corta por largo, por pausa o por puntuación."""
    grupos, actual = [], []
    for w in palabras:
        largo = len(" ".join(x["t"] for x in actual + [w]))
        if actual and (largo > max_car or w["ini"] - actual[-1]["fin"] > pausa):
            grupos.append(actual)
            actual = []
        actual.append(w)
        if w["t"][-1:] in ".,:;?!":
            grupos.append(actual)
            actual = []
    return grupos + [actual] if actual else grupos


def transcribir(wav: Path, retranscribir: bool) -> list[dict]:
    """Palabras de Whisper tal cual (con su espacio inicial), guardadas en whisper_palabras.json."""
    cache = REEL / "whisper_palabras.json"
    if cache.exists() and not retranscribir and cache.stat().st_mtime > wav.stat().st_mtime:
        return json.loads(cache.read_text(encoding="utf-8"))
    from faster_whisper import WhisperModel
    audio, sr = sf.read(wav, dtype="float32")
    modelo = WhisperModel(MODELO_WHISPER, device="cpu", compute_type="int8")
    segmentos, _ = modelo.transcribe(soxr.resample(audio, sr, 16_000).astype(np.float32), language="es",
                                     word_timestamps=True, initial_prompt=VOCABULARIO, condition_on_previous_text=False)
    palabras = [{"t": w.word, "ini": round(w.start, 3), "fin": round(w.end, 3)}
                for s in segmentos for w in s.words if w.word.strip()]
    cache.write_text(json.dumps(palabras, ensure_ascii=False, indent=1), encoding="utf-8")
    return palabras


def limpiar(crudas: list[dict], textos: list[str]) -> list[dict]:
    """Deja las palabras de Whisper listas para subtitular. Whisper pone los tiempos y la puntuación;
    su texto se alinea con el del guion, que es lo que la voz lee:
    - se unen las fichas que Whisper corta sin espacio («41» + «.000») y los miles van como en el informe;
    - donde Whisper oyó otra palabra en el mismo lugar («surfor»), va la del guion («Serfor»);
    - lo que Whisper oyó y el guion no tiene, si dura menos de 0.35 s, es una respiración y se quita."""
    palabras = []
    for w in crudas:
        if palabras and not w["t"].startswith(" "):
            palabras[-1]["t"] += w["t"].strip()
            palabras[-1]["fin"] = w["fin"]
        else:
            palabras.append({**w, "t": w["t"].strip()})
    for w in palabras:
        w["t"] = re.sub(r"(\d)[.,](\d{3})\b", r"\1 \2", w["t"])

    def clave(t: str) -> str:
        return re.sub(r"\W+", "", t.replace(" ", "").lower())

    guion = [m.group() for texto in textos for m in re.finditer(r"\d{1,3}(?: \d{3})+|\w+", texto)]
    pasos = difflib.SequenceMatcher(None, [clave(w["t"]) for w in palabras], [clave(g) for g in guion],
                                    autojunk=False).get_opcodes()
    salida = []
    for op, i1, i2, j1, j2 in pasos:
        for k in range(i1, i2):
            w = dict(palabras[k])
            if op == "delete" and w["fin"] - w["ini"] < 0.35:
                continue
            if op in ("equal", "replace") and i2 - i1 == j2 - j1:
                g = guion[j1 + k - i1]
                if salida and salida[-1]["t"][-1:] in ".!?":        # Whisper puso punto: va mayúscula
                    g = g[0].upper() + g[1:]
                elif not salida:
                    g = g[0].upper() + g[1:]
                nucleo = re.sub(r"^\W+|\W+$", "", w["t"])
                w["t"] = w["t"].replace(nucleo, g, 1) if nucleo else w["t"]
            salida.append(w)
    return salida


def escribir_srt(grupos: list[list[dict]], ruta: Path) -> None:
    def hms(s: float) -> str:
        ms = int(round(s * 1000))
        return f"{ms // 3_600_000:02}:{ms // 60_000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"
    bloques = [f"{i}\n{hms(g[0]['ini'])} --> {hms(g[-1]['fin'])}\n{' '.join(w['t'] for w in g)}\n"
               for i, g in enumerate(grupos, 1)]
    ruta.write_text("\n".join(bloques), encoding="utf-8")


def escribir_descripcion(guion: dict, ruta: Path) -> None:
    partes = [guion.get("descripcion", "").strip(), "", "Noticias de referencia:"]
    partes += [f"• {n['medio']} ({'/'.join(reversed(n['fecha'].split('-')))}): {n['titular']}\n  {n['url']}"
               for n in guion.get("noticias", [])]
    partes += ["", f"Monitor completo, datos y método: {MONITOR_URL}",
               "Datos: NASA FIRMS (VIIRS S-NPP), MODIS MCD64A1, INEI, WorldPop 2020, COEN-Indeci, Serfor.",
               "", " ".join(guion.get("hashtags", []))]
    ruta.write_text("\n".join(partes).strip() + "\n", encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cuadros", type=float, nargs="+", help="guarda solo estos instantes (s) como PNG en reports/reel/cuadros/")
    p.add_argument("--retranscribir", action="store_true", help="vuelve a pasar reel.wav por Whisper")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    guion = json.loads((REEL / "guion.json").read_text(encoding="utf-8"))
    tiempos = json.loads((REEL / "reel_tiempos.json").read_text(encoding="utf-8"))
    wav = REEL / "reel.wav"
    dur_voz = sf.info(wav).duration
    palabras = limpiar(transcribir(wav, args.retranscribir), [e["texto"] for e in guion["escenas"]])
    reel = Reel(guion, tiempos, palabras, dur_voz)
    escribir_srt(reel.grupos, REEL / "reel.srt")
    escribir_descripcion(guion, REEL / "descripcion.txt")
    print(f"Whisper: {len(palabras)} palabras en {len(reel.grupos)} subtítulos → reports/reel/reel.srt")

    if args.cuadros:
        (REEL / "cuadros").mkdir(exist_ok=True)
        for t in args.cuadros:
            reel.cuadro(t).save(REEL / "cuadros" / f"t{t:05.1f}.png")
        print(f"Guardados {len(args.cuadros)} cuadros en reports/reel/cuadros/")
        return

    salida = REEL / "reel.mp4"
    n = int(math.ceil(reel.total * FPS))
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", str(wav), "-af", "apad", "-t", f"{n / FPS:.3f}",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(salida)]
    with subprocess.Popen(cmd, stdin=subprocess.PIPE) as ff:
        for k in range(n):
            ff.stdin.write(reel.cuadro(k / FPS).tobytes())
            if k % (FPS * 5) == 0:
                print(f"  {k / FPS:4.0f} s / {reel.total:.0f} s", flush=True)
        ff.stdin.close()
    print(f"Guardado {salida.relative_to(RAIZ)}: {reel.total:.1f} s, {salida.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
