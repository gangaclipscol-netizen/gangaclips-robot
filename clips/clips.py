"""Fábrica de Clips: toma el último live de cada streamer (Kick), la IA lee la transcripción completa,
escoge los mejores momentos y deja clips verticales con subtítulos listos para TikTok / Shorts / Reels.

Uso:
  py -3.13 clips.py                     -> todos los canales de canales.json (solo lives nuevos)
  py -3.13 clips.py --canal westcol --n 3 [--vod 0]   -> prueba: 3 clips del último live (vod 0 = el más reciente)
Salida: Escritorio/Clips/<Streamer>/  (+ "Para publicar.txt"). NO publica nada.
Necesita GEMINI_API_KEY (variable de usuario de Windows).
"""
import json, os, re, sys, subprocess, shutil, urllib.request, argparse, datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent / "gta6"))
import video_con_voz as V  # palabras(), subtitulos(), texto_ajustado(), quitar_emoji()
sys.path.insert(0, str(AQUI.parent))
import telegram_envio as TG  # manda cada clip al Telegram de Sebastián

def _escritorio():
    """El Escritorio REAL de Windows (en el HP es D:/Desktop, no ~/Desktop)."""
    try:
        import ctypes
        b = ctypes.create_unicode_buffer(260); ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0, b)
        if b.value: return Path(b.value)
    except Exception: pass
    return Path.home() / "Desktop"


SALIDA = Path(os.environ.get("ADFLOW_SALIDA", _escritorio() / "Clips"))
BASE = Path("D:/AdFlow-Escaneo" if Path("D:/").exists() else "C:/AdFlow-Escaneo")  # el PC de Nicolás no tiene disco D:
TMP = Path(os.environ.get("ADFLOW_TMP", BASE / "clips-tmp"))
# cada cuenta con su lista de streamers, su estado y su bot de Telegram (ej. GangaClips de Nicolás)
CANALES = os.environ.get("ADFLOW_CANALES")  # si está, NO lee los streamers de la app
ESTADO = Path(os.environ.get("ADFLOW_ESTADO", AQUI / "estado.json"))
BOT = os.environ.get("ADFLOW_BOT", "clips")
IG = os.environ.get("ADFLOW_IG")  # usuario de Instagram (de ~/.adflow-meta.json) donde publicar solo los clips de Kick
MAX_PUB = int(os.environ.get("ADFLOW_MAX_PUB", "6"))  # tope de publicaciones por corrida (no llenar la cuenta de golpe)
PUBLICADOS = [0]
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
W, H = 1080, 1920
FONTSDIR = os.environ.get("ADFLOW_FONTSDIR", r"C\\:/Windows/Fonts")  # en la nube (Linux): /usr/share/fonts
NO_CATEG = {"Slots & Casino", "Casino", "Slots", "Gambling"}  # YouTube/Facebook no pagan (y castigan) casino


def get(url, binario=False):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
    return r if binario else r.decode("utf-8", "replace")


