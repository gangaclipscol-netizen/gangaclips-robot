"""Clips para campañas de MÚSICA de Quanty (la canción se pone en TikTok desde el sonido oficial al subir el video).

  vicecity  → "Ovi ft Reverse - Vice City": montaje de 15-20 s con los momentos de más acción de GTA VI de los streams
              de la ganga (choques, persecuciones, tiroteos), sin la voz del streamer.
  labotella → "LA BOTELLA - Mach & Daddy": escenas del VIDEO MUSICAL OFICIAL con frases POV aprobadas en el brief
              (prohíbe material de streamers y marcas de agua).

Cada clip llega a Telegram como 💰 CAMPAÑA con el texto (código incluido) y el enlace del sonido para agregarlo en TikTok.
Uso:  python musica.py vicecity [--n 2]   |   python musica.py labotella [--n 3]
"""
import argparse, json, os, random, subprocess, sys, tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import clips as C
import telegram_envio as TG

W, H = 1080, 1920
FUENTE = str(AQUI / "fuentes" / "Anton-Regular.ttf")

CAMPANAS = {
    "vicecity": {"nombre": "Ovi ft Reverse – Vice City", "codigo": "#qc2405x2611", "hasta": "2026-12-03",
                 "sonido": "https://vm.tiktok.com/ZN9PD2VthAPsF-ZnHlY/",
                 "hashtags": "#vicecity #ovi #reverse #gta6 #gtavi",
                 "textos": ["GTA VI CON VICE CITY DE FONDO 🌴", "ASÍ SE SIENTE JUGAR GTA VI 🔥", "VICE CITY NUNCA SE VIO TAN BIEN",
                            "CUANDO SUENA VICE CITY EN GTA VI 🌴", "GTA VI + ESTA CANCIÓN = 🔥"]},
    "labotella": {"nombre": "LA BOTELLA – Mach & Daddy", "codigo": "#qc2408x2611", "hasta": "2026-12-03",
                  "sonido": "https://www.tiktok.com/music/La-Botella-6954256185298012162",
                  "hashtags": "#labotella #machydaddy #reggaeton #reggaetonclasico #genz",
                  "video": "https://www.youtube.com/watch?v=n3yQLouzrGM",
                  # frases del brief (drivers 1, 2 y 4; el de alcohol NO por la advertencia de TikTok)
                  "textos": ["POV: TIENES 19 AÑOS Y ESCUCHAS LA MÚSICA DE TU MAMÁ", "GEN Z DESCUBRE UN CLÁSICO DEL REGGAETÓN",
                             "LA HERENCIA QUE ME DEJÓ MI PAPÁ CON ESTE TEMA", "ESTO SONABA ANTES DE QUE YO NACIERA Y SIGUE SIENDO UN HIT",
                             "CUANDO TU MAMÁ PONE ESTO EN EL CARRO Y DE REPENTE ENTIENDES TODO",
                             "EL REGGAETÓN ROMÁNTICO DE ANTES NO TIENE COMPETENCIA",
                             "CUANDO EL ALGORITMO TE LLEVA A LA BUENA MÚSICA DE ANTES"],
                  "descripciones": ["La Botella de Mach & Daddy — el clásico que la Gen Z está redescubriendo. Escúchala en TikTok @MachYDaddy",
                                    "La herencia musical más importante que te pudo dejar tu familia. @MachYDaddy en TikTok.",
                                    "El reggaetón romántico de antes que sigue ganando. La Botella disponible en TikTok.",
                                    "POV: tienes 19 años y acabas de descubrir La Botella de Mach & Daddy. Ya no hay vuelta atrás."]},
}


