# /// script
# requires-python = "==3.12.*"
# dependencies = [
#   "llama-cpp-python==0.3.36",
#   "huggingface-hub>=0.26",
#   "num2words>=0.5.13",
#   "msvc-runtime==14.51.36231",
#   "truststore>=0.10.5",
# ]
#
# [[tool.uv.index]]
# name = "llama-cpu"
# url = "https://abetlen.github.io/llama-cpp-python/whl/cpu"
# explicit = true
#
# [tool.uv.sources]
# llama-cpp-python = { index = "llama-cpu" }
# ///
"""Arma reports/reel/guion.json con Qwen2.5-1.5B (local, llama.cpp) a partir del guion y del informe.

Un modelo de 1.5B no copia citas ni pasa cifras a letras de forma fiable, así que el trabajo se
reparte:
- El modelo, por escena, escribe la locución (cifras en letras, para la voz), propone lo que va en
  pantalla y, para cada dato, ELIGE la frase del informe que lo respalda entre frases numeradas y
  la fuente entre las que cita el informe. La salida está restringida a un esquema JSON.
- El código copia el texto de la escena desde reports/reel/guion.md y la cita desde el informe
  (reports/monitor_incendios_2026.html), así que ambos son textuales.
- La revisión automática comprueba que cada cifra del texto esté en el informe (solo se aceptan
  redondeos de cifras redondas, nunca de años), que la locución diga lo mismo que el texto, que la
  pantalla use una cifra de la escena con su unidad y una etiqueta legible, y, en cada cita, que el
  dato sea de la escena, que la frase contenga la cifra y que la fuente elegida sea la que el informe
  le da a esa frase. La locución y la pantalla que no pasan se reemplazan por la versión
  determinista o la del guion; las citas que no pasan quedan marcadas. Todo queda anotado en
  "revision" y cada dato y cada noticia llevan "verificado": false para la revisión manual, que se
  hace con reports/reel/verificacion.md.

Uso:
    uv run scripts/reel_guion.py
"""
import argparse
import ctypes
import html
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path


def cargar_runtime_msvc() -> None:
    """llama.cpp se compila con MSVC 17.10+ y se cae con un msvcp140.dll anterior a 14.40; el de
    Windows en esta PC es 14.34. Se cargan primero las DLL del paquete msvc-runtime."""
    if sys.platform != "win32":
        return
    for nombre in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        ruta = Path(sys.prefix) / "Scripts" / nombre
        if ruta.exists():
            ctypes.WinDLL(str(ruta))


cargar_runtime_msvc()

import truststore

truststore.inject_into_ssl()    # en esta PC algo intercepta HTTPS; así Python usa los certificados de Windows

from huggingface_hub import hf_hub_download
from llama_cpp import Llama
from num2words import num2words

RAIZ = Path(__file__).resolve().parent.parent
INFORME = RAIZ / "reports" / "monitor_incendios_2026.html"
REEL = RAIZ / "reports" / "reel"
MODELO = ("Qwen/Qwen2.5-1.5B-Instruct-GGUF", "qwen2.5-1.5b-instruct-q4_k_m.gguf")
CORTE = "2026-10-21"
CANDIDATAS = 8          # frases del informe que ve el modelo por escena

# Fuentes tal como las atribuye el informe, con las marcas que las nombran en el texto
FUENTES = {
    "MODIS MCD64A1 (área quemada)": [r"\bMODIS\b"],
    "VIIRS S-NPP, NASA FIRMS (focos de calor)": [r"VIIRS", r"FIRMS"],
    "WorldPop 2020 (población cercana)": [r"WorldPop"],
    "COEN-Indeci vía Mongabay, 13/10/2026": [r"Mongabay", r"COEN"],
    "RPP, 08/10/2026": [r"\bRPP\b"],
    "Serfor vía Infobae, 13/10/2026": [r"Serfor, 13/10", r"Infobae"],
    "Serfor vía Actualidad Ambiental, 09/06/2022": [r"Según Serfor, el 95"],
    "D. S. 009-2013-MINAGRI (Política Forestal)": [r"009-2013"],
    "RDE 284-2018-MINAGRI-SERFOR-DE (plan del Serfor)": [r"284-2018", r"Plan de Prevención y Reducción"],
}
# Si la frase no nombra la fuente, el tema del dato la delata: así lo explica el método del informe
TEMAS = {
    "MODIS MCD64A1 (área quemada)": r"hectáreas|\bha\b|quem",
    "VIIRS S-NPP, NASA FIRMS (focos de calor)": r"focos",
    "WorldPop 2020 (población cercana)": r"personas|viven",
    "COEN-Indeci vía Mongabay, 13/10/2026": r"fallec|emergencias|cobertura natural|cultivos",
}
HASHTAGS = ["#IncendiosForestales", "#Perú", "#FocosDeCalor", "#Amazonía", "#DatosAbiertos"]
# Las tarjetas de cifras del informe: título, cifra, descripción y, a veces, una línea de fuente
LINEA_CIFRA = re.compile(r"^[×+]?\d[\d .,]*\s?(ha|%)?$")
LINEA_FUENTE = re.compile(r"^(COEN|Indeci|Serfor, \d|WorldPop|VIIRS S-NPP, al)")
UNIDADES = {"%": "por ciento", "km": "kilómetros", "ha": "hectáreas"}
VACIAS = set("para pero entre desde hasta según donde mientras tanto todavía sobre están tiene ellos".split())