def sh(args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    if r.returncode:
        print(r.stderr[-1200:]); raise RuntimeError("ffmpeg falló")
    return r.stdout


# ---------- 1. lives ----------
def lives(slug):
    return json.loads(get(f"https://kick.com/api/v2/channels/{slug}/videos"))


def transcripcion(base):
    """Subtítulos automáticos en español del live -> [(segundo_del_video, texto)] sin repetidos."""
    pl = get(base + "/subtitles/es/playlist.m3u8")
    segs = [l for l in pl.splitlines() if l and not l.startswith("#")]
    def uno(s):
        try: return get(base + "/subtitles/es/" + s)
        except Exception: return ""
    with ThreadPoolExecutor(16) as ex:
        vtts = list(ex.map(uno, segs))
    out, ultimo = [], ""
    for v in vtts:
        m = re.search(r"MPEGTS:(\d+)", v); off = int(m.group(1)) / 90000 if m else 0
        for a, txt in re.findall(r"(\d+:\d\d:\d\d\.\d+) --> [^\n]+\n(.+?)(?:\n\n|\Z)", v, re.S):
            h, mi, se = a.split(":"); t = int(h) * 3600 + int(mi) * 60 + float(se) - off
            txt = " ".join(txt.split())
            if txt and txt != ultimo:
                out.append((max(0.0, t), txt)); ultimo = txt
    return out


# Reglas de cada campaña (por nombre del creador, en minúsculas)
REGLAS = {}  # reglas por cuenta de campaña (GangaClips no tiene: clips libres de Kick)


def regla(nombre):
    return next((v for k, v in REGLAS.items() if k in nombre.lower()), {})


YT_CACHE = Path(os.environ["ADFLOW_YT_CACHE"]) if os.environ.get("ADFLOW_YT_CACHE") else None  # nube: videos ya bajados por el PC


def yt_videos(canal_url, n=8):
    if YT_CACHE and (YT_CACHE / "lista.json").exists():
        return json.loads((YT_CACHE / "lista.json").read_text(encoding="utf-8"))[:n]
    out = subprocess.run(["yt-dlp", "--flat-playlist", "--playlist-end", str(n), "--print", "%(id)s	%(duration)s	%(title)s",
                          canal_url.rstrip("/") + "/videos"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    vids = []
    for l in out.splitlines():
        p = l.split("	")
        if len(p) == 3 and p[1] not in ("NA", "None") and float(p[1]) > 120: vids.append({"id": p[0], "dur": float(p[1]), "titulo": p[2]})
    return vids


def yt_transcripcion(vid, t):
    t.mkdir(parents=True, exist_ok=True)
    if YT_CACHE:
        for x in YT_CACHE.glob(f"{vid}.*json3"): shutil.copy(x, t / x.name)
    subprocess.run(["yt-dlp", "--skip-download", "--write-auto-subs", "--write-subs", "--sub-langs", "es-orig,es", "--sub-format", "json3",
                    "-o", str(t / vid), "https://www.youtube.com/watch?v=" + vid], capture_output=True)
    f = next((x for x in [t / f"{vid}.es-orig.json3", t / f"{vid}.es.json3"] if x.exists()), None)
    if not f: return yt_whisper(vid, t)
    out = []
    for e in json.loads(f.read_text(encoding="utf-8")).get("events", []):
        txt = " ".join("".join(s.get("utf8", "") for s in e.get("segs") or []).split())
        if txt: out.append((e.get("tStartMs", 0) / 1000, txt))
    return out


def yt_whisper(vid, t):
    """Respaldo cuando YouTube no da subtítulos: baja solo el audio y lo transcribe con Whisper de Groq (gratis).
    Trozos de 10 min a 32 kbps mono (Groq acepta hasta 25 MB por archivo)."""
    k = os.environ.get("GROQ_API_KEY")
    if not k: return []
    import requests
    a = t / f"{vid}.audio.m4a"
    if YT_CACHE and (YT_CACHE / f"{vid}.mp4").exists(): a = YT_CACHE / f"{vid}.mp4"  # nube: el video lo subió el PC
    if not a.exists():
        subprocess.run(["yt-dlp", "-f", "ba[ext=m4a]/ba", "-o", str(a), "https://www.youtube.com/watch?v=" + vid], capture_output=True)
    if not a.exists(): return []
    out, ini, paso = [], 0, 600
    while True:
        trozo = t / f"{vid}.w{ini}.mp3"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(ini), "-t", str(paso), "-i", str(a), "-ac", "1", "-b:a", "32k", str(trozo)])
        if not trozo.exists() or trozo.stat().st_size < 2000: break
        try:
            with open(trozo, "rb") as fh:
                r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions", headers={"Authorization": "Bearer " + k},
                                  files={"file": fh}, timeout=300,
                                  data={"model": "whisper-large-v3", "language": "es", "response_format": "verbose_json", "temperature": "0"}).json()
        except Exception as e:
            print("  whisper falló:", e, flush=True); break
        segs = r.get("segments") or []
        out += [(ini + x["start"], " ".join(x["text"].split())) for x in segs if x.get("text", "").strip()]
        if not segs: break
        ini += paso
    print("  (transcrito con Whisper de Groq)", flush=True)
    return out


def yt_bajar(vid, t):
    dst = t / f"{vid}.mp4"
    if YT_CACHE and (YT_CACHE / f"{vid}.mp4").exists(): return str(YT_CACHE / f"{vid}.mp4")
    if not dst.exists():
        sh(["yt-dlp", "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]", "--merge-output-format", "mp4",
            "-o", str(dst), "https://www.youtube.com/watch?v=" + vid])
    return str(dst)


