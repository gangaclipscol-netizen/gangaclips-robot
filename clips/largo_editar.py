"""Edición de un video largo de YouTube a partir de una historia escogida por largo.py.

hacer() deja en la carpeta de salida: video.mp4 (1080p, adelanto de 10 s + partes con transición suave),
miniatura.jpg (1280x720: cara más expresiva recortada + 1-3 palabras gigantes) y youtube.txt (título, descripción
con capítulos y créditos, tags); luego lo sube como descarga (release de GitHub) y avisa por Telegram.
"""
import base64, json, os, re, shutil, subprocess, urllib.request
from pathlib import Path

import clips as C
import telegram_envio as TG

W, H = 1920, 1080
LADO = [""]  # dónde está el streamer en el cuadro de la miniatura (lo dice la IA al escoger el cuadro)
FUENTE = os.environ.get("ADFLOW_FUENTE", "C:/Windows/Fonts/ariblk.ttf")


def sh(args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    if r.returncode: print(r.stderr[-1500:], flush=True); raise RuntimeError("ffmpeg falló")
    return r


def duracion(f):
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)]).stdout.strip())


def cortar(stream, a, b, dst, fundido=0.35):
    """Un pedazo del live en 1080p30, con entrada y salida suaves (video y audio) para que los cortes no se sientan."""
    d = b - a
    vf = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p," \
         f"fade=t=in:st=0:d={fundido},fade=t=out:st={max(0, d - fundido):.2f}:d={fundido}"
    af = f"afade=t=in:st=0:d={fundido},afade=t=out:st={max(0, d - fundido):.2f}:d={fundido},aresample=48000"
    sh(["ffmpeg", "-y", "-ss", f"{a:.2f}", "-i", stream, "-t", f"{d:.2f}", "-vf", vf, "-af", af, "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "160k", "-ac", "2", str(dst)])


def a_tiempo_video(seg, tramos, ofs):
    """Segundo del live → segundo del video final (ofs = duración del adelanto). Si cae en un corte, el siguiente tramo."""
    acum = ofs
    for a, b in tramos:
        if seg < a: return acum
        if seg <= b: return acum + (seg - a)
        acum += b - a
    return acum


def mmss(s):
    s = int(s); return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# ---------- miniatura ----------
def cuadros_candidatos(stream, h, tramos, carpeta, n=14):
    """Cuadros 1080p repartidos por el adelanto y las partes (ahí están los momentos fuertes)."""
    momentos = []
    if h.get("adelanto"): momentos += [float(h["adelanto"][0]) + i for i in (1, 3, 5)]
    total = sum(b - a for a, b in tramos)
    for k in range(n - len(momentos)):
        x = total * (k + 0.5) / (n - len(momentos))
        for a, b in tramos:
            if x <= b - a: momentos.append(a + x); break
            x -= b - a
    out = []
    for k, m in enumerate(momentos):
        f = carpeta / f"c{k:02d}.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{m:.2f}", "-i", stream, "-frames:v", "1", "-q:v", "2", str(f)],
                       capture_output=True)
        if f.exists() and f.stat().st_size > 5000: out.append(f)
    return out


