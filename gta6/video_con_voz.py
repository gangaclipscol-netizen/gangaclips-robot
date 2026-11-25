"""Video vertical de "GTA 6 al Día" con la VOZ DE SEBASTIÁN + fotos oficiales de Rockstar en movimiento + subtítulos.

Uso:
  py -3.13 video_con_voz.py gta6-01.json            -> usa Escritorio/Canal GTA 6/Mi voz/01.(m4a|mp3|ogg|opus|wav|aac|mp4)
  py -3.13 video_con_voz.py gta6-01.json --prueba   -> sin grabación: voz de prueba (solo para ver cómo queda)

Las fotos (oficiales, 4K) están en D:/AdFlow-Escaneo/gta6-fotos (NO van a git). En cada escena del guion se puede
poner "fotos": ["Vice_City", "Jason_and_Lucia_03"] (prefijos de nombre); si no, se escogen solas.
"""
import json, re, subprocess, sys, random, shutil, asyncio
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

AQUI = Path(__file__).resolve().parent
import os
FOTOS = Path(os.environ.get("ADFLOW_FOTOS", "D:/AdFlow-Escaneo/gta6-fotos"))
def _escritorio():
    """El Escritorio REAL de Windows (en el HP es D:/Desktop, no ~/Desktop)."""
    try:
        import ctypes
        b = ctypes.create_unicode_buffer(260); ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0, b)
        if b.value: return Path(b.value)
    except Exception: pass
    return Path.home() / "Desktop"


SALIDA = Path(os.environ.get("ADFLOW_SALIDA_GTA", _escritorio() / "Canal GTA 6"))
MIVOZ = SALIDA / "Mi voz"
TMP = Path(os.environ.get("ADFLOW_TMP", "D:/AdFlow-Escaneo/gta6-tmp"))
W, H, FPS = 1080, 1920, 30
ROSA, AMAR = (255, 79, 163), (255, 214, 10)
F_BLACK = os.environ.get("ADFLOW_FUENTE", "C:/Windows/Fonts/ariblk.ttf")
POOL = ["Vice_City", "Jason_and_Lucia", "Leonida_Keys", "Lucia_Caminos", "Jason_Duval", "Port_Gellhorn",
        "Grassrivers", "Ambrosia", "Mount_Kalaga", "Cal_Hampton", "Boobie_Ike", "Raul_Bautista", "Real_Dimez"]


def sh(args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    if r.returncode:
        print(r.stderr[-1500:]); raise SystemExit("falló: " + " ".join(map(str, args[:6])))
    return r.stdout


def duracion(f):
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)]).strip())


def quitar_emoji(t):
    return re.sub(r"[^\w\s¿?¡!.,:;%/()\-+'\"áéíóúÁÉÍÓÚñÑüÜ]", "", t).strip()


# ---------- voz ----------
def voz_de_prueba(texto, dst):
    import edge_tts
    asyncio.run(edge_tts.Communicate(texto, "en-US-AndrewMultilingualNeural", rate="+8%").save(str(dst)))


REFERENCIA = Path(os.environ.get("ADFLOW_REFERENCIA", "D:/AdFlow-Escaneo/mi-voz/referencia.wav"))  # 12 s de la voz de Sebastián (sale de sus grabaciones)


def _py_voz_local():
    """Python del entorno con Chatterbox instalado (PC: D:/AdFlow-Escaneo/venv-voz; nube: ADFLOW_VOZ_PY)."""
    for p in (os.environ.get("ADFLOW_VOZ_PY"), "D:/AdFlow-Escaneo/venv-voz/Scripts/python.exe"):
        if p and Path(p).exists(): return p