# ---------- 2. la IA escoge ----------
def escoger(nombre, titulo_live, trans, n, extra="", tono="español colombiano"):
    lineas = "\n".join(f"[{int(t)}] {x}" for t, x in trans)
    prompt = f"""Eres editor de clips virales de streamers latinos. Esta es la transcripción automática (puede tener errores)
del live "{titulo_live}" de {nombre}. Cada línea empieza con [segundo del video].

Escoge los {n} MEJORES momentos para clips de TikTok/YouTube Shorts/Reels:
- Que se entiendan solos, sin contexto (historia completa, chiste con remate, reacción fuerte, polémica, consejo, momento épico o emotivo).
- Duración entre 25 y 70 segundos. Que empiece justo antes de lo bueno y termine después del remate.
- EVITA: cuando suena música o canta (copyright), casino/apuestas, odio, contenido sexual, datos personales de terceros.
- No repitas el mismo tema.
{extra}

Responde SOLO un JSON: lista de objetos con
"inicio" (segundo entero), "fin" (segundo entero), "titulo" (gancho para publicar, máx 70 caracteres, en {tono}),
"gancho" (texto corto en pantalla, máx 6 palabras, MAYÚSCULAS), "por_que" (una frase), "hashtags" (5, incluye #{nombre.lower()}), "puntaje" (1-10).
Ordena de mejor a peor.

TRANSCRIPCIÓN:
{lineas}"""
    key = os.environ.get("GEMINI_API_KEY")
    if not key: raise SystemExit("Falta GEMINI_API_KEY")
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4}}
    import time
    r = None
    for intento, modelo in enumerate(["gemini-flash-latest", "gemini-flash-latest", "gemini-flash-lite-latest",
                                      "gemini-flash-latest", "gemini-flash-lite-latest"]):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={key}"
        req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=300).read()); break
        except Exception as e:  # 503/429 = Gemini saturado: esperar y probar de nuevo
            print(f"  IA ocupada ({modelo}): {e}; reintento", flush=True); time.sleep(30 * (intento + 1))
    if r: txt = r["candidates"][0]["content"]["parts"][0]["text"]
    else:  # respaldo: Groq (gratis) cuando Gemini está sin cupo; la transcripción se recorta a lo que le cabe
        gk = os.environ.get("GROQ_API_KEY")
        if not gk: raise RuntimeError("Gemini no respondió (y no hay GROQ_API_KEY de respaldo)")
        print("  → respaldo: Groq", flush=True)
        corto = prompt if len(prompt) < 24000 else prompt[:24000] + "\n(transcripción recortada)\nResponde SOLO el JSON pedido."
        req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", json.dumps({
            "model": "openai/gpt-oss-120b", "temperature": 0.4,
            "messages": [{"role": "user", "content": corto}]}).encode(),
            {"Content-Type": "application/json", "Authorization": "Bearer " + gk, "User-Agent": "adflow-clips"})
        txt = json.loads(urllib.request.urlopen(req, timeout=300).read())["choices"][0]["message"]["content"]
    clips = json.loads(re.search(r"\[.*\]", txt, re.S).group(0))
    return [c for c in clips if 15 <= c["fin"] - c["inicio"] <= 90][:n]


# ---------- 3. cortar y editar ----------
def overlay(gancho, credito, dst, y_video, h_video):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    f, ls = V.texto_ajustado(d, V.quitar_emoji(gancho).upper(), V.F_BLACK, W - 120, 92, 50)
    alto = len(ls) * int(f.size * 1.12)
    y = max(150, y_video - 40 - alto)
    for l in ls:
        tw = d.textlength(l, font=f)
        d.text(((W - tw) / 2, y), l, font=f, fill="white", stroke_width=8, stroke_fill="black"); y += int(f.size * 1.12)
    fc = ImageFont.truetype(V.F_BLACK, 30)
    tw = d.textlength(credito, font=fc)
    yc = y_video + h_video + 18
    d.rounded_rectangle(((W - tw) / 2 - 18, yc, (W + tw) / 2 + 18, yc + 50), 12, fill=(83, 252, 24, 235))
    d.text(((W - tw) / 2, yc + 6), credito, font=fc, fill="black")
    im.save(dst)


def hacer_clip(c, stream, nombre, slug, t, credito=None):
    t.mkdir(parents=True, exist_ok=True)
    a, b = c["inicio"] - 1.0, c["fin"] + 1.5
    crudo = t / "crudo.mp4"
    sh(["ffmpeg", "-y", "-ss", f"{a:.2f}", "-i", stream, "-t", f"{b - a:.2f}", "-r", "30", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(crudo)])
    pals = V.palabras(crudo)
    # cortar en límites de palabras: arranca en la primera palabra y termina tras la última
    ini = max(0.0, pals[0][1] - 0.25) if pals else 0.0
    fin = min(V.duracion(crudo), pals[-1][2] + 0.6) if pals else V.duracion(crudo)
    pals = [(w, x - ini, y - ini) for w, x, y in pals]
    V.subtitulos(pals, t / "subs.ass")
    # subir los subtítulos debajo del video
    s = (t / "subs.ass").read_text(encoding="utf-8").replace(",2,70,70,560,1", ",2,70,70,640,1")
    (t / "subs.ass").write_text(s, encoding="utf-8")
    hv = 1150; yv = 400  # pantalla completa vertical (recorte al centro, donde está el streamer)
    overlay(c["gancho"], credito or f"{nombre} en Kick  ·  kick.com/{slug}", t / "ov.png", yv, hv)
    titulo = re.sub(r'[\\/:*?"<>|]', "", c["titulo"])[:70].strip()
    dst = SALIDA / nombre / f"{datetime.date.today():%Y-%m-%d} - {titulo}.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    fc = (f"[0:v]crop=ih*9/16:ih:(iw-ow)/2:0,scale={W}:{H},setsar=1[x];"
          f"[x][1:v]overlay=0:0,subtitles=subs.ass:fontsdir={FONTSDIR},format=yuv420p[v]")
    sh(["ffmpeg", "-y", "-ss", f"{ini:.2f}", "-to", f"{fin:.2f}", "-i", "crudo.mp4", "-i", "ov.png", "-filter_complex", fc,
        "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
        "-af", "loudnorm=I=-14:TP=-1.5", "-movflags", "+faststart", str(dst)], cwd=t)
    return dst