SISTEMA = "Eres asistente de un equipo de verificación de datos en el Perú. Respondes solo con JSON válido."
PEDIDO = """ESCENA: «{texto}»
EN PANTALLA, según el guion: {pantalla}

FRASES DEL INFORME:
{frases}

Tareas:
1. "locucion": copia la ESCENA palabra por palabra, pero escribe en letras todas las cifras, años,
   porcentajes y unidades. Ejemplos: "41 000 hectáreas" -> "cuarenta y un mil hectáreas";
   "3 137" -> "tres mil ciento treinta y siete"; "2013" -> "dos mil trece";
   "43 %" -> "cuarenta y tres por ciento"; "5 km" -> "cinco kilómetros". No agregues ni quites nada más.
2. "pantalla": "cifra" (la cifra principal de la escena, corta), "etiqueta" (qué mide) y "detalle"
   (contexto breve). Usa solo datos de la ESCENA o de las FRASES.
3. "afirmaciones": un elemento por cada dato de la ESCENA: "dato" (la cifra o el hecho), "frase"
   (el id de la frase del informe que lo respalda) y "fuente" (de dónde sale ese dato)."""


# ---------- informe ----------
def normalizar(texto: str) -> str:
    # el informe separa miles con espacios finos o duros; aquí todo pasa a un espacio común
    texto = re.sub(r"[   ]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def plano(texto: str) -> str:
    """Minúsculas, sin tildes ni puntuación: para comparar textos que deberían decir lo mismo."""
    texto = unicodedata.normalize("NFD", normalizar(texto).lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w ]+", " ", texto).split())


def lineas_informe() -> tuple[list[str], dict[str, str]]:
    s = INFORME.read_text(encoding="utf-8")
    enlaces = {u: normalizar(html.unescape(re.sub(r"<[^>]+>", "", t)))
               for u, t in re.findall(r'<a [^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', s, re.S)}
    s = re.sub(r"<(script|style|svg)[\s\S]*?</\1>", " ", s)
    s = re.sub(r"<(br|/p|/h\d|/li|/tr|/div|/section)[^>]*>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return [l for l in (normalizar(x) for x in s.splitlines()) if l], enlaces


def frases_informe(lineas: list[str]) -> list[dict]:
    """Frases citables. Las líneas largas se parten en oraciones; las cortas se juntan como tarjetas:
    una tarjeta empieza en el título que precede a una cifra y termina en su línea de fuente."""
    frases, bloque = [], []

    def cerrar():
        if bloque:
            frases.append({"texto": " ".join(t for _, t in bloque), "linea": bloque[0][0], "linea_fin": bloque[-1][0]})
            bloque.clear()

    for i, l in enumerate(lineas):
        if len(l) >= 70:
            cerrar()
            for oracion in re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿«])", l):
                if len(oracion) > 25:
                    frases.append({"texto": oracion, "linea": i, "linea_fin": i})
            continue
        siguiente = lineas[i + 1] if i + 1 < len(lineas) else ""
        if (LINEA_CIFRA.match(siguiente) and not LINEA_CIFRA.match(l)) or l.startswith("Hallazgo "):
            cerrar()
        bloque.append((i, l))
        if LINEA_FUENTE.match(l) or len(bloque) == 6:
            cerrar()
    cerrar()
    for k, f in enumerate(frases, 1):
        f.update(id=f"F{k}", valores={v for _, v in cifras(f["texto"])}, plano=set(plano(f["texto"]).split()))
    return frases