def voz_clonada(texto, dst):
    """La voz de Sebastián clonada (Chatterbox multilingüe, licencia MIT). Primero el Space gratis de Hugging Face;
    si no hay cupo (o en la nube con ADFLOW_VOZ_SOLO_LOCAL), el mismo modelo corre en esta máquina: no depende de nadie."""
    import time, numpy as np, wave
    frases, cur = [], ""
    for fr in re.split(r"(?<=[.!?])\s+", texto.strip()):
        if len(cur) + len(fr) < 260: cur = (cur + " " + fr).strip()
        else: frases.append(cur); cur = fr
    if cur: frases.append(cur)
    if not os.environ.get("ADFLOW_VOZ_SOLO_LOCAL"):
        try:
            return _voz_hf(frases, dst)
        except Exception as e:
            print("  Hugging Face sin cupo/caído:", str(e)[:120], "→ voz en esta máquina", flush=True)
    py = _py_voz_local()
    if not py: raise SystemExit("no se pudo generar la voz clonada (sin cupo y sin Chatterbox local)")
    fj = Path(str(dst) + ".frases.json"); fj.write_text(json.dumps(frases, ensure_ascii=False), encoding="utf-8")
    crudo = Path(str(dst) + ".local.wav")
    subprocess.run([py, str(Path(__file__).with_name("voz_local.py")), str(fj), str(REFERENCIA), str(crudo), "0.55", "0.5"], check=True)
    sh(["ffmpeg", "-y", "-i", str(crudo), "-ar", "24000", "-ac", "1", str(dst)])


GH = os.environ.get("ADFLOW_GH") or shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"  # en la nube: gh del sistema
VOZ_NUBE = AQUI / "voz-nube.json"   # guion -> id de la corrida de GitHub que hace su voz


def _gh(*args):
    r = subprocess.run([GH, *args, "-R", "Raichu-hub/adflow-microapps"], capture_output=True, text=True, encoding="utf-8")
    if r.returncode: raise RuntimeError((r.stderr or r.stdout)[:200])
    return r.stdout


def voz_nube(num, t):
    """Voz clonada de Sebastián hecha por NUESTRA herramienta (voz/clonar.py en GitHub Actions, gratis, 7 GB RAM):
    cada escena con su ánimo. No bloquea: la primera vez la pide y sale; en la siguiente ronda (20 min) la recoge."""
    est = json.loads(VOZ_NUBE.read_text(encoding="utf-8")) if VOZ_NUBE.exists() else {}
    rid = est.get(num)
    if rid:
        info = json.loads(_gh("run", "view", str(rid), "--json", "status,conclusion"))
        if info["status"] != "completed": raise SystemExit(f"voz {num}: GitHub la está haciendo (corrida {rid})")
        if info["conclusion"] == "success":
            d = t / "nube"; shutil.rmtree(d, ignore_errors=True)
            _gh("run", "download", str(rid), "-n", f"voz-{num}", "-D", str(d))
            w = next(d.glob("*.wav"), None)
            if w: return w
        print(f"  voz {num}: la corrida {rid} falló, la pido otra vez", flush=True)
    _gh("workflow", "run", "voz-guiones.yml", "-f", f"numeros={num}", "-f", "emocion=0.75")
    import time; time.sleep(8)
    rid = json.loads(_gh("run", "list", "--workflow", "voz-guiones.yml", "-L", "1", "--json", "databaseId"))[0]["databaseId"]
    est[num] = rid; VOZ_NUBE.write_text(json.dumps(est, indent=1), encoding="utf-8")
    # se guarda en el repo para que el PC y la nube sepan que esa voz ya se pidió (y no la pidan dos veces)
    repo = AQUI.parent.parent
    subprocess.run(["git", "add", str(VOZ_NUBE)], cwd=repo)
    subprocess.run(["git", "-c", "user.name=GangaClips", "-c", "user.email=actions@users.noreply.github.com", "commit", "-q", "-m", f"voz {num} pedida"], cwd=repo)
    subprocess.run(["git", "pull", "-q", "--rebase"], cwd=repo); subprocess.run(["git", "push", "-q"], cwd=repo)
    raise SystemExit(f"voz {num}: pedida a GitHub (corrida {rid}), el video sale en la próxima ronda")