def escoger_cuadros(cuadros, h):
    """La IA (visión) escoge el mejor cuadro para la miniatura (cara expresiva, persona clara) y otro para el círculo."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key or not cuadros: return cuadros[:2]
    partes = [{"text": f"Cuadros numerados 0 a {len(cuadros) - 1} de un video de YouTube titulado \"{h['titulo']}\".\n"
                       "Para la MINIATURA, escoge: \"principal\" = el cuadro donde el STREAMER sale de cuerpo o medio cuerpo, GRANDE, "
                       "NÍTIDO (sin movimiento) y con la expresión más exagerada (sorpresa, risa, grito); nada de caras chiquitas, "
                       "borrosas o cortadas; \"circulo\" = otro cuadro que muestre lo más llamativo de "
                       "la historia (el lugar, el invitado, el objeto). Nada de logos de casino, alcohol ni armas.\n"
                       "\"lado\" = dónde está el streamer en el cuadro principal (izquierda, centro o derecha).\n"
                       "Responde SOLO un JSON: {\"principal\": n, \"circulo\": n, \"lado\": \"...\"}"}]
    for f in cuadros:
        partes.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(_achicar(f)).decode()}})
    body = {"contents": [{"parts": partes}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
    for modelo in ("gemini-flash-latest", "gemini-flash-lite-latest", "gemini-flash-latest"):
        try:
            req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={key}",
                                         json.dumps(body).encode(), {"Content-Type": "application/json"})
            j = json.loads(json.loads(urllib.request.urlopen(req, timeout=180).read())["candidates"][0]["content"]["parts"][0]["text"])
            LADO[0] = j.get("lado", "")
            return [cuadros[int(j["principal"])], cuadros[int(j.get("circulo", 0))]]
        except Exception as e:
            print(f"  miniatura: IA ocupada ({modelo}) {str(e)[:80]}", flush=True)
            import time; time.sleep(20)
    return cuadros[:2]


def _achicar(f):
    from PIL import Image
    import io
    im = Image.open(f).convert("RGB"); im.thumbnail((640, 360)); b = io.BytesIO(); im.save(b, "JPEG", quality=80)
    return b.getvalue()


def cara_principal(img):
    """(x, y, ancho, alto) de la cara más grande del cuadro con YuNet (OpenCV), o None."""
    try:
        import cv2
        im = cv2.imread(str(img))
        det = cv2.FaceDetectorYN.create(str(C.AQUI / "modelos" / "yunet.onnx"), "", (im.shape[1], im.shape[0]), 0.7)
        _, caras = det.detect(im)
        if caras is None or not len(caras): return None
        caras = [c for c in caras if c[3] >= max(f[3] for f in caras) * 0.45]  # sin las caras chiquitas del fondo
        meta = {"izquierda": 0.2, "centro": 0.5, "derecha": 0.8}.get((LADO[0] or "").lower())
        elegida = (min(caras, key=lambda f: abs((f[0] + f[2] / 2) / im.shape[1] - meta)) if meta is not None  # la del streamer
                   else max(caras, key=lambda f: f[2] * f[3]))
        x, y, w, h = elegida[:4]
        return float(x), float(y), float(w), float(h)
    except Exception as e:
        print("  miniatura: sin detector de caras:", e, flush=True); return None


def cara_del_banco(slug, emocion=None):
    """Foto recortada del streamer del banco (clips/caras/<slug>/, la arma caras.py): la de la emoción pedida con mejor
    nota, o la mejor de todas. None si todavía no hay banco."""
    carpeta = C.AQUI / "caras" / slug
    try:
        indice = json.loads((carpeta / "indice.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    if not indice: return None
    misma = [x for x in indice if emocion and x.get("emocion") == emocion]
    import random
    elegida = random.choice(sorted(misma or indice, key=lambda x: -x.get("nota", 0))[:3])  # varía entre las 3 mejores
    return carpeta / elegida["archivo"]


def miniatura(contexto, circulo, texto, dst, slug=None, emocion=None, principal=None):
    """1280x720 al estilo de los canales grandes: de fondo el cuadro de la historia (vivo, oscurecido a la izquierda),
    el streamer recortado y grande a la derecha (del BANCO de caras: siempre nítido y expresivo), 1-3 palabras gigantes
    en Anton con borde y sombra, y un círculo con zoom a lo llamativo."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
    Wm, Hm = 1280, 720
    fondo = ImageOps.fit(Image.open(contexto).convert("RGB"), (Wm, Hm), centering=(0.5, 0.4))
    fondo = ImageEnhance.Color(fondo).enhance(1.45)
    fondo = ImageEnhance.Contrast(fondo).enhance(1.2).filter(ImageFilter.GaussianBlur(5))
    sombra = Image.new("L", (Wm, Hm)); ds = ImageDraw.Draw(sombra)
    for x in range(Wm): ds.line([(x, 0), (x, Hm)], fill=int(200 * max(0, 1 - x / (Wm * 0.7))))  # oscuro a la izquierda: ahí va el texto
    fondo = Image.composite(Image.new("RGB", (Wm, Hm), (0, 0, 0)), fondo, sombra)
    lienzo = fondo.convert("RGBA")
    # el streamer: del banco; si no hay banco, recortado del cuadro principal (solo si sale grande)
    persona = None
    banco = cara_del_banco(slug, emocion) if slug else None
    if banco:
        persona = Image.open(banco).convert("RGBA")
    elif principal:
        try:
            from rembg import remove, new_session
            p = remove(Image.open(principal).convert("RGB"), session=new_session("u2net_human_seg"))
            caja = p.getbbox()
            if caja and (caja[3] - caja[1]) >= 0.45 * p.height: persona = p.crop(caja)
        except Exception as e:
            print("  miniatura sin recorte:", e, flush=True)
    if persona is not None:
        alto = int(Hm * 1.02)
        escala = min(alto / persona.height, 1.4)  # nunca agrandar de más (se pixela)
        persona = persona.resize((int(persona.width * escala), int(persona.height * escala)), Image.LANCZOS)
        if persona.width > Wm * 0.58:
            persona = persona.resize((int(Wm * 0.58), int(persona.height * Wm * 0.58 / persona.width)), Image.LANCZOS)
        persona = persona.filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=2))
        a = persona.split()[3]
        borde = a.filter(ImageFilter.MaxFilter(17))
        blanco = Image.new("RGBA", persona.size, (255, 255, 255, 255)); blanco.putalpha(borde)
        brillo = Image.new("RGBA", persona.size, (255, 230, 0, 150)); brillo.putalpha(borde.filter(ImageFilter.GaussianBlur(18)))
        x, y = Wm - persona.width + 10, max(0, Hm - persona.height)
        lienzo.alpha_composite(brillo, (x, y)); lienzo.alpha_composite(blanco, (x, y)); lienzo.alpha_composite(persona, (x, y))
    # texto gigante (Anton), la última línea en amarillo, borde negro grueso y sombra
    d = ImageDraw.Draw(lienzo)
    fuente = str(C.AQUI / "fuentes" / "Anton-Regular.ttf")
    palabras = (texto or "").upper().split()
    lineas = [" ".join(palabras[i:i + 2]) for i in range(0, len(palabras), 2)] or ["¿QUÉ PASÓ?"]
    tam = 230
    while tam > 80:
        f = ImageFont.truetype(fuente, tam)
        if all(d.textlength(l, font=f) <= Wm * 0.56 for l in lineas) and tam * 1.0 * len(lineas) <= Hm * 0.72: break
        tam -= 8
    y = Hm - 50 - int(tam * 1.0) * len(lineas)
    for k, l in enumerate(lineas):
        color = (255, 222, 0) if k == len(lineas) - 1 else (255, 255, 255)
        sombra_t = Image.new("RGBA", (Wm, Hm)); ImageDraw.Draw(sombra_t).text((52, y + 10), l, font=f, fill=(0, 0, 0, 200),
                                                                                 stroke_width=tam // 10, stroke_fill=(0, 0, 0, 200))
        lienzo.alpha_composite(sombra_t.filter(ImageFilter.GaussianBlur(8)))
        ImageDraw.Draw(lienzo).text((44, y), l, font=f, fill=color, stroke_width=max(8, tam // 13), stroke_fill=(0, 0, 0))
        y += int(tam * 1.0)
    if circulo and circulo != contexto:  # círculo con zoom a lo llamativo, aro rojo
        r = 140
        c = ImageOps.fit(Image.open(circulo).convert("RGB"), (2 * r, 2 * r))
        c = ImageEnhance.Color(c).enhance(1.3).filter(ImageFilter.UnsharpMask(2, 80, 2))
        m = Image.new("L", (2 * r, 2 * r)); ImageDraw.Draw(m).ellipse((0, 0, 2 * r - 1, 2 * r - 1), fill=255)
        aro = Image.new("RGBA", (2 * r + 20, 2 * r + 20)); ImageDraw.Draw(aro).ellipse((0, 0, 2 * r + 19, 2 * r + 19), fill=(230, 20, 20, 255))
        lienzo.alpha_composite(aro, (40, 30)); c.putalpha(m); lienzo.alpha_composite(c.convert("RGBA"), (50, 40))
    final = lienzo.convert("RGB").filter(ImageFilter.UnsharpMask(radius=1.5, percent=60, threshold=2))
    final.save(dst, "JPEG", quality=94)


# ---------- todo junto ----------
def hacer(nombre, slug, v, base, h, tramos, t, salida):
    shutil.rmtree(t, ignore_errors=True); t.mkdir(parents=True)
    stream = base + "/1080p60/playlist.m3u8"
    piezas = []
    if h.get("adelanto"):
        a, b = float(h["adelanto"][0]), float(h["adelanto"][1])
        if 3 <= b - a <= 20: cortar(stream, a, b, t / "p000.mp4"); piezas.append(t / "p000.mp4")
    for k, (a, b) in enumerate(tramos):
        print(f"  cortando parte {k + 1}/{len(tramos)} (min {a / 60:.0f}-{b / 60:.0f})", flush=True)
        cortar(stream, a, b, t / f"p{k + 1:03d}.mp4"); piezas.append(t / f"p{k + 1:03d}.mp4")
    (t / "lista.txt").write_text("".join(f"file '{p.name}'\n" for p in piezas), encoding="utf-8")
    titulo = re.sub(r'[\\/:*?"<>|]', "", h["titulo"])[:90].strip()
    carpeta = salida / nombre / f"{v['start_time'][:10]} - {titulo}"
    carpeta.mkdir(parents=True, exist_ok=True)
    video = carpeta / "video.mp4"
    sh(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "lista.txt", "-c", "copy", "-movflags", "+faststart", str(video)], cwd=t)
    ofs = duracion(piezas[0]) if h.get("adelanto") and piezas and piezas[0].name == "p000.mp4" else 0
    # capítulos (YouTube: el primero en 0:00, mínimo 3, de 10 s o más)
    caps = [(0, "Lo que vas a ver")] if ofs else []
    for cap in h.get("capitulos", []):
        s = a_tiempo_video(float(cap.get("inicio", 0)), tramos, ofs)
        if not caps or s - caps[-1][0] >= 10: caps.append((s, cap.get("titulo", "")))
    if caps and caps[0][0] != 0: caps.insert(0, (0, "Inicio"))
    capitulos = "\n".join(f"{mmss(s)} {tt}" for s, tt in caps) if len(caps) >= 3 else ""
    texto = (f"{h.get('descripcion', '').strip()}\n\n"
             + (f"⏱️ CAPÍTULOS\n{capitulos}\n\n" if capitulos else "")
             + f"🎥 Stream original de {nombre} en vivo: https://kick.com/{slug}\nCanal de fans · No oficial\n\n"
             + " ".join("#" + re.sub(r"\W", "", x) for x in h.get("tags", [])[:5]))
    (carpeta / "youtube.txt").write_text(f"TÍTULO:\n{h['titulo']}\n\nDESCRIPCIÓN:\n{texto}\n\nETIQUETAS:\n{', '.join(h.get('tags', []))}\n",
                                         encoding="utf-8")
    cand = cuadros_candidatos(stream, h, tramos, t)
    principal, circulo = (escoger_cuadros(cand, h) + [None, None])[:2]
    if principal:  # fondo = el cuadro más nítido de la historia; el streamer sale del banco de caras (caras.py)
        def nit(f):
            try:
                import cv2
                return cv2.Laplacian(cv2.cvtColor(cv2.imread(str(f)), cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
            except Exception:
                return 0
        fondo = max(cand, key=nit)
        miniatura(fondo, circulo if circulo != fondo else principal, h.get("texto_miniatura", ""), carpeta / "miniatura.jpg",
                  slug=slug, emocion=h.get("emocion_miniatura"), principal=principal)
    print(f"  ✓ video {duracion(video) / 60:.1f} min · {carpeta}", flush=True)
    entregar(nombre, h, carpeta, texto)
    return carpeta


def entregar(nombre, h, carpeta, texto):
    """Telegram no acepta videos de 30 min: el video queda como descarga en el release 'largos' de GitHub."""
    enlace = ""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo and shutil.which("gh"):
        etiqueta = "largos"
        subprocess.run(["gh", "release", "view", etiqueta, "-R", repo], capture_output=True).returncode and \
            subprocess.run(["gh", "release", "create", etiqueta, "-R", repo, "-t", "Videos largos para YouTube",
                            "-n", "Videos largos listos para subir a YouTube (los hace clips/largo.py)."], capture_output=True)
        nombre_arch = re.sub(r"[^\w.-]+", "_", f"{carpeta.name[:60]}.mp4")
        dst = carpeta / nombre_arch; shutil.copy(carpeta / "video.mp4", dst)
        subir = [str(dst)]
        if (carpeta / "miniatura.jpg").exists():  # cada miniatura con su nombre (no se pisan entre videos)
            mini = carpeta / nombre_arch.replace(".mp4", "-miniatura.jpg"); shutil.copy(carpeta / "miniatura.jpg", mini); subir.append(str(mini))
        r = subprocess.run(["gh", "release", "upload", etiqueta, *subir, "--clobber", "-R", repo], capture_output=True, text=True)
        if r.returncode == 0: enlace = f"https://github.com/{repo}/releases/download/{etiqueta}/{nombre_arch}"
        else: print("  x no subió al release:", r.stderr[-300:], flush=True)
    cfg = TG._cfg(C.BOT)
    if cfg and cfg.get("token") and cfg.get("chat_id") and (carpeta / "miniatura.jpg").exists():
        import requests
        with open(carpeta / "miniatura.jpg", "rb") as f:
            requests.post(f"https://api.telegram.org/bot{cfg['token']}/sendPhoto", data={
                "chat_id": cfg["chat_id"],
                "caption": f"🎬 VIDEO LARGO para YouTube · {nombre} · puntaje {h.get('puntaje')}/10\n\n{h['titulo']}\n\n"
                           f"⬇️ Descargar video: {enlace or '(no se pudo subir; está en la carpeta del robot)'}\n"
                           f"🖼️ Arriba: la miniatura (guárdala)\n📋 Abajo: título y descripción para copiar"[:1024]},
                files={"photo": f}, timeout=60)
    TG.copiable(h["titulo"], bot=C.BOT)
    TG.copiable(texto, bot=C.BOT)