# ---------- TikTok por Zernio (antes Late): plan gratis = 2 cuentas, API incluida ----------
def publicar_tiktok_zernio(f, usuario, texto):
    return publicar_zernio(f, "tiktok", usuario, texto)


def publicar_zernio(f, plataforma, usuario, texto, titulo=""):
    """Sube el clip a Zernio y lo publica en el TikTok `usuario`. Llave en ~/.adflow-zernio.json o ZERNIO_KEY. Devuelve URL o None."""
    import requests, uuid
    cfg = Path.home() / ".adflow-zernio.json"
    k = os.environ.get("ZERNIO_KEY") or (json.loads(cfg.read_text())["key"] if cfg.exists() else None)
    if not k: return None
    h = {"Authorization": "Bearer " + k}; B = "https://api.zernio.com/v1"
    try:
        cuentas = requests.get(B + "/accounts", headers=h, timeout=30).json().get("accounts", [])
        cta = next((c for c in cuentas if c.get("platform") == plataforma and usuario.lower() in str(c.get("username", "")).lower()), None)
        if not cta: print(f"  x Zernio: no está conectado {plataforma}", usuario, flush=True); return None
        pre = requests.post(B + "/media/presign", headers=h, json={"filename": Path(f).name.encode("ascii", "ignore").decode() or "clip.mp4",
                                                                    "contentType": "video/mp4"}, timeout=30).json()
        with open(f, "rb") as v:
            requests.put(pre["uploadUrl"], data=v, headers={"Content-Type": "video/mp4"}, timeout=900).raise_for_status()
        extra = ({"tiktokSettings": {"privacyLevel": "PUBLIC_TO_EVERYONE", "allowComment": True, "allowDuet": True, "allowStitch": True,
                                      "contentPreviewConfirmed": True, "expressConsentGiven": True}} if plataforma == "tiktok" else
                 {"title": (titulo or texto.splitlines()[-2] if len(texto.splitlines()) > 1 else texto)[:95], "visibility": "public", "madeForKids": False})
        cuerpo = {"content": texto[:2200], "mediaItems": [{"type": "video", "url": pre["publicUrl"]}], "publishNow": True,
                  "platforms": [{"platform": plataforma, "accountId": cta["_id"], "platformSpecificData": extra}]}
        if plataforma == "youtube": cuerpo["title"] = extra["title"]
        r = requests.post(B + "/posts", headers={**h, "Idempotency-Key": str(uuid.uuid4())}, json=cuerpo, timeout=900).json()
        post = r.get("post", r)
        url = next((x.get("platformPostUrl") for x in post.get("platforms", []) if x.get("platformPostUrl")), None)
        print(f"  🎵 {plataforma} (Zernio):", url or str(r)[:300], flush=True)
        return url or ("pendiente" if post.get("status") in ("published", "publishing", "scheduled") else None)
    except Exception as e:
        print(f"  x {plataforma} (Zernio):", e, flush=True); return None


# ---------- publicar solo con Upload-Post (YouTube / Instagram conectados al perfil) ----------
def publicar(f, perfil, titulo, descripcion, sin=()):
    """Publica en las redes conectadas al perfil de Upload-Post (menos las de `sin`). Devuelve {red: url}. Nunca rompe el robot."""
    cfg = Path.home() / ".adflow-uploadpost.json"
    if not cfg.exists(): return {}
    import requests
    h = {"Authorization": "Apikey " + json.loads(cfg.read_text())["key"]}
    try:
        cuentas = next(x for x in requests.get("https://api.upload-post.com/api/uploadposts/users", headers=h, timeout=30).json()["profiles"]
                       if x["username"] == perfil)["social_accounts"]
        redes = [r for r in ("youtube", "instagram") if isinstance(cuentas.get(r), dict) and r not in sin]
        if not redes: return {}
        def subir_up(redes):
            with open(f, "rb") as v:
                return requests.post("https://api.upload-post.com/api/upload", headers=h, timeout=900,
                                     data={"user": perfil, "platform[]": redes, "title": titulo[:95], "description": descripcion, "tiktok_title": descripcion[:2200],
                                           "privacyStatus": "public", "media_type": "REELS"},
                                     files={"video": ("clip.mp4", v, "video/mp4")}).json()
        r = subir_up(redes)
        if r.get("error_code") == "plan_required" and "tiktok" in redes:  # plan gratis: TikTok no; que no tumbe YouTube
            print("  (Upload-Post gratis no publica TikTok; sigo sin TikTok)", flush=True)
            redes = [x for x in redes if x != "tiktok"]
            r = subir_up(redes) if redes else {}
        urls = {k: x.get("url") for k, x in (r.get("results") or {}).items() if x.get("success") and x.get("url")}
        rid = r.get("request_id") or (re.search(r"request_id=(\w+)", r.get("message", "")) or [None, None])[1]
        if not urls and rid:  # subida larga: Upload-Post la termina por detrás; esperar los enlaces (máx 10 min)
            import time
            for _ in range(40):
                time.sleep(15)
                s = requests.get("https://api.upload-post.com/api/uploadposts/status", params={"request_id": rid}, headers=h, timeout=30).json()
                if s.get("status") == "completed":
                    urls = {x["platform"]: x["post_url"] for x in s.get("results", []) if x.get("success") and x.get("post_url")}
                    break
        print("  📤 publicado:", urls or str(r)[:300], flush=True)
        return urls
    except Exception as e:
        print("  x no se pudo publicar:", e, flush=True); return {}