def _voz_hf(frases, dst):
    import time, numpy as np, wave
    from gradio_client import Client, handle_file
    tok = (Path.home() / ".hf.token").read_text().strip()
    c = Client("ResembleAI/Chatterbox-Multilingual-TTS", token=tok, verbose=False)
    partes, sr = [], 24000
    for fr in frases:
        for intento in range(3):
            try:
                r = c.predict(fr, "es", handle_file(str(REFERENCIA)), 0.55, 0.7, 7, 0.5, api_name="/generate_tts_audio"); break
            except Exception as e:
                print("  reintento voz:", str(e)[:120], flush=True)
                if "ZeroGPU" in str(e) or "quota" in str(e).lower(): raise
                time.sleep(20)
        else: raise RuntimeError("Hugging Face no respondió")
        tmpw = Path(str(dst) + ".part.wav")
        sh(["ffmpeg", "-y", "-i", r, "-ar", str(sr), "-ac", "1", str(tmpw)])
        with wave.open(str(tmpw)) as wv: partes.append(np.frombuffer(wv.readframes(wv.getnframes()), np.int16))
        partes.append(np.zeros(int(0.12 * sr), np.int16))
    with wave.open(str(dst), "wb") as wv:
        wv.setnchannels(1); wv.setsampwidth(2); wv.setframerate(sr); wv.writeframes(np.concatenate(partes).tobytes())


def limpiar_audio(src, dst):
    # quita ruido de fondo, recorta silencios del inicio/fin y deja el volumen como en TikTok
    sh(["ffmpeg", "-y", "-i", str(src), "-vn", "-af",
        "highpass=f=80,afftdn=nf=-25,silenceremove=start_periods=1:start_threshold=-45dB,"
        "areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
        "loudnorm=I=-14:TP=-1.5:LRA=11", "-ar", "48000", "-ac", "1", str(dst)])


def palabras(audio):
    from faster_whisper import WhisperModel
    import numpy as np
    # se le pasa el audio ya decodificado (16 kHz mono) para no depender de la versión de PyAV
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(audio), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True).stdout
    data = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
    m = WhisperModel("small", device="cpu", compute_type="int8")
    segs, _ = m.transcribe(data, language="es", word_timestamps=True, vad_filter=True)
    return [(w.word.strip(), w.start, w.end) for s in segs for w in s.words if w.word.strip()]


def alinear_guion(pals, texto):
    """Subtítulos con las palabras EXACTAS del guion: el audio solo da los tiempos.
    (La IA que escucha a veces oye mal, sobre todo con voz clonada.)"""
    import difflib
    guion = texto.split()
    if not pals or not guion: return pals
    a = [_norm(p[0]) for p in pals]; b = [_norm(w) for w in guion]
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "delete": continue
        if i2 > i1: ini, fin = pals[i1][1], pals[i2 - 1][2]
        else:  # palabras del guion que no se oyeron: entre la anterior y la siguiente
            ini = out[-1][2] if out else (pals[0][1])
            fin = pals[i1][1] if i1 < len(pals) else ini + 0.3 * (j2 - j1)
            fin = max(fin, ini + 0.12 * (j2 - j1))
        if op == "equal":
            out += [(guion[j1 + k], pals[i1 + k][1], pals[i1 + k][2]) for k in range(j2 - j1)]
        else:
            paso = (fin - ini) / max(1, j2 - j1)
            out += [(guion[j1 + k], ini + k * paso, ini + (k + 1) * paso) for k in range(j2 - j1)]
    return out


def tiempos_escenas(escenas, pals, total):
    """Reparte las palabras dichas entre las escenas, en proporción a las palabras del guion."""
    cuenta = [len(e["voz"].split()) for e in escenas]
    tot, n = sum(cuenta), len(pals)
    inicios, acum = [0.0], 0
    for c in cuenta[:-1]:
        acum += c
        i = min(n - 1, round(acum / tot * n))
        inicios.append(max(inicios[-1] + 0.8, pals[i][1] - 0.1))
    return [(inicios[k], inicios[k + 1] if k + 1 < len(inicios) else total) for k in range(len(inicios))]


