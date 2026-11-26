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
ESTADO_DIR = Path(os.environ["ADFLOW_ESTADO_DIR"]) if os.environ.get("ADFLOW_ESTADO_DIR") else None  # un archivo por streamer (robots en paralelo)
BOT = os.environ.get("ADFLOW_BOT", "clips")
IG = os.environ.get("ADFLOW_IG")  # usuario de Instagram (de ~/.adflow-meta.json) donde publicar solo los clips de Kick
MAX_PUB = int(os.environ.get("ADFLOW_MAX_PUB", "10"))  # tope de publicaciones por corrida (no llenar la cuenta de golpe)
MAX_DIA = int(os.environ.get("ADFLOW_MAX_DIA", "90"))  # tope diario en IG/FB (Instagram permite 100 en 24 h; el jurado ya deja pocos)
ESPACIO_MIN = int(os.environ.get("ADFLOW_ESPACIO_MIN", "0"))  # minutos mínimos entre una publicación y la siguiente
MIN_PUNTAJE = int(os.environ.get("ADFLOW_MIN_PUNTAJE", "9"))  # solo se hacen los clips que el jurado califica 9-10 (solo los mejores)
TIKTOK_MIN = int(os.environ.get("ADFLOW_TIKTOK_MIN", "9"))  # puntaje mínimo (1-10) para mandar el clip a los borradores de TikTok
PUBLICADOS = [0]
FIJO = [False]  # --n a mano: sacar exactamente esa cantidad
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


def whisper_kick(base, desde=0, max_horas=2.0, trozo=600):
    """Transcripción con Whisper de Groq cuando Kick no tiene subtítulos (canales chicos). Escucha desde `desde` hasta
    el final del live (máx. `max_horas`), en trozos de 10 min de audio liviano. Devuelve [(segundo, texto)]."""
    k = os.environ.get("GROQ_API_KEY")
    if not k: return []
    import requests, tempfile, time
    try:
        pl = get(base + "/160p30/playlist.m3u8")
        total = sum(float(l.split(":")[1].split(",")[0]) for l in pl.splitlines() if l.startswith("#EXTINF:"))
    except Exception as e:
        print("  x Whisper: no pude leer el live:", e, flush=True); return []
    ini = max(0.0, max(desde - 60, total - max_horas * 3600))
    print(f"  sin subtítulos de Kick: escucho con Whisper min {ini / 60:.0f} a {total / 60:.0f}", flush=True)
    out = []
    with tempfile.TemporaryDirectory() as d:
        t = ini
        while t < total - 5:
            mp3 = Path(d) / f"{int(t)}.mp3"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.1f}", "-i", base + "/160p30/playlist.m3u8", "-t", str(trozo),
                            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", str(mp3)], capture_output=True)
            if mp3.exists() and mp3.stat().st_size > 10000:
                for intento in range(4):
                    with open(mp3, "rb") as a:
                        r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions", headers={"Authorization": "Bearer " + k},
                                          files={"file": (mp3.name, a, "audio/mpeg")},
                                          data={"model": "whisper-large-v3-turbo", "language": "es", "response_format": "verbose_json"},
                                          timeout=300)
                    if r.status_code == 429:  # cupo por hora de Groq: esperar y seguir
                        time.sleep(60 * (intento + 1)); continue
                    for s in r.json().get("segments", []):
                        txt = " ".join(s.get("text", "").split())
                        if txt: out.append((t + float(s["start"]), txt))
                    break
            t += trozo
    print(f"  Whisper: {len(out)} frases", flush=True)
    return out


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
        # Los tiempos de Kick ya son los del video (00:18:30 cae en el segundo 1110 del live). El MPEGTS del encabezado
        # es el arranque del video (62 s) y NO hay que restarlo: restarlo cortaba todos los clips 1 minuto antes.
        m = re.search(r"LOCAL:(\d+):(\d\d):(\d\d\.\d+)", v)
        off = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0
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
# Lo que hace que un clip de streamer se vuelva viral en TikTok/Reels/Shorts (lo que miden: % que se queda a los 3 s,
# tiempo visto, % que lo ve completo o lo repite, compartidos). Se usa al escoger y en el jurado final.
CRITERIO_VIRAL = """CÓMO SE CALIFICA (sé MUY exigente: la mayoría de momentos de un live NO sirven):
- GANCHO (lo más importante): en los primeros 1-2 segundos ya pasa algo o se dice una frase que obliga a quedarse
  (pregunta, confesión, grito, algo absurdo, conflicto). Nada de saludos, "bueno", explicaciones ni silencios al inicio.
- REMATE: termina en un punto alto (el chiste, la reacción, la respuesta, el giro). Si se diluye, no sirve.
- SE ENTIENDE SOLO: alguien que nunca ha visto al streamer lo entiende sin contexto.
- EMOCIÓN FUERTE: risa de verdad, sorpresa, rabia, ternura, "no puede ser", polémica sana. Lo "interesante a medias" no pasa.
- RETENCIÓN: sin partes muertas; 20 a 45 segundos es lo ideal (más largo solo si la historia lo vale).
- REPETIBLE/COMPARTIBLE: dan ganas de verlo otra vez o mandárselo a alguien.
- El chat del stream es la mejor prueba: si explotó (muchos mensajes, risas KEKW/jaja, OOOO/NOOO), el momento pegó.
Puntaje 1-10: 10 = se vuelve viral; 9 = la gente lo compartiría; 8 o menos = NO sirve (solo se publican 9 y 10)."""