def frases_nuevas(clave, n):
    """Frases DISTINTAS para el texto de arriba, escritas por la IA cada vez (con la lista del brief como ejemplo), para que
    no se repitan entre clips ni entre días. Si la IA no responde, toma de la lista sin repetir."""
    camp = CAMPANAS[clave]
    import datetime
    prompt = f"""Escribe {n + 3} textos DISTINTOS para poner arriba de un video vertical de TikTok de la campaña "{camp['nombre']}".
Estilo de estos ejemplos (no los copies, inventa nuevos): {json.dumps(camp['textos'], ensure_ascii=False)}
Reglas: máx. 12 palabras, en MAYÚSCULAS, español latino natural, que den ganas de quedarse a ver, sin groserías,
sin hablar de alcohol, drogas ni apuestas, sin inventar datos. Varía el ángulo entre ellos (POV, nostalgia, humor, reto,
pregunta). Hoy es {datetime.date.today()} (sé original). Responde SOLO un JSON: lista de textos."""
    try:
        r = json.loads(__import__("re").search(r"\[.*\]", C.ia(prompt), __import__("re").S).group(0))
        r = [x.strip().upper() for x in r if isinstance(x, str) and 3 <= len(x.split()) <= 14]
        random.shuffle(r)
        if len(r) >= n: return r[:n]
    except Exception as e:
        print("  frases: la IA no respondió, uso la lista:", e, flush=True)
    return random.sample(camp["textos"], min(n, len(camp["textos"])))