# ---------- fluidez: quita trabas, "eh", repeticiones y pausas largas ----------
MULETILLAS = {"eh", "ehh", "em", "emm", "mm", "mmm", "ah", "ahh", "este", "eeh"}


def _norm(w):
    return re.sub(r"[^\wáéíóúñü]", "", w.lower())


def fluidez(voz, dst, pals=None):
    import numpy as np, wave
    pals = pals or palabras(voz)
    n = len(pals); quitar = set()
    ws = [_norm(p[0]) for p in pals]
    for i, w in enumerate(ws):
        if w in MULETILLAS and not (w == "este" and i + 1 < n and ws[i + 1] not in MULETILLAS): quitar.add(i)
        if not w: quitar.add(i)
    # arranques repetidos: "sale el, sale el diecinueve" -> se queda la última vez
    i = 0
    while i < n:
        hecho = False
        for k in range(6, 0, -1):
            for gap in range(0, 4):
                j = i + k + gap
                if j + k <= n and ws[i:i + k] == ws[j:j + k] and all(ws[i:i + k]):
                    if k == 1 and gap > 0: continue
                    quitar.update(range(i, j)); i = j; hecho = True; break
            if hecho: break
        if not hecho: i += 1
    seg = []
    for i, (w, a, b) in enumerate(pals):
        if i in quitar: continue
        a, b = max(0, a - 0.06), b + 0.09
        if seg and a - seg[-1][1] <= 0.22: seg[-1][1] = max(seg[-1][1], b)
        else: seg.append([a, b])
    with wave.open(str(voz)) as wv:
        sr = wv.getframerate(); x = np.frombuffer(wv.readframes(wv.getnframes()), np.int16).astype(np.float32)
    out, fade = [], int(0.012 * sr)
    pausa = np.zeros(int(0.16 * sr), np.float32)
    for a, b in seg:
        c = x[int(a * sr):int(b * sr)].copy()
        if len(c) > 2 * fade:
            c[:fade] *= np.linspace(0, 1, fade); c[-fade:] *= np.linspace(1, 0, fade)
        out += [c, pausa]
    y = np.concatenate(out) if out else x
    with wave.open(str(dst), "wb") as wv:
        wv.setnchannels(1); wv.setsampwidth(2); wv.setframerate(sr); wv.writeframes(np.clip(y, -32768, 32767).astype(np.int16).tobytes())
    print(f"fluidez: quité {len(quitar)} trabas/muletillas, {len(x)/sr:.1f}s -> {len(y)/sr:.1f}s", flush=True)
    print("  quitado:", " | ".join(pals[i][0] for i in sorted(quitar))[:400], flush=True)


# ---------- imágenes ----------
def escoger_fotos(esc, k, usadas):
    pref = esc.get("fotos") or [POOL[(k * 3 + i) % len(POOL)] for i in range(3)]
    cands = []
    for p in pref:
        cands += sorted(f for f in FOTOS.glob(p + "*.jpg") if f not in usadas)
    random.Random(k).shuffle(cands)
    return cands or sorted(FOTOS.glob("Vice_City*.jpg"))


