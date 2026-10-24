# /// script
# requires-python = "==3.12.*"
# dependencies = [
#   "qwen-tts==0.1.1",
#   "torch==2.13.0",
#   "faster-whisper==1.2.1",
#   "soundfile>=0.12",
#   "truststore>=0.10.5",
#   "msvc-runtime==14.51.36231",
#   "audiotsm>=0.1.2",
# ]
#
# [tool.uv]
# # gradio solo lo usa la demo web de qwen-tts
# override-dependencies = ["gradio; sys_platform == 'never'"]
# ///
"""Clona la voz de una grabación con Qwen3-TTS y lee la locución de reports/reel/guion.json.

1. Decodifica la grabación de referencia (por defecto, el audio de WhatsApp más reciente en data/),
   la pasa a 24 kHz y la transcribe con Whisper. La transcripción queda en
   reports/reel/voz_referencia.txt: si Whisper se equivoca, se corrige ahí y se vuelve a correr.
2. Con Qwen3-TTS-12Hz-0.6B-Base arma la voz clonada (modo ICL: audio + su transcripción) y lee
   la "locucion" de cada escena por separado, así se sabe dónde empieza cada una.
3. Une las escenas con pausas cortas en reports/reel/reel.wav y guarda sus tiempos en
   reports/reel/reel_tiempos.json para el video.

Uso:
    uv run scripts/reel_voz.py
    uv run scripts/reel_voz.py --escenas 3 5          # vuelve a leer solo esas escenas
    uv run scripts/reel_voz.py --prueba "Hola, esta es una prueba de mi voz."

Corre en CPU: cuenta varios minutos por escena. Usa el modelo de 0.6B porque el de 1.7B no cabe
en el disco de esta PC.
"""
import argparse
import ctypes
import json
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
REEL = RAIZ / "reports" / "reel"
VOZ = REEL / "voz"
MODELO_TTS = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
MODELO_WHISPER = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
SR = 24_000
PAUSA_S = 0.35          # silencio entre escenas
SEMILLA = 2026
MAX_S = 56.0            # el reel dura de 45 a 60 s: la voz más los 3.5 s de la tarjeta final del video
MAX_ACELERACION = 1.15


def cargar_runtime_msvc() -> None:
    """PyTorch y CTranslate2 se compilan con MSVC 17.10+ y se caen (WinError 1114 o violación de
    acceso) con un msvcp140.dll anterior a 14.40; el de Windows en esta PC es 14.34. Se cargan
    primero las DLL que trae el paquete msvc-runtime, firmadas por Microsoft."""
    if sys.platform != "win32":
        return
    for nombre in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        ruta = Path(sys.prefix) / "Scripts" / nombre
        if ruta.exists():
            ctypes.WinDLL(str(ruta))


cargar_runtime_msvc()

import av
import numpy as np
import soundfile as sf
import soxr
import torch
import truststore

truststore.inject_into_ssl()    # en esta PC algo intercepta HTTPS; así Python usa los certificados de Windows


def leer_audio(ruta: Path, sr: int) -> np.ndarray:
    # se decodifica con PyAV: faster-whisper 1.2.1 no funciona con PyAV 19 para leer archivos
    with av.open(str(ruta)) as c:
        fuente = c.streams.audio[0].rate
        partes = [f.to_ndarray() for f in c.decode(audio=0)]
    audio = np.concatenate([p.mean(axis=0) if p.ndim > 1 else p for p in partes]).astype(np.float32)
    return soxr.resample(audio, fuente, sr).astype(np.float32)


def recortar_silencio(audio: np.ndarray, sr: int, umbral: float = 0.01, margen_s: float = 0.15) -> np.ndarray:
    fuerte = np.flatnonzero(np.abs(audio) > umbral)
    if not len(fuerte):
        return audio
    m = int(margen_s * sr)
    return audio[max(fuerte[0] - m, 0):fuerte[-1] + m]


def referencia(ruta: Path, retranscribir: bool) -> tuple[np.ndarray, str]:
    audio = recortar_silencio(leer_audio(ruta, SR), SR)
    audio = 0.9 * audio / np.abs(audio).max()
    sf.write(REEL / "voz_referencia.wav", audio, SR)
    txt = REEL / "voz_referencia.txt"
    if retranscribir or not txt.exists():
        from faster_whisper import WhisperModel
        whisper = WhisperModel(MODELO_WHISPER, device="cpu", compute_type="int8")
        segmentos, _ = whisper.transcribe(soxr.resample(audio, SR, 16_000).astype(np.float32), language="es")
        txt.write_text(" ".join(s.text.strip() for s in segmentos) + "\n", encoding="utf-8")
        del whisper
    return audio, txt.read_text(encoding="utf-8").strip()