def chat_picos(slug, v, desde, hasta, paso=20):
    """Mide el chat de Kick a lo largo del live: mensajes por minuto y % de risas/sorpresa cada `paso` segundos.
    Devuelve texto con los picos (segundo del video) para la IA, o "" si no se pudo."""
    try:
        cid = json.loads(get(f"https://kick.com/api/v2/channels/{slug}"))["id"]
        ini = datetime.datetime.strptime(v["start_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    except Exception as e:
        print("  x chat: no pude leer el canal:", e, flush=True); return ""
    RISA = re.compile(r"KEKW|LUL|OMEGALUL|ICANT|LMAO|kek|ja{0,1}ja|JAJ|xd|😂|🤣|💀", re.I)
    WOW = re.compile(r"OOOO|NOOO|WOW|Pog|😱|😳|🔥|HYPE|CLAP", re.I)
    def uno(t):
        cuando = (ini + datetime.timedelta(seconds=t)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        try:
            m = json.loads(get(f"https://kick.com/api/v2/channels/{cid}/messages?start_time={cuando}"))["data"]["messages"]
        except Exception:
            return None
        if len(m) < 3: return (t, len(m), 0.0, 0.0)
        hs = sorted(datetime.datetime.fromisoformat(x["created_at"].replace("Z", "+00:00")) for x in m)
        seg = max(1.0, (hs[-1] - hs[0]).total_seconds())
        txt = [x.get("content") or "" for x in m]
        return (t, len(m) / seg * 60, sum(bool(RISA.search(s)) for s in txt) / len(m), sum(bool(WOW.search(s)) for s in txt) / len(m))
    with ThreadPoolExecutor(12) as ex:
        datos = [d for d in ex.map(uno, range(int(desde), int(hasta), paso)) if d]
    if len(datos) < 5: return ""
    # Kick da la hora de los mensajes al segundo: el ritmo solo distingue chat lento/rápido; lo que más dice es el
    # % de risas y de sorpresa (KEKW, jaja, OOOO, NOOO...). Ritmo normal = mediana del live.
    normal = sorted(d[1] for d in datos)[len(datos) // 2] or 1
    nota = lambda d: (d[2] + d[3]) * min(2.0, d[1] / normal)
    puntos = [d for d in sorted(datos, key=nota, reverse=True)[:15] if d[2] + d[3] >= 0.25]
    if not puntos: return ""
    lineas = [f"[{t}] {ri:.0%} risas, {w:.0%} sorpresa/hype" + (" · chat muy rápido" if r > normal * 1.3 else "")
              for t, r, ri, w in sorted(puntos)]
    print(f"  chat: {len(datos)} muestras, picos en {[p[0] for p in sorted(puntos)][:8]}...", flush=True)
    return ("PICOS DEL CHAT (el chat reacciona 3-20 s DESPUÉS de lo que pasa en el video; un pico con muchas risas casi\n"
            "siempre marca un clip bueno, pero igual tiene que entenderse solo):\n" + "\n".join(lineas))


def escoger(nombre, titulo_live, trans, n, extra="", tono="español colombiano", chat=""):
    lineas = "\n".join(f"[{int(t)}] {x}" for t, x in trans)
    prompt = f"""Eres el mejor editor de clips virales de streamers latinos (TikTok, Reels, Shorts). Esta es la transcripción
automática (puede tener errores) del live "{titulo_live}" de {nombre}. Cada línea empieza con [segundo del video].

{CRITERIO_VIRAL}

Escoge HASTA {n} momentos, SOLO los que saquen 9 o 10 (si solo hay 1 o ninguno, devuelve eso; mejor pocos que relleno):
- Que se entiendan solos, sin contexto (historia completa, chiste con remate, reacción fuerte, polémica, consejo, momento épico o emotivo).
- Duración entre 20 y 60 segundos. "inicio" = el segundo donde EMPIEZA ese tema (no antes): lo prometido en el título
  tiene que oírse en los primeros segundos. "fin" = justo después del remate o cierre.
- UN SOLO TEMA por clip: si en medio cambian de tema, termina el clip antes del cambio.
- El título describe lo que se oye en el clip, sin inventar ni prometer algo que no pasa.
- PROHIBIDO (normas de TikTok/Instagram; la cuenta ya tiene una advertencia, ante la duda NO lo escojas):
  música sonando o cantando (copyright); casino, apuestas, Stake, slots o hablar de apostar; tomar alcohol, drogas,
  vapear o fumar; armas reales o violencia real, peleas, amenazas reales; odio o burlas contra un grupo (raza,
  religión, orientación, discapacidad) o acoso a una persona real; contenido sexual explícito; menores de edad en
  pantalla; retos peligrosos; datos personales de terceros (teléfonos, direcciones); desinformación médica o política;
  dinero fácil o estafas. La violencia DENTRO de un videojuego está bien si no es lo central ni es gore.
  LAS GROSERÍAS SÍ ESTÁN PERMITIDAS en lo que se dice ("marica", "parce", "mierda", "hijueputa", "gonorrea", "puta",
  insultos de cariño o de rabia entre amigos o contra el juego): así hablan los streamers y TikTok lo permite; en los
  subtítulos se tapan solas. NO descartes un momento por groserías.
- No repitas el mismo tema.
{extra}

Responde SOLO un JSON: lista de objetos con
"inicio" (segundo entero), "fin" (segundo entero), "titulo" (gancho para publicar, máx 70 caracteres, en {tono}),
"gancho" (texto corto en pantalla, máx 6 palabras, MAYÚSCULAS), "por_que" (una frase), "hashtags" (5, incluye #{nombre.lower()}), "puntaje" (1-10).
TÍTULO, GANCHO Y HASHTAGS LIMPIOS: sin groserías ni palabras sexuales o de drogas (nada de verga, puto, mierda, marica,
huevón, cagar, pepo, tangas...). Si el momento gira en torno a una grosería, descríbelo con gracia sin decirla
("La confesión más loca de Chanty 😂"). Lo que se dice EN el clip sí puede tener groserías.
Ordena de mejor a peor.

{chat}

TRANSCRIPCIÓN:
{lineas}"""
    clips = json.loads(re.search(r"\[.*\]", ia(prompt), re.S).group(0))
    return [c for c in clips if 15 <= c["fin"] - c["inicio"] <= 90 and int(c.get("puntaje") or 0) >= MIN_PUNTAJE][:n]


def ia(prompt):
    """Texto de la IA: Gemini y, si está sin cupo, Groq de respaldo."""
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
    return txt


def revisar(c, pals, tono="español colombiano"):
    """Segunda pasada con lo que REALMENTE se oye (Whisper del pedazo cortado con margen): ajusta inicio/fin para que el
    tema esté desde el arranque y sea uno solo, corrige título y gancho, o descarta. Devuelve c actualizado o None."""
    frases, actual = [], []
    for w, x, y in pals:  # frases cortas con su segundo, para que la IA ubique bien los cortes
        actual.append((w, x, y))
        if w.endswith((".", "?", "!", ",")) or len(actual) >= 12 or (y - actual[0][1]) > 6:
            frases.append(f"[{actual[0][1]:.1f}-{actual[-1][2]:.1f}] " + " ".join(p[0] for p in actual)); actual = []
    if actual: frases.append(f"[{actual[0][1]:.1f}-{actual[-1][2]:.1f}] " + " ".join(p[0] for p in actual))
    prompt = f"""Eres editor de clips virales. Este es EXACTAMENTE lo que se dice en un pedazo de un live (cada línea:
[segundo inicial-segundo final] texto). Se propuso este clip:
- título: {c['titulo']}
- gancho en pantalla: {c['gancho']}
- por qué: {c.get('por_que', '')}

Decide el mejor clip DENTRO de este pedazo:
1. "inicio": el segundo donde EMPIEZA el tema (lo del título se tiene que oír en los primeros 3 segundos). Usa un segundo inicial de línea.
2. Un solo tema de principio a fin: si cambian de tema, corta antes.
3. "fin": justo después del remate o cierre de ese tema. Usa un segundo final de línea.
4. Entre 20 y 60 segundos (20-45 es lo ideal para que lo vean completo).
5. "titulo" (máx 70 caracteres, en {tono}) y "gancho" (máx 6 palabras, MAYÚSCULAS) tienen que decir lo que de verdad se oye; reescríbelos si no cuadran
   Sin groserías ni palabras sexuales o de drogas en el título ni en el gancho (descríbelo con gracia sin decirlas).
6. Normas de TikTok (la cuenta ya tiene una advertencia): si el TEMA del clip es apuestas/casino/Stake, consumo de
   alcohol o drogas, violencia real o amenazas reales, odio contra un grupo o acoso a una persona real, contenido sexual
   explícito, menores, datos personales o retos peligrosos, DESCÁRTALO aunque sea gracioso.
   Las GROSERÍAS normales (marica, parce, mierda, hijueputa, gonorrea, puta, insultos entre amigos o contra el juego) SÍ
   están permitidas en lo que se dice: NO descartes por eso (en los subtítulos se tapan solas).
Si el tema prometido no está aquí, no sale un buen clip o rompe una norma, responde {{"descartar": true, "motivo": "..."}}.

7. JURADO: califica el clip FINAL (con estos cortes) con este criterio y pon "puntaje". Si saca menos de 9, descártalo.
""" + CRITERIO_VIRAL + f"""

Responde SOLO un JSON: {{"inicio": n, "fin": n, "titulo": "...", "gancho": "...", "puntaje": n, "descartar": false}}

LO QUE SE DICE:
""" + "\n".join(frases)
    try:
        r = json.loads(re.search(r"\{.*\}", ia(prompt), re.S).group(0))
    except Exception as e:
        print("  x revisión de la IA falló (dejo el corte original):", e, flush=True); return c
    if r.get("descartar"):
        print("  ✗ descartado en la revisión:", r.get("motivo", ""), flush=True); return None
    try: ini, fin = float(r["inicio"]), float(r["fin"])
    except Exception: return c
    if not 15 <= fin - ini <= 90: return c
    puntaje = int(r.get("puntaje") or c.get("puntaje") or 0)
    if puntaje < MIN_PUNTAJE:
        print(f"  ✗ el jurado le dio {puntaje}/10 (mínimo {MIN_PUNTAJE}): no se hace", flush=True); return None
    c = {**c, "_ini": ini, "_fin": fin, "puntaje": puntaje, "titulo": r.get("titulo") or c["titulo"], "gancho": r.get("gancho") or c["gancho"]}
    return c


# ---------- 3. cortar y editar ----------
LOGO_CAMPANA = [None]  # logo que pide una campaña (ej. el de Kick de Saulsaso): va arriba a la derecha del video


def overlay(gancho, credito, dst, y_video, h_video):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    if LOGO_CAMPANA[0] and Path(LOGO_CAMPANA[0]).exists():  # "ni tan chiquito ni tan grande": círculo de 150 px con borde
        from PIL import ImageOps
        lado = 150
        lg = ImageOps.fit(Image.open(LOGO_CAMPANA[0]).convert("RGB"), (lado, lado))
        m = Image.new("L", (lado, lado)); ImageDraw.Draw(m).ellipse((0, 0, lado - 1, lado - 1), fill=255)
        aro = Image.new("RGBA", (lado + 12, lado + 12)); ImageDraw.Draw(aro).ellipse((0, 0, lado + 11, lado + 11), fill=(83, 252, 24, 255))
        xl, yl = W - lado - 40, y_video + 20
        im.alpha_composite(aro, (xl - 6, yl - 6)); lg.putalpha(m); im.alpha_composite(lg.convert("RGBA"), (xl, yl))
    f, ls = V.texto_ajustado(d, V.quitar_emoji(gancho).upper(), V.F_BLACK, W - 160, 66, 40)  # más chico: que no estorbe
    alto = len(ls) * int(f.size * 1.12)
    y = max(150, y_video - 40 - alto)
    for l in ls:
        tw = d.textlength(l, font=f)
        d.text(((W - tw) / 2, y), l, font=f, fill="white", stroke_width=6, stroke_fill="black"); y += int(f.size * 1.12)
    fc = ImageFont.truetype(V.F_BLACK, 26)
    tw = d.textlength(credito, font=fc)
    yc = y_video + h_video + 18
    d.rounded_rectangle(((W - tw) / 2 - 18, yc, (W + tw) / 2 + 18, yc + 50), 12, fill=(83, 252, 24, 235))
    d.text(((W - tw) / 2, yc + 6), credito, font=fc, fill="black")
    im.save(dst)


def moderar(f, c):
    """Revisión de IMAGEN antes de publicar (la de texto no ve los logos): 6 cuadros del clip terminado a Gemini.
    Devuelve (True, "") si se puede publicar, o (False, motivo). Si no se puede revisar, NO se publica solo."""
    import base64, tempfile
    key = os.environ.get("GEMINI_API_KEY")
    if not key: return False, "no hay IA para revisar la imagen"
    dur = V.duracion(f); partes = []
    with tempfile.TemporaryDirectory() as d:
        for k in range(6):
            img = Path(d) / f"{k}.jpg"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{dur * (k + 0.5) / 6:.2f}", "-i", str(f), "-frames:v", "1",
                            "-vf", "scale=540:-2", "-q:v", "4", str(img)], capture_output=True)
            if img.exists(): partes.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(img.read_bytes()).decode()}})
    if not partes: return False, "no pude sacar cuadros del clip"
    prompt = f"""Eres moderador de TikTok. Estos son 6 cuadros de un clip vertical de un streamer (título: "{c['titulo']}").
La cuenta ya tiene una advertencia: sé estricto. ¿Se ve algo de esto?
- logos, marcas o pantallas de casino/apuestas (Stake, Roobet, slots, ruletas, cuotas de apuestas)
- alcohol o personas tomando, drogas, cigarrillos o vapeadores
- armas reales, sangre o violencia real (la violencia de un videojuego no cuenta si no es gore)
- desnudos, ropa interior, poses o gestos sexuales
- menores de edad
- símbolos de odio, gestos obscenos, datos personales legibles (teléfonos, direcciones)
Responde SOLO un JSON: {{"ok": true}} si no hay nada, o {{"ok": false, "motivo": "qué se ve y en qué cuadro"}}."""
    body = {"contents": [{"parts": [{"text": prompt}] + partes}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
    import time
    for intento in range(4):
        for modelo in ("gemini-flash-latest", "gemini-flash-lite-latest"):
            try:
                req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={key}",
                                             json.dumps(body).encode(), {"Content-Type": "application/json"})
                r = json.loads(urllib.request.urlopen(req, timeout=120).read())
                j = json.loads(re.search(r"\{.*\}", r["candidates"][0]["content"]["parts"][0]["text"], re.S).group(0))
                return (True, "") if j.get("ok") else (False, j.get("motivo", "contenido riesgoso"))
            except Exception as e:
                print(f"  moderación ocupada ({modelo}): {str(e)[:80]}", flush=True)
        time.sleep(20 * (intento + 1))
    return False, "no se pudo revisar la imagen (IA sin cupo)"


def camara_del_streamer(video, ini, fin):
    """Busca la cámara del streamer (cara pequeña y quieta en el mismo lugar = cámara en una esquina sobre el juego).
    Devuelve (x, y, ancho, alto) de la cara en píxeles del video, o None si el streamer llena la pantalla o no se ve."""
    try:
        import cv2
    except Exception:
        return None
    det = cv2.FaceDetectorYN.create(str(AQUI / "modelos" / "yunet.onnx"), "", (320, 320), 0.7)  # YuNet (OpenCV, MIT)
    cap = cv2.VideoCapture(str(video)); caras = []; alto_video = 0
    for k in range(8):  # 8 cuadros repartidos en el clip
        cap.set(cv2.CAP_PROP_POS_MSEC, (ini + (fin - ini) * (k + 0.5) / 8) * 1000)
        ok, img = cap.read()
        if not ok: continue
        alto_video = img.shape[0]
        det.setInputSize((img.shape[1], img.shape[0]))
        _, fs = det.detect(img)
        fs = [f[:4] for f in (fs if fs is not None else []) if f[3] >= alto_video * 0.05]
        if fs: caras.append(max(fs, key=lambda f: f[2] * f[3]))
    cap.release()
    if len(caras) < 3: return None  # casi no aparece una cara: no es una cámara fija
    import statistics as st
    x, y, w, h = (st.median(int(cf[i]) for cf in caras) for i in range(4))
    quieta = sum(abs(cf[0] - x) < w * 0.6 and abs(cf[1] - y) < h * 0.6 for cf in caras) >= len(caras) * 0.7
    if not quieta or h > alto_video * 0.28: return None  # se mueve o es grande: el streamer ocupa la pantalla (IRL / charla)
    return x, y, w, h


def encuadre(crudo, ini, fin, yv, hv):
    """Filtro de ffmpeg que deja el video vertical en [x] sin cortar lo importante, sobre el mismo video difuminado.
    Devuelve (filtro, y donde empieza el video, alto del video) para acomodar título, crédito y subtítulos alrededor.
    - gameplay (cámara chica del streamer en una esquina): el stream COMPLETO 16:9, como se ve en Kick;
    - IRL / charla (el streamer ocupa la pantalla): el video casi completo (4:3), más grande."""
    fondo = f"[a]scale=-2:{H},crop={W}:{H},boxblur=25:2,eq=brightness=-0.18,setsar=1[bg]"
    # Siempre el stream COMPLETO tal como se ve en Kick (sin recortar nada), sobre el mismo video difuminado
    # (a Nicolás le gusta así; los recortes 4:3 o la cámara agrandada se veían mal).
    vh = round(W * 9 / 16)
    filtro = f"[b]scale={W}:{vh},setsar=1[m]"  # los streams de Kick son 16:9
    print("  encuadre: stream completo (16:9) con fondo difuminado", flush=True)
    y = yv + (hv - vh) // 2
    return f"[0:v]split=2[a][b];{fondo};{filtro};[bg][m]overlay=0:{y}[x]", y, vh


GROSERIAS = re.compile(r"^(verga|vergas|puta|puto|putas|putos|putamadre|mierda|mierdas|marica|maricas|maric[oó]n|"
                       r"hijueputa|hp|gonorrea|malparido|malparida|culo|culiar|culi[aá]|cagar|cag[oó]|pendejo|pendeja|"
                       r"chimba|pirobo|piroba|perra|zorra|coño|joder|polla|pene|tetas|sexo|porno)$", re.I)
LIMPIAS = re.compile(r"\b(verga|puto|puta|mierda|marica|huev[oó]n|hijueputa|gonorrea|malparid[oa]|cagad[oa]|cagar|cag[oó]|cagu[eé]|cagaste|"
                     r"pep[ií]simo|tangas?|porno|sexo|culo)\b", re.I)


def tapar(palabra):
    """Grosería en los subtítulos → primera y última letra con asteriscos (M***A). El audio queda igual."""
    limpia = re.sub(r"[^\wáéíóúñ]", "", palabra, flags=re.I)
    if len(limpia) > 2 and GROSERIAS.match(limpia):
        return palabra.replace(limpia, limpia[0] + "*" * (len(limpia) - 2) + limpia[-1])
    return palabra


def limpiar_titulo(c):
    """Respaldo por si la IA dejó una grosería en el título, gancho o hashtags: se tapa con asteriscos."""
    t = lambda x: LIMPIAS.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), x)
    c["titulo"], c["gancho"] = t(c["titulo"]), t(c["gancho"])
    c["hashtags"] = [h for h in c.get("hashtags", []) if not LIMPIAS.search(h)]
    return c