def clip_foto(img, dur, k, dst):
    """Foto 16:9 -> vertical con movimiento (paneo o zoom), sin temblor."""
    s = 1.18
    hh = int(H * s); ww = int(hh * 16 / 9) // 2 * 2
    cw = int(hh * 9 / 16) // 2 * 2
    modo = k % 3
    if modo == 0:   # paneo izquierda -> derecha
        a, b = 0.25, 0.65
        vf = f"scale={ww}:{hh},crop={cw}:{hh}:x='({ww}-{cw})*({a}+{b - a}*t/{dur:.3f})':y=0,scale={W}:{H}"
    elif modo == 1:  # paneo derecha -> izquierda
        a, b = 0.70, 0.35
        vf = f"scale={ww}:{hh},crop={cw}:{hh}:x='({ww}-{cw})*({a}+({b - a})*t/{dur:.3f})':y=0,scale={W}:{H}"
    else:            # zoom lento al centro
        fr = int(dur * FPS) + 1
        vf = (f"scale={ww * 2}:{hh * 2},crop={cw * 2}:{hh * 2},"
              f"zoompan=z='1+0.12*on/{fr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={fr}:s={W}x{H}:fps={FPS}")
    sh(["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(img), "-vf", vf + ",format=yuv420p",
        "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-t", f"{dur:.3f}", str(dst)])


def texto_ajustado(d, txt, font_path, max_w, size, min_size=40):
    while size > min_size:
        f = ImageFont.truetype(font_path, size)
        lineas, cur = [], ""
        for w in txt.split():
            t = (cur + " " + w).strip()
            if d.textlength(t, font=f) <= max_w: cur = t
            else: lineas.append(cur); cur = w
        lineas.append(cur)
        if len(lineas) <= 3: return f, lineas
        size -= 6
    return ImageFont.truetype(font_path, min_size), [txt]


def overlay(esc, marca, dst):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = Image.new("L", (1, H))
    for y in range(H):  # oscurece arriba y abajo para que se lea
        a = 170 * max(0, 1 - y / 760) + 150 * max(0, (y - 1150) / 770)
        g.putpixel((0, y), int(min(200, a)))
    im.paste(Image.new("RGBA", (W, H), (0, 0, 0, 255)), (0, 0), g.resize((W, H)))
    d = ImageDraw.Draw(im)
    # marca
    fm = ImageFont.truetype(F_BLACK, 34)
    tm = marca.upper()
    tw = d.textlength(tm, font=fm)
    d.rounded_rectangle((60, 170, 60 + tw + 44, 228), 14, fill=ROSA)
    d.text((82, 175), tm, font=fm, fill="white")
    tipo = esc.get("tipo")
    y = 270
    if tipo == "dato" and esc.get("numero"):
        f, ls = texto_ajustado(d, quitar_emoji(esc["numero"]).upper(), F_BLACK, W - 120, 190, 90)
        for l in ls:
            d.text((60, y), l, font=f, fill=AMAR, stroke_width=8, stroke_fill="black"); y += int(f.size * 1.05)
        sub = quitar_emoji(esc.get("texto", ""))
    elif tipo == "cierre":
        sub = quitar_emoji(esc.get("sub", ""))
        f, ls = texto_ajustado(d, "SÍGUENOS", F_BLACK, W - 120, 130)
        for l in ls:
            d.text((60, y), l, font=f, fill=AMAR, stroke_width=8, stroke_fill="black"); y += int(f.size * 1.08)
    else:
        f, ls = texto_ajustado(d, quitar_emoji(esc.get("titulo", "")).upper(), F_BLACK, W - 120, 110)
        for l in ls:
            d.text((60, y), l, font=f, fill="white", stroke_width=7, stroke_fill="black"); y += int(f.size * 1.08)
        sub = quitar_emoji(esc.get("texto", "") if tipo == "reto" else "")
    if sub:
        f2, ls2 = texto_ajustado(d, sub, F_BLACK, W - 120, 52, 36)
        y += 10
        for l in ls2:
            d.text((60, y), l, font=f2, fill="white", stroke_width=5, stroke_fill="black"); y += int(f2.size * 1.2)
    im.save(dst)