# ---------- subir a la app (clips.adflowai.workers.dev) ----------
APP = "https://clips.adflowai.workers.dev"
TOKEN_F = Path.home() / ".adflow-clips.token"   # token del bot (NO va al repo)


def subir(f, c, nombre, slug, origen):
    if not TOKEN_F.exists(): return
    import requests
    meta = {"streamer": nombre, "titulo": c["titulo"], "gancho": c["gancho"], "por_que": c.get("por_que", ""),
            "hashtags": c.get("hashtags", []), "puntaje": c.get("puntaje"), "duracion": V.duracion(f),
            "credito": f"{nombre} en vivo en kick.com/{slug}", "origen": origen}
    with open(f, "rb") as v:
        r = requests.post(APP + "/api/clips", headers={"Authorization": "Bearer " + TOKEN_F.read_text().strip()},
                          files={"video": ("clip.mp4", v, "video/mp4")}, data={"meta": json.dumps(meta, ensure_ascii=False)}, timeout=300)
    print("  ↑ subido a la app" if r.ok else f"  x no subió: {r.status_code} {r.text[:150]}", flush=True)
    try: return r.json() if r.ok else {}
    except Exception: return {}


# ---------- Instagram GRATIS con la API oficial de Meta (permiso de página que no vence) ----------
def _subir_directo(url, tok, archivo):
    """Sube el MP4 a Meta en una sola petición (sin URL pública). Sirve para Instagram (rupload) y Reels de Facebook."""
    import requests
    datos = Path(archivo).read_bytes()
    return requests.post(url, headers={"Authorization": "OAuth " + tok, "offset": "0", "file_size": str(len(datos))},
                         data=datos, timeout=600).json()


def publicar_ig(video_publico, caption, usuario, archivo=None):
    """Publica un Reel en la cuenta `usuario` (ver ~/.adflow-meta.json). Devuelve la URL del reel o None.
    video_publico = URL pública del MP4, o archivo = MP4 local (se sube directo, p. ej. desde GitHub Actions)."""
    cfg = Path.home() / ".adflow-meta.json"
    if not (cfg.exists() and (video_publico or archivo)): return None
    import requests, time
    c = json.loads(cfg.read_bytes().decode("utf-8", errors="replace")).get("cuentas", {}).get(usuario)
    if not c: return None
    G, tok, ig = "https://graph.facebook.com/v21.0", c["page_token"], c["ig_id"]
    try:
        datos = {"media_type": "REELS", "caption": caption[:2200], "share_to_feed": "true", "access_token": tok}
        datos.update({"upload_type": "resumable"} if archivo else {"video_url": video_publico})
        r = requests.post(f"{G}/{ig}/media", data=datos, timeout=60).json()
        if "id" not in r: print("  x Instagram:", r, flush=True); return None
        if archivo:
            u = _subir_directo(r.get("uri") or f"https://rupload.facebook.com/ig-api-upload/v21.0/{r['id']}", tok, archivo)
            if not u.get("success"): print("  x Instagram subida:", u, flush=True); return None
        for _ in range(40):  # Meta procesa el video (1-5 min)
            time.sleep(10)
            s = requests.get(f"{G}/{r['id']}", params={"fields": "status_code,status", "access_token": tok}, timeout=30).json()
            if s.get("status_code") == "FINISHED": break
            if s.get("status_code") == "ERROR": print("  x Instagram procesando:", s, flush=True); return None
        p = requests.post(f"{G}/{ig}/media_publish", data={"creation_id": r["id"], "access_token": tok}, timeout=60).json()
        if "id" not in p: print("  x Instagram publicar:", p, flush=True); return None
        url = requests.get(f"{G}/{p['id']}", params={"fields": "permalink", "access_token": tok}, timeout=30).json().get("permalink")
        print("  📸 Instagram (Meta):", url, flush=True)
        return url
    except Exception as e:
        print("  x Instagram:", e, flush=True); return None