def cifras(texto: str) -> list[tuple[str, float]]:
    texto = normalizar(texto)
    return [(m, float(m.replace(" ", "").replace(",", ".")))
            for m in re.findall(r"\d{1,3}(?: \d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?", texto)]


def calza(valor: float, valores: set[float]) -> str | None:
    """'exacta' si la cifra está tal cual. Una cifra redonda del guion («más de 61 000») también
    calza con la del informe que, truncada a miles, da lo mismo (61 481). Los años nunca se redondean."""
    if valor in valores:
        return "exacta"
    if valor >= 1000 and valor % 1000 == 0:
        for v in sorted(valores):
            if v // 1000 * 1000 == valor:
                return f"redondeo de {miles(v)}"
    return None


def miles(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ") if n == int(n) else str(n)


# ---------- guion ----------
def escenas_guion() -> list[dict]:
    filas = re.findall(r"^\| (\d+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$",
                       (REEL / "guion.md").read_text(encoding="utf-8"), re.M)
    return [{"id": int(i), "nombre": n.strip(), "texto": t.strip(), "pantalla": p.strip()} for i, n, t, p in filas]


def pantalla_guion(texto: str) -> dict:
    """«**41 000 ha** · barras 2024 / 2025 / 2026 (ene–ago)» -> cifra, etiqueta, detalle."""
    negritas = re.findall(r"\*\*(.+?)\*\*", texto)
    resto = [p.strip() for p in re.sub(r"\*\*.+?\*\*", "", texto).split("·") if p.strip()]
    return {"cifra": negritas[0] if negritas else "", "etiqueta": resto[0] if resto else "",
            "detalle": " · ".join(negritas[1:] + resto[1:])}


def en_letras(texto: str) -> str:
    """Locución determinista: cifras, porcentajes y unidades en letras."""
    def reemplazo(m: re.Match) -> str:
        # num2words dice "cuarenta y uno mil"; delante de "mil" va "un"
        palabras = re.sub(r"\buno mil\b", "un mil", num2words(int(m.group(1).replace(" ", "")), lang="es"))
        return palabras + (f" {UNIDADES[m.group(2)]}" if m.group(2) else "")
    return re.sub(r"(\d{1,3}(?: \d{3})+|\d+)(?:\s?(%|km|ha)(?!\w))?", reemplazo, normalizar(texto))


def claves(texto: str) -> set[str]:
    return {w for w in plano(texto).split() if len(w) >= 5 and w not in VACIAS}


# ---------- modelo ----------
def candidatas(texto: str, frases: list[dict]) -> list[dict]:
    valores, palabras = {v for _, v in cifras(texto)}, claves(texto)

    def puntaje(f: dict) -> float:
        p = sum(3 for v in valores if v >= 100 and calza(v, f["valores"]))
        p += sum(1 for v in valores if v < 100 and v in f["valores"])
        return p + len(palabras & f["plano"])
    return sorted(frases, key=puntaje, reverse=True)[:CANDIDATAS]


def preguntar(llm: Llama, pedido: str, esquema: dict, max_tokens: int = 700) -> dict:
    r = llm.create_chat_completion(
        messages=[{"role": "system", "content": SISTEMA}, {"role": "user", "content": pedido}],
        response_format={"type": "json_object", "schema": esquema},
        temperature=0.1, max_tokens=max_tokens, seed=2026,
    )
    return json.loads(r["choices"][0]["message"]["content"])


def esquema_escena(ids: list[str]) -> dict:
    texto = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "locucion": texto,
            "pantalla": {"type": "object", "properties": {"cifra": texto, "etiqueta": texto, "detalle": texto},
                         "required": ["cifra", "etiqueta", "detalle"]},
            "afirmaciones": {"type": "array", "minItems": 1, "maxItems": 4, "items": {
                "type": "object",
                "properties": {"dato": texto, "frase": {"enum": ids}, "fuente": {"enum": list(FUENTES)}},
                "required": ["dato", "frase", "fuente"]}},
        },
        "required": ["locucion", "pantalla", "afirmaciones"],
    }


# ---------- revisión ----------
def nombra(texto: str) -> list[str]:
    return [f for f, marcas in FUENTES.items() if any(re.search(m, texto) for m in marcas)]