# ---------- subtítulos ----------
def ass_tiempo(t):
    h, r = divmod(max(0, t), 3600); m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def subtitulos(pals, dst):
    cab = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\n"
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
           "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
           "Style: S,Arial Black,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,7,3,2,70,70,560,1\n\n"
           "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    grupos, g = [], []
    for p in pals:
        g.append(p)
        if len(g) == 3 or re.search(r"[.,!?;:]$", p[0]): grupos.append(g); g = []
    if g: grupos.append(g)
    ev = []
    for gi, g in enumerate(grupos):
        fin_g = grupos[gi + 1][0][1] if gi + 1 < len(grupos) else g[-1][2] + 0.4
        for i, (w, a, b) in enumerate(g):
            fin = g[i + 1][1] if i + 1 < len(g) else min(fin_g, b + 0.6)
            txt = " ".join(("{\\c&H0AD6FF&}" + quitar_emoji(x[0]).upper() + "{\\c&HFFFFFF&}") if j == i else quitar_emoji(x[0]).upper()
                           for j, x in enumerate(g))
            ev.append(f"Dialogue: 0,{ass_tiempo(a)},{ass_tiempo(fin)},S,,0,0,0,,{txt}")
    dst.write_text(cab + "\n".join(ev) + "\n", encoding="utf-8")


# ---------- principal ----------
# Ánimo de cada escena -> indicación de tono para Eleven v4 (no se lee en voz alta). Estilo: noticia intrigante, con suspenso.
TONO_11 = {"normal": "tono serio de noticia, con suspenso", "suspenso": "bajando la voz, intrigante", "sorprendido": "sorprendido, con fuerza",
           "emocionado": "firme, con energía", "vendedor": "convincente, con energía", "feliz": "entusiasmado",
           "miedo": "tenso, en voz baja", "triste": "serio y pausado"}


def voz_elevenlabs(texto, dst):
    """Voz clonada de Sebastián en ElevenLabs (voice_id en ~/.adflow-elevenlabs.json). Gasta ~1 crédito por carácter."""
    import requests, datetime
    c = json.loads((Path.home() / ".adflow-elevenlabs.json").read_text(encoding="utf-8"))
    uso_f = AQUI / "elevenlabs-uso.json"  # en el repo: videos hechos por mes (tope acordado: 60/mes; el resto es de otra persona)
    uso = json.loads(uso_f.read_text(encoding="utf-8")) if uso_f.exists() else {}
    mes = datetime.date.today().strftime("%Y-%m"); tope = int(c.get("tope_mes", 60))
    if uso.get(mes, 0) >= tope: raise SystemExit(f"ElevenLabs: ya van {uso[mes]} videos este mes (tope {tope}); sigue el próximo mes")
    hoy = datetime.date.today().isoformat(); tope_dia = int(c.get("tope_dia", 2))
    if uso.get(hoy, 0) >= tope_dia: raise SystemExit(f"ElevenLabs: ya van {uso[hoy]} videos hoy (tope {tope_dia}); sigue mañana")
    r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{c['voice_id']}?output_format=mp3_44100_128",
                      headers={"xi-api-key": c["key"]}, timeout=300,
                      json={"text": texto, "model_id": c.get("modelo", "eleven_v4"),
                            "voice_settings": {"stability": 0.35, "similarity_boost": 0.9, "style": 0.8, "use_speaker_boost": True}})
    if not r.ok: raise SystemExit(f"ElevenLabs: {r.status_code} {r.text[:200]}")
    Path(dst).write_bytes(r.content)
    uso[mes] = uso.get(mes, 0) + 1; uso[hoy] = uso.get(hoy, 0) + 1
    uso = {k: v for k, v in uso.items() if len(k) == 7 or k >= (datetime.date.today() - datetime.timedelta(days=7)).isoformat()}
    uso_f.write_text(json.dumps(uso, indent=1), encoding="utf-8")
    print(f"  voz ElevenLabs: {len(texto)} caracteres · video {uso[mes]}/{tope} del mes", flush=True)
    return Path(dst)