def palabras_groq(video):
    """Palabras con tiempos usando Whisper large-v3 de Groq (mejor que el modelo chico en CPU). [] si no se puede."""
    k = os.environ.get("GROQ_API_KEY")
    if not k: return []
    import requests, tempfile
    try:
        with tempfile.TemporaryDirectory() as d:
            mp3 = Path(d) / "a.mp3"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(mp3)], check=True)
            with open(mp3, "rb") as a:
                r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions", headers={"Authorization": "Bearer " + k},
                                  files={"file": ("a.mp3", a, "audio/mpeg")},
                                  data={"model": "whisper-large-v3", "language": "es", "response_format": "verbose_json",
                                        "timestamp_granularities[]": "word"}, timeout=180).json()
        pals = [(w["word"].strip(), float(w["start"]), float(w["end"])) for w in r.get("words", []) if w.get("word", "").strip()]
        if not pals: print("  x Groq sin palabras:", str(r)[:150], flush=True)
        return pals
    except Exception as e:
        print("  x Groq (subtítulos):", e, flush=True); return []


def hacer_clip(c, stream, nombre, slug, t, credito=None):
    t.mkdir(parents=True, exist_ok=True)
    # 20 s de margen a cada lado: la revisión con el audio real puede mover el corte (los subtítulos de Kick fallan)
    a, b = max(0.0, c["inicio"] - 20), c["fin"] + 20
    crudo = t / "crudo.mp4"
    sh(["ffmpeg", "-y", "-ss", f"{a:.2f}", "-i", stream, "-t", f"{b - a:.2f}", "-r", "30", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(crudo)])
    todas = palabras_groq(crudo) or V.palabras(crudo)  # Whisper grande (Groq) entiende mejor el español rápido; si falla, el local
    r = revisar(c, todas) if todas else c
    if r is None: raise RuntimeError("descartado: el momento no da para un buen clip")
    c.update(r)
    limpiar_titulo(c)
    desde, hasta = (c["_ini"], c["_fin"]) if "_ini" in c else (c["inicio"] - a - 1.0, c["fin"] - a + 1.5)
    pals = [p for p in todas if p[1] >= desde - 0.3 and p[2] <= hasta + 0.3]
    # cortar en límites de palabras: arranca en la primera palabra y termina tras la última
    ini = max(0.0, pals[0][1] - 0.25) if pals else max(0.0, desde)
    fin = min(V.duracion(crudo), pals[-1][2] + 0.6) if pals else min(V.duracion(crudo), hasta)
    pals = [(w, x - ini, y - ini) for w, x, y in pals]
    fc, yv, hv = encuadre(crudo, ini, fin, 400, 1150)  # el video va centrado entre y=400 y y=1550
    V.subtitulos([(tapar(w), x, y) for w, x, y in pals], t / "subs.ass")  # groserías con asteriscos en pantalla
    # subtítulos chicos (84 → 60, borde 7 → 5) DEBAJO del video y del crédito: no tapan el juego ni al streamer
    s = (t / "subs.ass").read_text(encoding="utf-8").replace(",2,70,70,560,1", f",2,90,90,{H - (yv + hv) - 215},1")
    s = s.replace("Arial Black,84,", "Arial Black,60,").replace(",1,7,3,2,", ",1,5,2,2,")
    (t / "subs.ass").write_text(s, encoding="utf-8")
    overlay(c["gancho"], credito or f"{nombre} en Kick  ·  kick.com/{slug}", t / "ov.png", yv, hv)  # título arriba, crédito abajo
    titulo = re.sub(r'[\\/:*?"<>|]', "", c["titulo"])[:70].strip()
    dst = SALIDA / nombre / f"{datetime.date.today():%Y-%m-%d} - {titulo}.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    fc += f";[x][1:v]overlay=0:0,subtitles=subs.ass:fontsdir={FONTSDIR},format=yuv420p[v]"
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