def publicar_fb(video_publico, descripcion, usuario, archivo=None):
    """Publica un Reel en la PÁGINA de Facebook unida al Instagram `usuario` (permiso pages_manage_posts). URL o None.
    video_publico = URL pública del MP4, o archivo = MP4 local (se sube directo)."""
    cfg = Path.home() / ".adflow-meta.json"
    if not (cfg.exists() and (video_publico or archivo)): return None
    import requests, time
    c = json.loads(cfg.read_bytes().decode("utf-8", errors="replace")).get("cuentas", {}).get(usuario)
    if not c: return None
    G, tok = "https://graph.facebook.com/v21.0", c["page_token"]
    try:
        pid = c.get("page_id") or requests.get(f"{G}/me", params={"access_token": tok}, timeout=30).json()["id"]
        s = requests.post(f"{G}/{pid}/video_reels", data={"upload_phase": "start", "access_token": tok}, timeout=60).json()
        if "video_id" not in s: print("  x Facebook:", s, flush=True); return None
        u = (_subir_directo(s["upload_url"], tok, archivo) if archivo else
             requests.post(s["upload_url"], headers={"Authorization": "OAuth " + tok, "file_url": video_publico}, timeout=300).json())
        if not u.get("success"): print("  x Facebook subida:", u, flush=True); return None
        f = requests.post(f"{G}/{pid}/video_reels", data={"upload_phase": "finish", "video_id": s["video_id"], "video_state": "PUBLISHED",
                                                        "description": descripcion[:2200], "access_token": tok}, timeout=60).json()
        if not f.get("success"): print("  x Facebook publicar:", f, flush=True); return None
        url = f"https://www.facebook.com/reel/{s['video_id']}"
        print("  📘 Facebook (Meta):", url, flush=True)
        return url
    except Exception as e:
        print("  x Facebook:", e, flush=True); return None


def streamers_de_la_app():
    if not TOKEN_F.exists(): return None
    import requests
    try:
        r = requests.get(APP + "/api/streamers", headers={"Authorization": "Bearer " + TOKEN_F.read_text().strip()}, timeout=30)
        return [{"nombre": x["nombre"], x["fuente"]: x["canal"], "clips_por_live": x["clips_por_live"]}
                for x in r.json() if x["activo"] and x["fuente"] in ("kick", "youtube")]
    except Exception as e:
        print("no pude leer la app:", e); return None


def publicar_clip(f, c, nombre, r, app):
    """Publica un clip ya armado en todas las redes de la campaña, lo envía a la campaña y avisa por Telegram."""
    primera = (r.get("primera", "") + "\n") if r.get("primera") else ""
    tags = ' '.join(c.get('hashtags', []))
    texto = f"{primera}{c['titulo']}\n{tags}"
    ig = publicar_ig(app.get("publico"), texto, r["instagram"]) if r.get("instagram") else None
    fb = publicar_fb(app.get("publico"), texto, r["instagram"], archivo=f) if r.get("instagram") else None  # su página de Facebook
    texto2 = f"{r['primera_otras']}\n{c['titulo']}\n{tags}" if r.get("primera_otras") else texto  # TikTok/YouTube piden otra 1.ª línea
    urls = publicar(f, r["uploadpost"], c["titulo"], texto2, sin=("instagram",) if r.get("instagram") else ()) if r.get("uploadpost") else {}
    if "tiktok" not in urls and r.get("tiktok"):
        tt = publicar_zernio(f, "tiktok", r["tiktok"], texto2)
        if tt and tt != "pendiente": urls["tiktok"] = tt
    if "youtube" not in urls and r.get("youtube_zernio"):  # Upload-Post gratis = 10 subidas/mes; Zernio no tiene tope
        yt = publicar_zernio(f, "youtube", r["youtube_zernio"], texto2, c["titulo"])
        if yt: urls["youtube"] = yt if yt != "pendiente" else "https://www.youtube.com/@" + r["youtube_zernio"]
    if ig: urls["instagram"] = ig
    if fb: urls["facebook"] = fb
    if "youtube" not in urls and r.get("youtube_canal"):  # sin cupo de Upload-Post: a la carpeta para subir a mano
        try:
            import carpeta_youtube as CY
            CY.agregar(f, f"{c['titulo'][:80]} {' '.join(c.get('hashtags', [])[:2])}", texto, r["youtube_canal"])
            print("  → carpeta 'pa subir a youtube'", flush=True)
        except Exception as e:
            print("  x carpeta:", e, flush=True)
    if urls:
        res = {}
        if r.get("enviar"):  # enviar solo a la campaña (Content Rewards) dentro de los 30 min
            try:
                import enviar_campana as EC
                for k, u in urls.items():
                    if k != "facebook": res[k] = EC.enviar(r["enviar"], u)  # la campaña no acepta Facebook
            except Exception as e:
                res = {k: (False, str(e)[:150]) for k in urls}
        print("  📨 campaña:", res, flush=True)
        lineas = [f"{k}: {u}\n   {'✅ ENVIADO a la campaña' if res.get(k, (False,))[0] else '⚠️ NO enviado (' + res.get(k, (0, 'sin intento'))[1] + ') → envíalo tú YA'}"
                  if k != "facebook" else f"facebook: {u}" for k, u in urls.items()]
        falta = [x for x in ("YouTube", "TikTok") if x.lower() not in urls]
        TG.enviar(f, f"✅ {nombre}: publicado por el robot\n" + "\n".join(lineas) + f"\n\nCampaña: {r.get('enviar', '')}"
                  + (f"\n\n📤 SUBIR A MANO a {' y '.join(falta)} (y enviar el enlace a la campaña en menos de 30 min):\n"
                     f"📌 TÍTULO: {c['titulo'][:90]} {' '.join(c.get('hashtags', [])[:2])}\n📝 DESCRIPCIÓN:\n{texto}" if falta else ""))
    else:
        TG.enviar(f, f"✂️ {nombre}\n⏱️ Al publicar, envía el enlace a la campaña en MENOS DE 30 MIN\n\n"
                     f"📌 TÍTULO (máx 100):\n{c['titulo'][:95]}\n\n📝 DESCRIPCIÓN:\n{primera}{c['titulo']}\n{tags}")