def hacer(guion, prueba=False, clon=False):
    g = Path(guion)
    d = json.loads(g.read_text(encoding="utf-8"))
    num = re.search(r"-(\d+)$", g.stem).group(1)
    t = TMP / g.stem
    shutil.rmtree(t, ignore_errors=True); t.mkdir(parents=True)
    esc = d["escenas"]
    if prueba:
        src = t / "prueba.mp3"; voz_de_prueba(" ".join(e["voz"] for e in esc), src)
    else:
        srcs = [f for f in MIVOZ.glob(num + ".*") if f.suffix.lower() in (".m4a", ".mp3", ".ogg", ".opus", ".wav", ".aac", ".mp4", ".3gp", ".amr")]
        if not srcs and clon and (Path.home() / ".adflow-elevenlabs.json").exists():  # tu voz clonada en ElevenLabs (rápida, plan Creator)
            srcs = [voz_elevenlabs(" ".join(f"[{TONO_11.get(e.get('animo', 'normal'), TONO_11['normal'])}] {e['voz']}" for e in d["escenas"]),
                                   t / f"{num}.clon.mp3")]
        if not srcs and clon and os.environ.get("ADFLOW_VOZ_PY"):  # servidor propio (AWS): la voz se hace aquí mismo
            subprocess.run([os.environ["ADFLOW_VOZ_PY"], str(AQUI.parent / "voz" / "clonar.py"), "--guiones", str(g), "--dir", str(t)],
                           check=True, cwd=str(AQUI.parent / "voz"))
            srcs = [t / f"{num}.clon.wav"]
        if not srcs and clon:
            srcs = [voz_nube(num, t)]  # nuestra voz clonada con ánimo por escena (GitHub Actions; sin Hugging Face)
        if not srcs: raise SystemExit(f"No está la grabación: {MIVOZ}\\{num}.m4a")
        src = srcs[0]
    cruda = t / "cruda.wav"; limpiar_audio(src, cruda)
    voz = t / "voz.wav"
    if prueba: shutil.copy(cruda, voz)
    elif src.name == "clon.wav" or ".clon." in src.name: shutil.copy(cruda, voz)  # voz clonada: no se recorta
    else: fluidez(cruda, voz)
    total = duracion(voz) + 0.5
    pals = palabras(voz)
    print("palabras oídas:", len(pals), flush=True)
    if src.name == "clon.wav" or ".clon." in src.name or prueba:
        pals = alinear_guion(pals, " ".join(e["voz"] for e in esc))
    tiempos = tiempos_escenas(esc, pals, total)
    partes, usadas = [], set()
    for k, (e, (a, b)) in enumerate(zip(esc, tiempos)):
        dur = b - a
        fotos = escoger_fotos(e, k, usadas)
        n = max(1, round(dur / 3.2))
        clips = []
        for j in range(n):
            f = fotos[j % len(fotos)]; usadas.add(f)
            c = t / f"c{k}_{j}.mp4"; clip_foto(f, dur / n, k + j, c); clips.append(c)
        ov = t / f"o{k}.png"; overlay(e, d.get("marca", "GTA 6 al Día"), ov)
        lst = t / f"l{k}.txt"; lst.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8")
        p = t / f"p{k}.mp4"
        sh(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(ov), "-filter_complex",
            "[0:v][1:v]overlay=0:0,format=yuv420p", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
            "-t", f"{dur:.3f}", str(p)])
        partes.append(p)
    lst = t / "todo.txt"; lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in partes), encoding="utf-8")
    subtitulos(pals, t / "subs.ass")
    SALIDA.mkdir(exist_ok=True)
    final = SALIDA / (("PRUEBA - " if prueba else "") + d["archivo"])
    sh(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "todo.txt", "-i", "voz.wav", "-vf",
        "subtitles=subs.ass:fontsdir=" + os.environ.get("ADFLOW_FONTSDIR", "C\\\\:/Windows/Fonts"), "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(final)], cwd=t)
    print("listo:", final, flush=True)
    return final


if __name__ == "__main__":
    hacer(sys.argv[1], "--prueba" in sys.argv, "--clon" in sys.argv)