def tiktok_borrador(f):
    """Manda el clip a los borradores de TikTok de la cuenta conectada en GangaClips Publisher (servidor de Cloudflare).
    Funciona aunque la app no esté aprobada: no publica, solo deja el video en la bandeja. Devuelve True si llegó."""
    api, k = os.environ.get("GANGACLIPS_API"), os.environ.get("GANGACLIPS_API_KEY")
    if not (api and k): return False
    try:
        import requests
        with open(f, "rb") as v:
            r = requests.post(api.rstrip("/") + "/tiktok/draft", headers={"X-Robot-Key": k, "Content-Type": "video/mp4"},
                              data=v, timeout=300).json()
        if r.get("publish_id"): print("  📥 TikTok: enviado a borradores", flush=True); return True
        print("  x TikTok borrador:", str(r)[:200], flush=True)
        if "too_many_pending" in str(r):
            avisar("tiktok_bandeja", "📥 TikTok: tu bandeja de borradores está LLENA. Publica o descarta los borradores de "
                                    "GangaClips Publisher para que vuelvan a llegar.")
        elif "access_token" in str(r).lower() or "no está conectado" in str(r):
            avisar("tiktok_conexion", "🔌 TikTok: se perdió la conexión de GangaClips Publisher. Abre la app web y vuelve a conectar TikTok.")
    except Exception as e:
        print("  x TikTok borrador:", e, flush=True)
    return False