def tema_cercano(texto: str, dato: str, opciones) -> str | None:
    """La fuente cuyo tema (hectáreas, focos, personas, fallecidos) aparece más cerca del dato."""
    numeros = [l for l, _ in cifras(dato)]
    pos = next((texto.find(n) for n in numeros if n in texto), None)
    temas = [(abs(m.start() - pos) if pos is not None else 0, f)
             for f, p in TEMAS.items() if f in opciones for m in re.finditer(p, texto, re.I)]
    if temas and (pos is not None or len({f for _, f in temas}) == 1):
        return min(temas)[1]
    return None


def fuentes_segun_informe(frase: dict, dato: str, lineas: list[str]) -> list[str]:
    """La fuente que el informe le da a la frase: la que nombra la propia frase (las tarjetas
    terminan en su línea de fuente); si no nombra ninguna, la del tema más cercano al dato; si
    tampoco, la que nombran las dos líneas siguientes. Cuando hay varias, decide el tema."""
    texto = frase["texto"]
    nombradas = nombra(texto)
    if not nombradas:
        tema = tema_cercano(texto, dato, TEMAS)
        if tema:
            return [tema]
        nombradas = nombra(" ".join(lineas[frase["linea_fin"] + 1:frase["linea_fin"] + 3]))
    if len(nombradas) > 1:
        tema = tema_cercano(texto, dato, nombradas)
        if tema:
            return [tema]
    return nombradas


def revisar_escena(esc: dict, qwen: dict, frases: dict[str, dict], todas: list[dict], lineas: list[str],
                   en_informe: set[float]) -> dict:
    rev = {"cifras": [], "locucion": "", "pantalla": "", "afirmaciones_agregadas": 0}

    # 1. cada cifra del texto debe estar en el informe; se anota la frase más afín donde aparece
    palabras = claves(esc["texto"])
    for literal, valor in cifras(esc["texto"]):
        estado = calza(valor, en_informe)
        evidencia = max((f for f in todas if calza(valor, f["valores"])), key=lambda f: len(palabras & f["plano"]),
                        default=None)
        rev["cifras"].append({"cifra": literal, "estado": estado or "NO está en el informe",
                              "frase": evidencia["id"] if evidencia else None,
                              "cita": evidencia["texto"] if evidencia else None})

    # 2. la locución debe decir lo mismo que el texto; si no, se usa la versión determinista
    esperada = en_letras(esc["texto"])
    propuesta = normalizar(qwen.get("locucion", ""))
    if plano(propuesta) == plano(esperada):
        esc["locucion"] = propuesta
        rev["locucion"] = "la de Qwen coincide con el texto"
    else:
        esc["locucion"] = esperada
        rev["locucion"] = "corregida: la de Qwen no decía lo mismo que el texto"
        rev["locucion_qwen"] = propuesta

    # 3. la cifra grande de la pantalla tiene que ser una que se dice en la escena o que el guion
    #    propone, con su unidad; la etiqueta tiene que decir qué mide; las demás cifras de la
    #    pantalla tienen que estar en el informe
    pantalla = {k: normalizar(str(v)) for k, v in qwen.get("pantalla", {}).items()}
    del_guion = pantalla_guion(esc["pantalla_guion"])
    de_la_escena = {v for _, v in cifras(esc["texto"] + " " + esc["pantalla_guion"])}
    cifra = pantalla.get("cifra", "")
    problemas = [f"«{l}» no se dice en la escena" for l, v in cifras(cifra) if not calza(v, de_la_escena)]
    problemas += [f"{l} no está en el informe" for campo in ("etiqueta", "detalle")
                  for l, v in cifras(pantalla.get(campo, "")) if not calza(v, en_informe | de_la_escena)]
    if not cifras(cifra):
        problemas.append("la cifra principal no tiene número")
    unidad = re.sub(r"[\d\s.,]+", "", del_guion["cifra"])       # ×, ha, %, años
    if unidad and unidad not in cifra:
        problemas.append(f"a «{cifra}» le falta «{unidad}»")
    if len(re.findall(r"[^\W\d_]{2,}", pantalla.get("etiqueta", ""))) < 2:
        problemas.append(f"la etiqueta «{pantalla.get('etiqueta', '')}» no dice qué mide")
    if problemas:
        esc["pantalla"] = pantalla_guion(esc.pop("pantalla_guion"))
        rev["pantalla"] = "corregida con la del guion: " + "; ".join(problemas)
        rev["pantalla_qwen"] = pantalla
    else:
        esc.pop("pantalla_guion")
        esc["pantalla"] = pantalla
        rev["pantalla"] = "la de Qwen solo usa cifras de la escena y del informe"

    # 4. cada afirmación: la cita se copia del informe y se comprueba que el dato sea de la escena,
    #    que la cita contenga su cifra y que la fuente elegida sea la que el informe le da
    afirmaciones, vistas = [], set()
    for af in qwen.get("afirmaciones", []):
        f = frases[af["frase"]]
        if (plano(af["dato"]), f["id"]) in vistas:
            continue
        vistas.add((plano(af["dato"]), f["id"]))
        numeros = [v for _, v in cifras(af["dato"])]
        segun = fuentes_segun_informe(f, af["dato"], lineas)
        afirmaciones.append({
            "dato": normalizar(af["dato"]), "cita_informe": f["texto"], "frase": f["id"], "fuente": af["fuente"],
            "origen": "Qwen2.5-1.5B",
            "dato_en_la_escena": all(calza(v, de_la_escena) for v in numeros) if numeros else None,
            "cita_respalda_cifra": all(calza(v, f["valores"]) for v in numeros) if numeros else None,
            "fuente_segun_informe": segun,
            "fuente_coincide": af["fuente"] in segun if segun else None,
            "verificado": False,
        })
    # 5. las cifras del texto que ninguna cita válida y con fuente respalda reciben la frase donde aparecen
    validas = [a for a in afirmaciones if a["cita_respalda_cifra"] is not False and a["fuente_segun_informe"]]
    for c in rev["cifras"]:
        valor = float(c["cifra"].replace(" ", ""))
        cubierta = any(calza(valor, frases[a["frase"]]["valores"]) for a in validas)
        if not cubierta and c["frase"] and valor >= 10:
            f = frases[c["frase"]]
            segun = fuentes_segun_informe(f, c["cifra"], lineas)
            unica = len(segun) == 1
            afirmaciones.append({
                "dato": c["cifra"], "cita_informe": f["texto"], "frase": f["id"],
                "fuente": segun[0] if unica else "por verificar", "origen": "revisión automática",
                "dato_en_la_escena": True, "cita_respalda_cifra": True,
                "fuente_segun_informe": segun, "fuente_coincide": True if unica else None, "verificado": False,
            })
            rev["afirmaciones_agregadas"] += 1
    esc["afirmaciones"] = afirmaciones
    return rev