COLA = Path(os.environ.get("ADFLOW_COLA", str(SALIDA / "_cola")))  # clips hechos esperando que la campaña nos apruebe


def encolar(f, c, nombre, app):
    """Solicitud pendiente: guarda el clip SIN publicar (si se publica antes, la campaña ya no lo acepta)."""
    d = COLA / nombre; d.mkdir(parents=True, exist_ok=True)
    dst = d / Path(f).name; shutil.copy(f, dst)
    (d / (dst.stem + ".json")).write_text(json.dumps({"c": c, "app": app}, ensure_ascii=False), encoding="utf-8")
    print(f"  ⏸️ en cola (campaña sin aprobar): {dst.name}", flush=True)
    TG.texto(f"⏸️ {nombre}: clip guardado SIN publicar (la campaña aún no nos aprueba). Hay {len(list(d.glob('*.mp4')))} en cola.\n{c['titulo']}")


def publicar_cola(nombre, r, n):
    """Campaña aprobada: publica (y envía en seguida) hasta n clips de la cola. Devuelve cuántos publicó."""
    d = COLA / nombre; hechos = 0
    for f in sorted(d.glob("*.mp4"))[:n]:
        j = d / (f.stem + ".json")
        meta = json.loads(j.read_text(encoding="utf-8")) if j.exists() else {"c": {"titulo": f.stem, "hashtags": []}, "app": {}}
        print(f"  ▶️ de la cola: {f.name}", flush=True)
        publicar_clip(f, meta["c"], nombre, r, meta.get("app") or {})
        f.unlink(missing_ok=True); j.unlink(missing_ok=True); hechos += 1
    return hechos


# ---------- principal ----------
def procesar_youtube(canal, n, vod_idx=None):
    nombre, url = canal["nombre"], canal["youtube"]
    r = regla(nombre); clave = "yt:" + url
    est = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {}
    hechos = set(est.get(clave, []))
    aprobado = True
    if r.get("enviar") and r.get("esperar_aprobacion"):
        import enviar_campana as EC
        st = EC.estado(r["enviar"]); print("  campaña:", st, flush=True)
        aprobado = st != "pendiente"
        en_cola = len(list((COLA / nombre).glob("*.mp4")))
        if aprobado and en_cola:
            n -= publicar_cola(nombre, r, n)
            if n <= 0: return
        if not aprobado and en_cola >= 12:
            print(f"  cola llena ({en_cola}) esperando aprobación: no gasto más videos", flush=True); return
    vids = yt_videos(url, 40)  # 40: cuando no hay videos nuevos, sigue con los anteriores de su canal
    vids = [vids[vod_idx]] if vod_idx is not None else [v for v in vids if v["id"] not in hechos][:1]
    for v in vids:
        t0 = TMP / ("yt-" + v["id"])
        print(f"video: {v['titulo']} ({v['dur'] / 60:.0f} min)", flush=True)
        trans = yt_transcripcion(v["id"], t0)
        print("  líneas de transcripción:", len(trans), flush=True)
        if not trans: print("  sin subtítulos: YouTube no los dio (¿bloqueo del servidor?); se reintenta otro día", flush=True); continue
        if len(trans) < 30: hechos.add(v["id"]); continue
        elegidos = escoger(nombre, v["titulo"], trans, n, r.get("extra", ""), r.get("tono", "español colombiano"))
        print("  la IA escogió:", len(elegidos), flush=True)
        stream = yt_bajar(v["id"], t0)
        for i, c in enumerate(elegidos):
            t = t0 / str(i); shutil.rmtree(t, ignore_errors=True)
            try:
                f = hacer_clip(c, stream, nombre, "", t, r.get("credito", f"{nombre} · YouTube"))
                print(f"  ✓ {f.name}  [{c.get('puntaje')}] {c.get('por_que', '')}", flush=True)
                origen = f"https://www.youtube.com/watch?v={v['id']}&t={int(c['inicio'])}s"
                app = subir(f, c, nombre, "", origen) or {}
                if aprobado: publicar_clip(f, c, nombre, r, app)
                else: encolar(f, c, nombre, app)
            except Exception as e:
                print("  x clip", i, e, flush=True)
        hechos.add(v["id"])
        shutil.rmtree(t0, ignore_errors=True)
    est[clave] = sorted(hechos)
    ESTADO.write_text(json.dumps(est, indent=1), encoding="utf-8")


