"""Manda cada video terminado al Telegram de Sebastián (él publica a mano).
Config FUERA del repo: ~/.adflow-telegram.json  ->  {"token": "<token de @BotFather>", "chat_id": 123456}
Si falta chat_id: escríbele cualquier cosa a tu bot y corre  py -3.13 telegram_envio.py  (lo encuentra y lo guarda).
Prueba:  py -3.13 telegram_envio.py ruta\\video.mp4 "texto"
"""
import json, sys
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
from pathlib import Path

ARCHIVOS = {"clips": Path.home() / ".adflow-telegram.json", "fabrica": Path.home() / ".adflow-telegram-fabrica.json",
            "gangaclips": Path.home() / ".adflow-telegram-gangaclips.json"}  # PC de Nicolás: Westcol y amigos


def _ruta(bot):
    f = ARCHIVOS.get(bot, ARCHIVOS["clips"])
    return f if f.exists() else ARCHIVOS["clips"]


def _cfg(bot="clips"):
    CFG = _ruta(bot)
    if not CFG.exists(): return None
    try: return json.loads(CFG.read_text(encoding="utf-8"))
    except Exception: return None


def buscar_chat_id(bot="clips"):
    import requests
    CFG = _ruta(bot)
    c = _cfg(bot)
    if not c or not c.get("token"): print("falta", CFG); return None
    r = requests.get(f"https://api.telegram.org/bot{c['token']}/getUpdates", timeout=30).json()
    ids = [u["message"]["chat"]["id"] for u in r.get("result", []) if "message" in u]
    if not ids: print("Escríbele un mensaje a tu bot en Telegram y vuelve a correr esto."); return None
    c["chat_id"] = ids[-1]
    CFG.write_text(json.dumps(c), encoding="utf-8")
    print("chat_id guardado"); return ids[-1]


def texto(mensaje, bot="clips"):
    """Solo un mensaje de texto (sin video). Nunca rompe al que lo llama."""
    c = _cfg(bot)
    if not c or not c.get("token") or not c.get("chat_id"): return False
    import requests
    try:
        return requests.post(f"https://api.telegram.org/bot{c['token']}/sendMessage",
                             data={"chat_id": c["chat_id"], "text": mensaje[:4000]}, timeout=30).ok
    except Exception:
        return False


def copiable(mensaje, bot="clips"):
    """Mensaje en formato código: en Telegram se copia con UN toque (para pegarlo en TikTok). Nunca rompe."""
    c = _cfg(bot)
    if not c or not c.get("token") or not c.get("chat_id"): return False
    import html, requests
    try:
        return requests.post(f"https://api.telegram.org/bot{c['token']}/sendMessage",
                             data={"chat_id": c["chat_id"], "parse_mode": "HTML",
                                   "text": "<code>" + html.escape(mensaje[:3900]) + "</code>"}, timeout=30).ok
    except Exception:
        return False


def _comprimir(video, mb=45):
    """Copia del video que pesa menos de `mb` MB (bitrate según la duración). Si falla, devuelve el original."""
    import subprocess, tempfile
    try:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                                   capture_output=True, text=True).stdout.strip())
        kbps = int(mb * 8192 / dur) - 160  # deja 160 kbps para el audio
        dst = Path(tempfile.gettempdir()) / ("tg-" + video.name)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-c:v", "libx264", "-preset", "veryfast",
                        "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k", "-c:a", "aac", "-b:a", "128k",
                        "-movflags", "+faststart", str(dst)], check=True)
        return dst
    except Exception as e:
        print("  x no pude comprimir para Telegram:", e, flush=True); return video


def enviar(video, texto="", bot="clips"):
    """Devuelve True si llegó. Nunca rompe el robot si Telegram falla."""
    c = _cfg(bot)
    if not c or not c.get("token"): return False
    if not c.get("chat_id") and not buscar_chat_id(bot): return False
    c = _cfg(bot)
    import requests
    video = Path(video)
    if video.stat().st_size >= 49 * 1024 * 1024:  # los bots de Telegram no aceptan más de 50 MB: comprimir una copia
        video = _comprimir(video)
    metodo, campo = ("sendVideo", "video") if video.stat().st_size < 49 * 1024 * 1024 else ("sendDocument", "document")
    try:
        with open(video, "rb") as f:
            r = requests.post(f"https://api.telegram.org/bot{c['token']}/{metodo}",
                              data={"chat_id": c["chat_id"], "caption": texto[:1000], "supports_streaming": "true"},
                              files={campo: (video.name, f, "video/mp4")}, timeout=600)
        print("  → Telegram ✓" if r.ok else f"  → Telegram x {r.status_code} {r.text[:120]}", flush=True)
        return r.ok
    except Exception as e:
        print("  → Telegram x", e, flush=True); return False


if __name__ == "__main__":
    args = sys.argv[1:]
    bot = "clips"
    if "--bot" in args:
        i = args.index("--bot"); bot = args[i + 1]; del args[i:i + 2]
    if args: enviar(args[0], args[1] if len(args) > 1 else "", bot)
    else: buscar_chat_id(bot)