def grabacion_por_defecto() -> Path:
    audios = [p for ext in ("mp4", "m4a", "ogg", "opus", "wav", "mp3") for p in (RAIZ / "data").rglob(f"*.{ext}")]
    if not audios:
        sys.exit("No encontré la grabación de referencia en data/; indícala con --referencia")
    return max(audios, key=lambda p: p.stat().st_mtime)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--referencia", type=Path, help="grabación de la voz a clonar (por defecto, la más reciente en data/)")
    p.add_argument("--escenas", type=int, nargs="+", help="ids de las escenas a volver a leer (por defecto, todas)")
    p.add_argument("--prueba", help="lee solo este texto y lo guarda en reports/reel/voz/prueba.wav")
    p.add_argument("--retranscribir", action="store_true", help="vuelve a transcribir la referencia con Whisper")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    VOZ.mkdir(parents=True, exist_ok=True)

    ruta = args.referencia or grabacion_por_defecto()
    audio_ref, texto_ref = referencia(ruta, args.retranscribir)
    print(f"Referencia: {ruta.name} ({len(audio_ref) / SR:.1f} s)\n  «{texto_ref}»")

    if args.prueba:
        escenas = [{"id": "prueba", "locucion": args.prueba}]
    else:
        guion = json.loads((REEL / "guion.json").read_text(encoding="utf-8"))
        escenas = guion["escenas"]
        sin_verificar = sum(not a.get("verificado") for e in escenas for a in e.get("afirmaciones", []))
        if sin_verificar:
            print(f"Aviso: {sin_verificar} datos de guion.json siguen con \"verificado\": false")
        if args.escenas:
            escenas = [e for e in escenas if e["id"] in args.escenas]

    from huggingface_hub import snapshot_download
    from qwen_tts import Qwen3TTSModel
    t = time.time()
    # con la carpeta local, transformers no vuelve a consultar la API de Hugging Face al cargar.
    # float32: esta CPU (Ryzen 5 5500U) no calcula bfloat16 en hardware y PyTorch cae en una ruta
    # que no termina ni una frase en 15 min; en float32 va a ~10 s por segundo de audio. Cuantizar
    # a int8 (dinámico, por tensor o por canal) lo deja igual de rápido, pero la voz sale ininteligible.
    tts = Qwen3TTSModel.from_pretrained(snapshot_download(MODELO_TTS), device_map="cpu", dtype=torch.float32)
    voz = tts.create_voice_clone_prompt(ref_audio=(audio_ref, SR), ref_text=texto_ref)
    print(f"Modelo cargado en {time.time() - t:.0f} s")

    for esc in escenas:
        t = time.time()
        torch.manual_seed(SEMILLA)
        # el modelo trae max_new_tokens=8192 (~11 min); si no cierra a tiempo, en CPU tarda horas.
        # 12 códigos por segundo y al menos 10 caracteres por segundo, más 2 s de margen
        tope = int(12 * (len(esc["locucion"]) / 10 + 2))
        wavs, sr = tts.generate_voice_clone(text=esc["locucion"], language="Spanish", voice_clone_prompt=voz,
                                            max_new_tokens=tope)
        assert sr == SR, sr
        sf.write(VOZ / f"escena_{esc['id']}.wav", wavs[0], sr)
        print(f"  escena {esc['id']}: {len(wavs[0]) / sr:.1f} s de audio en {time.time() - t:.0f} s")
    if args.prueba:
        return

    # une todas las escenas del guion, también las que no se volvieron a leer
    pausa = np.zeros(int(PAUSA_S * SR), dtype=np.float32)
    partes, tiempos, inicio = [], [], 0.0
    for esc in guion["escenas"]:
        wav, _ = sf.read(VOZ / f"escena_{esc['id']}.wav", dtype="float32")
        wav = recortar_silencio(wav, SR, umbral=0.005, margen_s=0.08)
        partes += [wav, pausa]
        tiempos.append({"id": esc["id"], "nombre": esc["nombre"], "inicio": round(inicio, 3),
                        "fin": round(inicio + len(wav) / SR, 3)})
        inicio += (len(wav) + len(pausa)) / SR
    reel = np.concatenate(partes)
    dur = len(reel) / SR
    if dur > MAX_S:
        # se acelera con WSOLA, que no cambia el tono; más de un 15 % ya se nota: ahí hay que acortar el guion
        from audiotsm import wsola
        from audiotsm.io.array import ArrayReader, ArrayWriter
        tasa = min(dur / MAX_S, MAX_ACELERACION)
        escritor = ArrayWriter(channels=1)
        wsola(channels=1, speed=tasa).run(ArrayReader(reel[None, :]), escritor)
        reel = escritor.data[0].astype(np.float32)
        tiempos = [{**x, "inicio": round(x["inicio"] / tasa, 3), "fin": round(x["fin"] / tasa, 3)} for x in tiempos]
        print(f"La voz duraba {dur:.1f} s: se aceleró ×{tasa:.2f}")
    reel = 0.89 * reel / np.abs(reel).max()      # pico en -1 dBFS
    sf.write(REEL / "reel.wav", reel, SR, subtype="PCM_16")
    (REEL / "reel_tiempos.json").write_text(json.dumps(tiempos, ensure_ascii=False, indent=2), encoding="utf-8")

    dur = len(reel) / SR
    print(f"Guardado reports/reel/reel.wav: {dur:.1f} s" + ("" if dur <= MAX_S + 0.5 else "  (demasiado larga: acortar el guion)"))


if __name__ == "__main__":
    main()