def avisos(a: dict) -> list[str]:
    salida = []
    if a["dato_en_la_escena"] is False:
        salida.append("el dato no se dice en esta escena")
    if a["cita_respalda_cifra"] is False:
        salida.append("la cita NO contiene la cifra")
    if a["fuente_coincide"] is False:
        salida.append(f"el informe le da otra fuente: {', '.join(a['fuente_segun_informe'])}")
    if a["fuente_coincide"] is None:
        salida.append(f"elegir entre: {', '.join(a['fuente_segun_informe'])}" if a["fuente_segun_informe"]
                      else "el informe no nombra la fuente junto a la cita")
    return salida


def escribir_verificacion(guion: dict, ruta: Path) -> None:
    md = [f"# Verificación manual · {guion['titulo']}", "",
          f"Generado el {guion['generado_por']['fecha'][:10]} con {guion['generado_por']['modelo']}. "
          "Marca cada casilla y, en guion.json, cambia \"verificado\" a true.", ""]
    for esc in guion["escenas"]:
        rev = esc["revision"]
        md += [f"## {esc['id']}. {esc['nombre']}", "", f"> {esc['texto']}", "",
               f"- Locución: {esc['locucion']}  \n  *{rev['locucion']}*",
               f"- Pantalla: **{esc['pantalla']['cifra']}** · {esc['pantalla']['etiqueta']} · {esc['pantalla']['detalle']}  \n"
               f"  *{rev['pantalla']}*", ""]
        for c in rev["cifras"]:
            md.append(f"- [ ] Cifra **{c['cifra']}**: {c['estado']}" + (f" — {c['frase']}: «{c['cita']}»" if c["frase"] else ""))
        for a in esc["afirmaciones"]:
            md.append(f"- [ ] **{a['dato']}** — {a['fuente']} — {a['frase']}: «{a['cita_informe']}»"
                      + (f"  \n  ⚠ {'; '.join(avisos(a))}" if avisos(a) else "") + f"  \n  *({a['origen']})*")
        md.append("")
    md += ["## Noticias", ""]
    md += [f"- [ ] {n['medio']} ({n['fecha']}): [{n['titular']}]({n['url']})" for n in guion["noticias"]]
    md += ["", "## Descripción para publicar", "", guion["descripcion"], "", " ".join(guion["hashtags"]),
           f"  \n*{guion['revision_descripcion']}*", ""]
    ruta.write_text("\n".join(md), encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    lineas, enlaces = lineas_informe()
    todas = frases_informe(lineas)
    por_id = {f["id"]: f for f in todas}
    en_informe = {v for l in lineas for _, v in cifras(l)}
    noticias = json.loads((REEL / "noticias.json").read_text(encoding="utf-8"))

    llm = Llama(model_path=hf_hub_download(*MODELO), n_ctx=4096, n_threads=6, verbose=False)
    escenas = []
    for e in escenas_guion():
        cands = candidatas(e["texto"], todas)
        pedido = PEDIDO.format(texto=e["texto"], pantalla=e["pantalla"],
                               frases="\n".join(f"{f['id']}: {f['texto']}" for f in cands))
        qwen = preguntar(llm, pedido, esquema_escena([f["id"] for f in cands]))
        esc = {"id": e["id"], "nombre": e["nombre"], "texto": e["texto"], "pantalla_guion": e["pantalla"]}
        esc["revision"] = revisar_escena(esc, qwen, por_id, todas, lineas, en_informe)
        escenas.append(esc)
        print(f"  {esc['id']}. {esc['nombre']}: locución {esc['revision']['locucion'].split(':')[0]}, "
              f"pantalla {esc['revision']['pantalla'].split(':')[0]}, {len(esc['afirmaciones'])} citas")

    # descripción para publicar; si trae cifras que no están en el informe, se arma una sin modelo
    textos = " ".join(e["texto"] for e in escenas)
    desc = preguntar(llm, f"Escribe la descripción de un reel de Instagram (máximo 400 caracteres, en español "
                          f"del Perú) con este guion, sin agregar cifras nuevas, y hasta 5 hashtags en español "
                          f"(por ejemplo #IncendiosForestales).\nGUION: {textos}",
                     {"type": "object", "properties": {"descripcion": {"type": "string"},
                      "hashtags": {"type": "array", "maxItems": 5, "items": {"type": "string"}}},
                      "required": ["descripcion", "hashtags"]}, max_tokens=400)
    hashtags = ["#" + h.lstrip("#") for h in desc["hashtags"] if re.fullmatch(r"#?\w+", h)]
    revision_hashtags = "los de Qwen"
    if not hashtags:
        hashtags = HASHTAGS
        revision_hashtags = f"Qwen no dio hashtags válidos ({desc['hashtags']}); se usan los de por defecto"
    ajenas = [l for l, v in cifras(desc["descripcion"]) if not calza(v, en_informe)]
    if ajenas:
        desc["descripcion"] = " ".join(e["texto"] for e in escenas[:3])
        revision_desc = f"corregida: la de Qwen traía cifras que no están en el informe ({', '.join(ajenas)})"
    else:
        revision_desc = "la de Qwen solo usa cifras del informe"

    guion = {
        "titulo": "Incendios forestales en el Perú 2026",
        "corte_datos": CORTE,
        "duracion_objetivo_s": {"min": 45, "max": 60},
        "fuente_principal": "reports/monitor_incendios_2026.html",
        "generado_por": {"modelo": f"{MODELO[0]} ({MODELO[1]})", "motor": "llama.cpp (local, CPU)",
                         "fecha": datetime.now().isoformat(timespec="seconds")},
        "escenas": escenas,
        "noticias": [{**n, "url_en_informe": n["url"] in enlaces, "verificado": False} for n in noticias],
        "descripcion": normalizar(desc["descripcion"]),
        "hashtags": hashtags,
        "revision_descripcion": revision_desc,
        "revision_hashtags": revision_hashtags,
    }
    (REEL / "guion.json").write_text(json.dumps(guion, ensure_ascii=False, indent=2), encoding="utf-8")
    escribir_verificacion(guion, REEL / "verificacion.md")

    print("Guardados reports/reel/guion.json y reports/reel/verificacion.md")
    for esc in escenas:
        lista = [f"{c['cifra']}: {c['estado']}" for c in esc["revision"]["cifras"] if c["estado"] != "exacta"]
        lista += [f"«{a['dato']}»: {aviso}" for a in esc["afirmaciones"] for aviso in avisos(a)]
        print(f"  {esc['id']}. " + ("\n     ".join(lista) if lista else "sin observaciones"))
    print(f"  descripción: {revision_desc}")


if __name__ == "__main__":
    main()