def cupo_ig_usado(usuario):
    """Cuántas publicaciones hizo la API en la cuenta de Instagram en las últimas 24 h (Instagram permite 50)."""
    cfg = Path.home() / ".adflow-meta.json"
    try:
        import requests
        c = json.loads(cfg.read_bytes().decode("utf-8", errors="replace"))["cuentas"][usuario]
        r = requests.get(f"https://graph.facebook.com/v21.0/{c['ig_id']}/content_publishing_limit",
                         params={"fields": "quota_usage", "access_token": c["page_token"]}, timeout=30).json()
        return int(r["data"][0]["quota_usage"])
    except Exception as e:  # si no se puede saber, NO publicar (mejor quedarse corto que llenar la cuenta)
        print("  x no pude leer el cupo de Instagram:", e, flush=True); return 999


def minutos_desde_ultimo_ig(usuario):
    """Minutos desde la última publicación de la cuenta (para dejar mínimo ESPACIO_MIN entre reels). 0 si no se sabe."""
    cfg = Path.home() / ".adflow-meta.json"
    try:
        import requests
        c = json.loads(cfg.read_bytes().decode("utf-8", errors="replace"))["cuentas"][usuario]
        r = requests.get(f"https://graph.facebook.com/v21.0/{c['ig_id']}/media",
                         params={"fields": "timestamp", "limit": 1, "access_token": c["page_token"]}, timeout=30).json()
        ultimo = datetime.datetime.strptime(r["data"][0]["timestamp"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
        return (datetime.datetime.now(datetime.timezone.utc) - ultimo).total_seconds() / 60
    except Exception as e:
        print("  x no pude ver la última publicación:", e, flush=True); return 0


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
        for intento in range(3):  # cuenta nueva subiendo seguido: Instagram a veces responde ProcessingFailedError un rato
            r = requests.post(f"{G}/{ig}/media", data=datos, timeout=60).json()
            if "id" not in r: print("  x Instagram:", r, flush=True); return None
            if not archivo: break
            u = _subir_directo(r.get("uri") or f"https://rupload.facebook.com/ig-api-upload/v21.0/{r['id']}", tok, archivo)
            if u.get("success"): break
            print(f"  x Instagram subida (intento {intento + 1}):", u, flush=True)
            if intento == 2:
                avisar("instagram_subida", "📸 Instagram está rechazando las subidas (ProcessingFailedError). Los clips te llegan "
                                           "a Telegram para subirlos a mano mientras se le pasa.")
                return None
            time.sleep(60 if intento == 0 else 180)
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


def avisar(tipo, mensaje, cada_horas=6):
    """Aviso a Telegram cuando algo falla, sin repetir el mismo tipo más de una vez cada `cada_horas`
    (los robots corren cada 15 min; la marca de tiempo vive en clips/estado/_avisos.json y se sube con el estado)."""
    f = (ESTADO_DIR or AQUI) / "_avisos.json"
    ahora = datetime.datetime.now(datetime.timezone.utc)
    try: hechos = json.loads(f.read_text(encoding="utf-8"))
    except Exception: hechos = {}
    ultimo = hechos.get(tipo)
    if ultimo and ahora - datetime.datetime.fromisoformat(ultimo) < datetime.timedelta(hours=cada_horas): return
    print(f"  🔔 aviso: {mensaje}", flush=True)
    if TG.texto(f"⚠️ GangaClips · {mensaje}", bot=BOT):
        hechos[tipo] = ahora.isoformat()
        f.parent.mkdir(parents=True, exist_ok=True); f.write_text(json.dumps(hechos, indent=1), encoding="utf-8")


def _pausa_fb(horas=None):
    """Facebook bloquea la página un rato si se publica mucho seguido (error 368). Con horas: guarda la pausa
    (clips/estado/_facebook.json, la comparten los robots); sin horas: devuelve hasta cuándo sigue, o None."""
    f = (ESTADO_DIR or AQUI) / "_facebook.json"
    ahora = datetime.datetime.now(datetime.timezone.utc)
    if horas:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"pausa_hasta": (ahora + datetime.timedelta(hours=horas)).isoformat()}), encoding="utf-8")
        print(f"  ⏸️ Facebook pide calma: pausa de {horas} h", flush=True); return None
    try:
        hasta = datetime.datetime.fromisoformat(json.loads(f.read_text(encoding="utf-8"))["pausa_hasta"])
        return hasta if hasta > ahora else None
    except Exception:
        return None