def placa(texto, dst, y_video, h_video):
    """Texto grande arriba del video (Anton, blanco con borde negro), sin logos ni marcas de agua."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    tam = 96
    while tam > 50:
        f = ImageFont.truetype(FUENTE, tam)
        palabras, lineas, l = texto.split(), [], ""
        for p in palabras:
            if d.textlength((l + " " + p).strip(), font=f) <= W - 120: l = (l + " " + p).strip()
            else: lineas.append(l); l = p
        lineas.append(l)
        if len(lineas) * tam * 1.1 <= y_video - 120: break
        tam -= 6
    y = y_video - 50 - int(len(lineas) * tam * 1.1)
    for l in lineas:
        d.text(((W - d.textlength(l, font=f)) / 2, y), l, font=f, fill="white", stroke_width=7, stroke_fill="black"); y += int(tam * 1.1)
    im.save(dst)


def armar(fuente_y_tramos, texto, dst, t):
    """Pega los tramos (cada uno: archivo o url, inicio, duración) en vertical con fondo difuminado, SIN audio
    (la canción se agrega en TikTok) y con el texto arriba."""
    vh = round(W * 9 / 16); yv = (H - vh) // 2 - 60
    piezas = []
    for k, (src, a, d) in enumerate(fuente_y_tramos):
        p = t / f"p{k}.mp4"
        fc = (f"[0:v]split=2[a][b];[a]scale=-2:{H},crop={W}:{H},boxblur=25:2,eq=brightness=-0.18,setsar=1[bg];"
              f"[b]scale={W}:{vh},setsar=1[m];[bg][m]overlay=0:{yv},fps=30,format=yuv420p[v]")
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.2f}", "-i", src, "-t", f"{d:.2f}", "-filter_complex", fc,
                            "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(p)], capture_output=True, text=True)
        if r.returncode == 0 and p.exists(): piezas.append(p)
    if not piezas: return None
    (t / "lista.txt").write_text("".join(f"file '{p.name}'\n" for p in piezas))
    sh = lambda a: subprocess.run(a, capture_output=True, text=True, cwd=t)
    sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", "lista.txt", "-c", "copy", "junto.mp4"])
    placa(texto, t / "placa.png", yv, vh)
    sh(["ffmpeg", "-v", "error", "-y", "-i", "junto.mp4", "-i", "placa.png", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-filter_complex", "[0:v][1:v]overlay=0:0,format=yuv420p[v]", "-map", "[v]", "-map", "2:a", "-shortest",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-c:a", "aac", "-movflags", "+faststart", str(dst)])
    return dst if dst.exists() else None


def vicecity(n):
    """Momentos de acción de GTA VI: picos de 'sorpresa/hype' en el chat de los streams de la ganga que juegan GTA VI."""
    camp = CAMPANAS["vicecity"]; hechos = []
    canales = [c for c in json.loads((AQUI / "gangaclips.json").read_text(encoding="utf-8")) if not c.get("campana")]
    tramos = []
    for c in canales:
        try: vids = C.lives(c["kick"])[:2]
        except Exception: continue
        for v in vids:
            cats = " ".join(x.get("name", "") for x in v.get("categories") or []) + " " + v.get("session_title", "")
            if "GTA" not in cats.upper(): continue
            base = v["source"].rsplit("/", 1)[0]
            dur = (v.get("duration") or 0) / 1000 or 7200
            picos = C.chat_picos(c["kick"], v, max(0, dur - 4 * 3600), dur, paso=30)
            for l in picos.splitlines():
                if "sorpresa" in l and "[" in l:
                    try:
                        seg = int(l.split("[")[1].split("]")[0]); hype = int(l.split(",")[1].split("%")[0].strip())
                        if hype >= 25: tramos.append((hype, base + "/1080p60/playlist.m3u8", max(0, seg - 12)))
                    except Exception: pass
    tramos.sort(reverse=True)
    print(f"vicecity: {len(tramos)} momentos de acción candidatos", flush=True)
    random.shuffle(tramos[:40])
    frases = frases_nuevas("vicecity", n)
    for i in range(n):
        lote = tramos[i * 6:(i + 1) * 6]  # 6 momentos x 5 s = ~30 s
        if len(lote) < 4: break
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); dst = d / f"vicecity-{i}.mp4"
            texto = frases.pop() if frases else random.choice(camp["textos"])
            if armar([(src, a, 5.0) for _, src, a in lote], texto, dst, d):
                entregar("vicecity", dst, texto.capitalize()); hechos.append(dst)
    return hechos


def labotella(n):
    """Escenas del video musical oficial con frases POV del brief."""
    camp = CAMPANAS["labotella"]
    with tempfile.TemporaryDirectory() as d:
        d = Path(d); fuente = d / "mv.mp4"
        privado = os.environ.get("LABOTELLA_VIDEO")  # el video oficial guardado en el repo PRIVADO gangaclips-campanas
        if privado and Path(privado).exists(): fuente = Path(privado); r = None
        else:  # YouTube suele bloquear las descargas desde GitHub
            r = subprocess.run(["yt-dlp", "-q", "--no-warnings", "-f", "bv*[height<=720][ext=mp4]/b[height<=720]", "-o", str(fuente), camp["video"]],
                               capture_output=True, text=True)
        if not fuente.exists():
            print("labotella: YouTube no dejó bajar el video oficial:", (r.stderr[-300:] if r else ""), flush=True)
            TG.texto("⚠️ GangaClips · Campaña La Botella: YouTube no dejó bajar el video oficial desde la nube. "
                     "Avísale a Claude para buscar otra forma.", bot=C.BOT)
            return []
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(fuente)],
                                   capture_output=True, text=True).stdout.strip() or 170)
        textos = frases_nuevas("labotella", n)
        for i, texto in enumerate(textos):
            # 3 escenas distintas del video de ~9 s cada una (~27 s), en orden, para que suene más canción
            inicios = sorted(random.sample(range(8, max(9, int(dur) - 12), 3), 3))
            dst = d / f"labotella-{i}.mp4"
            if armar([(str(fuente), a, 9) for a in inicios], texto, dst, d):
                entregar("labotella", dst, random.choice(camp["descripciones"]))
    return textos


def entregar(clave, f, descripcion):
    camp = CAMPANAS[clave]
    texto = f"{descripcion}\n\n" + C.hashtags_5([camp["codigo"]], camp["hashtags"].split())  # TikTok: máx. 5 (el código primero)
    TG.enviar(f, f"💰 CAMPAÑA {camp['nombre']}\n👉 Súbelo en TikTok desde @clipscuanticos (NO en gangaclips).\n"
                 f"🎵 AL SUBIR agrega este sonido (obligatorio): {camp['sonido']}\n"
                 f"📤 Después envíalo en Quanty → Clips → + Subir clip.", bot=C.BOT)
    TG.copiable(texto, bot=C.BOT)
    print("✓ entregado", f.name, flush=True)


if __name__ == "__main__":
    import datetime
    ap = argparse.ArgumentParser(); ap.add_argument("campana", choices=list(CAMPANAS)); ap.add_argument("--n", type=int, default=2)
    a = ap.parse_args()
    if datetime.date.today().isoformat() > CAMPANAS[a.campana]["hasta"]: print("campaña vencida"); sys.exit(0)
    (vicecity if a.campana == "vicecity" else labotella)(a.n)