def procesar(canal, n, vod_idx=None):
    if "youtube" in canal: return procesar_youtube(canal, n, vod_idx)
    nombre, slug = canal["nombre"], canal["kick"]
    est = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {}
    hechos = set(est.get(slug, []))
    vids = [v for v in lives(slug) if not v.get("is_live")]
    if vod_idx is not None: vids = [vids[vod_idx]]
    else: vids = [v for v in vids[:3] if v["slug"] not in hechos]
    notas = []
    for v in vids:
        cats = {c["name"] for c in v.get("categories") or []}
        if cats & NO_CATEG:
            print("salto (casino):", v["session_title"], flush=True); hechos.add(v["slug"]); continue
        base = v["source"].rsplit("/", 1)[0]
        print(f"live: {v['session_title']} ({v['duration'] / 3.6e6:.1f} h)", flush=True)
        trans = transcripcion(base)
        print("  líneas de transcripción:", len(trans), flush=True)
        if len(trans) < 50: hechos.add(v["slug"]); continue
        elegidos = escoger(nombre, v["session_title"], trans, n)
        print("  la IA escogió:", len(elegidos), flush=True)
        stream = base + "/720p60/playlist.m3u8"
        for i, c in enumerate(elegidos):
            t = TMP / v["slug"] / str(i)
            shutil.rmtree(t, ignore_errors=True)
            try:
                f = hacer_clip(c, stream, nombre, slug, t)
                print(f"  ✓ {f.name}  [{c.get('puntaje')}] {c.get('por_que', '')}", flush=True)
                subir(f, c, nombre, slug, f"https://kick.com/{slug}/videos/{v.get('slug', '')} @ {int(c['inicio'])}s")
                tags = ' '.join(c.get('hashtags', []))
                texto = f"{c['titulo']}\n\n🎥 {nombre} en vivo en kick.com/{slug}\nCuenta de fans · No oficial\n\n{tags}"
                hechas = {}
                if IG and PUBLICADOS[0] < MAX_PUB:  # publicar solo en Instagram + página de Facebook (API de Meta)
                    hechas = {"Instagram": publicar_ig(None, texto, IG, archivo=f), "Facebook": publicar_fb(None, texto, IG, archivo=f)}
                    if any(hechas.values()): PUBLICADOS[0] += 1
                redes = "\n".join(f"✅ {k}: {u}" if u else f"⚠️ {k}: NO se publicó (súbelo tú)" for k, u in hechas.items())
                pendientes = "TikTok, YouTube" + ("" if hechas else ", Instagram, Facebook")
                TG.enviar(f, f"✂️ {nombre}\n\n{texto}\n\n{redes + chr(10) if redes else ''}⚠️ Publicar a mano: {pendientes}", bot=BOT)
                notas.append(f"{f.name}\n{c['titulo']} {' '.join(c.get('hashtags', []))}\n"
                             f"Crédito: {nombre} en vivo en kick.com/{slug}\n")
            except Exception as e:
                print("  x clip", i, e, flush=True)
        hechos.add(v["slug"])
        shutil.rmtree(TMP / v["slug"], ignore_errors=True)
    est[slug] = sorted(hechos)
    ESTADO.write_text(json.dumps(est, indent=1), encoding="utf-8")
    if notas:
        p = SALIDA / nombre / "Para publicar.txt"
        viejo = p.read_text(encoding="utf-8") if p.exists() else ""
        p.write_text("\n".join(notas) + "\n" + viejo, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canal"); ap.add_argument("--n", type=int); ap.add_argument("--vod", type=int)
    a = ap.parse_args()
    # candado: la tarea corre cada 20 min; si la anterior sigue trabajando (menos de 3 h), no arrancar otra
    TMP.mkdir(parents=True, exist_ok=True); lock = TMP / "clips.lock"
    if lock.exists() and datetime.datetime.now().timestamp() - lock.stat().st_mtime < 3 * 3600:
        print("ya hay otro robot trabajando; salgo", flush=True); return
    lock.write_text(str(os.getpid()))
    try: _main(a)
    finally: lock.unlink(missing_ok=True)


def _main(a):
    canales = (json.loads((AQUI / CANALES).read_text(encoding="utf-8")) if CANALES else
               streamers_de_la_app() or json.loads((AQUI / "canales.json").read_text(encoding="utf-8")))
    for c in canales:
        if a.canal and a.canal.lower() not in (c.get("kick") or c.get("youtube") or "").lower() + c["nombre"].lower(): continue
        if c.get("youtube") and not os.environ.get("ADFLOW_NUBE") and not a.canal:
            print(f"{c['nombre']}: lo hace la nube (carlotta.yml); el PC solo le sube los videos", flush=True); continue
        try: procesar(c, a.n or c.get("clips_por_live", 6), a.vod)
        except Exception as e: print("error", c["nombre"], e, flush=True)
    print("listo", flush=True)


if __name__ == "__main__":
    main()