def publicar_fb(video_publico, descripcion, usuario, archivo=None):
    """Publica un Reel en la PÁGINA de Facebook unida al Instagram `usuario` (permiso pages_manage_posts). URL o None.
    video_publico = URL pública del MP4, o archivo = MP4 local (se sube directo)."""
    cfg = Path.home() / ".adflow-meta.json"
    if not (cfg.exists() and (video_publico or archivo)): return None
    import requests, time
    c = json.loads(cfg.read_bytes().decode("utf-8", errors="replace")).get("cuentas", {}).get(usuario)
    if not c: return None
    G, tok = "https://graph.facebook.com/v21.0", c["page_token"]
    pausa = _pausa_fb()
    if pausa: print(f"  ⏸️ Facebook en pausa por límite de spam hasta {pausa:%H:%M} UTC", flush=True); return None
    try:
        pid = c.get("page_id") or requests.get(f"{G}/me", params={"access_token": tok}, timeout=30).json()["id"]
        s = requests.post(f"{G}/{pid}/video_reels", data={"upload_phase": "start", "access_token": tok}, timeout=60).json()
        if "video_id" not in s:
            print("  x Facebook:", str(s)[:300], flush=True)
            if (s.get("error") or {}).get("code") == 368:  # "limitamos la frecuencia…": no insistir
                _pausa_fb(horas=12)
                avisar("facebook_368", "📘 Facebook frenó la página por publicar seguido (límite de spam). El robot pausa Facebook "
                                       "12 h y sigue con Instagram y Telegram. No tienes que hacer nada.")
            elif s.get("error"):
                avisar("facebook_error", f"📘 Facebook no deja publicar: {str(s.get('error', {}).get('message', s))[:200]}")
            return None
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
    logo = (canal.get("campana") or {}).get("logo")
    LOGO_CAMPANA[0] = str(AQUI / logo) if logo else None
    archivo =(ESTADO_DIR / f"{slug}.json") if ESTADO_DIR else ESTADO
    est = json.loads(archivo.read_text(encoding="utf-8")) if archivo.exists() else {}
    hechos = set(est.get(slug, []))
    vivo = est.setdefault("en_vivo", {})  # {slug del live: último segundo ya revisado} → clips MIENTRAS sigue el live
    todos = lives(slug)
    vids = [v for v in todos if not v.get("is_live")]
    if vod_idx is not None: vids = [vids[vod_idx]]
    else:
        import vigia  # mismo criterio que el vigía: solo lives que terminaron hace menos de 24 h
        viejos = [v for v in vids[:3] if v["slug"] not in hechos and not vigia.reciente(v)]
        for v in viejos:
            print("salto (live de hace más de 24 h):", v["session_title"], flush=True); hechos.add(v["slug"]); vivo.pop(v["slug"], None)
        vids = [v for v in todos if v.get("is_live")][:1] + [v for v in vids[:3] if v["slug"] not in hechos]
    notas = []
    for v in vids:
        en_vivo = bool(v.get("is_live"))
        cats = {c["name"] for c in v.get("categories") or []}
        if cats & NO_CATEG:
            print("salto (casino):", v["session_title"], flush=True)
            if not en_vivo: hechos.add(v["slug"]); vivo.pop(v["slug"], None)
            continue
        base = v["source"].rsplit("/", 1)[0]
        try: trans = transcripcion(base)
        except Exception as e:  # sin subtítulos (live muy reciente o borrado): no reintentar cada 15 min
            print("  x sin transcripción:", e, flush=True); trans = []
        desde = vivo.get(v["slug"], 0) if vod_idx is None else 0
        if len(trans) < 20:  # canales chicos: Kick no les hace subtítulos → escuchar el audio con Whisper (Groq)
            trans = whisper_kick(base, desde)
        hasta = trans[-1][0] if trans else 0
        print(f"live{' EN VIVO' if en_vivo else ''}: {v['session_title']} (min {desde / 60:.0f} a {hasta / 60:.0f})", flush=True)
        if en_vivo and hasta - desde < 12 * 60:
            print("  aún no hay 12 min nuevos; espero", flush=True); continue
        ventana = [x for x in trans if x[0] >= desde - 60]
        if len(ventana) < 50:
            print("  muy poca transcripción", flush=True)
            if not en_vivo: hechos.add(v["slug"]); vivo.pop(v["slug"], None)
            continue
        # cuántos clips: ~clips_por_hora de lo que falta revisar; un live revisado completo saca mínimo clips_por_live
        cuantos = round((hasta - desde) / 3600 * canal.get("clips_por_hora", 4))
        cuantos = min(canal.get("clips_max", 15), max(1 if desde or en_vivo else n, cuantos))
        if FIJO[0]: cuantos = n
        print(f"  clips a sacar: {cuantos}", flush=True)
        chat = chat_picos(slug, v, max(0, desde - 60), hasta) if not FIJO[0] else ""  # dónde explotó el chat (risas, hype)
        try: elegidos = escoger(nombre, v["session_title"], ventana, cuantos, chat=chat)
        except Exception as e:
            print("  x la IA no respondió:", e, flush=True); elegidos = []
            avisar("ia_sin_cupo", f"🤖 La IA (Gemini y Groq) no respondió y no se pudieron escoger clips de {nombre}. "
                                  f"Suele ser falta de cupo; se reintenta solo en la próxima vuelta.")
        # en vivo: no cortar momentos pegados al borde (todavía no se ven completos)
        elegidos = [c for c in elegidos if c["inicio"] >= desde - 60 and (not en_vivo or c["fin"] <= hasta - 20)]
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
                seguro, motivo = moderar(f, c)  # normas de TikTok/IG: lo que se VE (logos de casino, alcohol, armas...)
                if not seguro:
                    print("  ⛔ no se publica:", motivo, flush=True)
                    continue
                puntaje = int(c.get("puntaje") or 0)
                camp = canal.get("campana")
                if camp:  # streamer de una campaña (Quanty): NO va a GangaClips; te llega listo para subir en la otra cuenta
                    texto_c = (f"{c['titulo']}\n\n🎥 {nombre} en kick.com/{slug}\n\n{tags} {' '.join(camp.get('hashtags', []))} "
                               f"{camp.get('codigo', '')}").strip()
                    TG.enviar(f, f"💰 CAMPAÑA {camp.get('nombre', nombre)} · puntaje {puntaje}/10\n"
                                 f"👉 Súbelo en TikTok desde {camp.get('cuenta', '')} (NO en gangaclips) y envíalo en Quanty en menos de 48 h\n\n{texto_c}", bot=BOT)
                    TG.copiable(texto_c, bot=BOT)
                    continue
                hechas = {}
                # publicar solo (IG + página de FB): pocos al día y espaciados; si no toca, te llega a Telegram para que decidas
                if IG and PUBLICADOS[0] < MAX_PUB and cupo_ig_usado(IG) < MAX_DIA and minutos_desde_ultimo_ig(IG) >= ESPACIO_MIN:
                    hechas = {"Instagram": publicar_ig(None, texto, IG, archivo=f), "Facebook": publicar_fb(None, texto, IG, archivo=f)}
                    if any(hechas.values()): PUBLICADOS[0] += 1
                # TikTok: a los borradores solo los mejores (muchos clips de otros = "contenido no original" y menos alcance)
                borrador = puntaje >= TIKTOK_MIN and tiktok_borrador(f)
                redes ="\n".join(f"✅ {k}: {u}" if u else f"⚠️ {k}: NO se publicó (súbelo tú)" for k, u in hechas.items())
                if borrador: redes = (redes + "\n" if redes else "") + "📥 TikTok: en tus borradores (toca el mensaje de abajo para copiar el texto)"
                faltan = [r for r, ok in (("TikTok", borrador), ("Instagram", hechas.get("Instagram")), ("Facebook", hechas.get("Facebook"))) if not ok]
                aviso = f"⚠️ Publicar a mano: {', '.join(faltan)}" if faltan else "✅ Publicado en IG y FB · TikTok: dale Publicar en tus borradores"
                TG.enviar(f, f"⭐ {nombre}{' 🔴 EN VIVO' if en_vivo else ''} · puntaje {puntaje}/10\n\n{texto}\n\n{redes + chr(10) if redes else ''}{aviso}", bot=BOT)
                TG.copiable(texto, bot=BOT)  # un toque en Telegram = texto copiado para pegarlo en TikTok
                notas.append(f"{f.name}\n{c['titulo']} {' '.join(c.get('hashtags', []))}\n"
                             f"Crédito: {nombre} en vivo en kick.com/{slug}\n")
            except Exception as e:
                print("  x clip", i, e, flush=True)
        if en_vivo: vivo[v["slug"]] = max(desde, hasta - 20)
        else: hechos.add(v["slug"]); vivo.pop(v["slug"], None)
        shutil.rmtree(TMP / v["slug"], ignore_errors=True)
    est[slug] = sorted(hechos)
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(json.dumps(est, indent=1, ensure_ascii=False), encoding="utf-8")
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
        FIJO[0] = bool(a.n)
        try: procesar(c, a.n or c.get("clips_por_live", 6), a.vod)
        except Exception as e: print("error", c["nombre"], e, flush=True)
    print("listo", flush=True)


if __name__ == "__main__":
    main()
