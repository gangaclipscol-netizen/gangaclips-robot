"""Banco de caras para las miniaturas: de varios streams de un streamer saca los cuadros donde sale GRANDE, NÍTIDO y
con buena expresión, los recorta (sin fondo) y guarda los mejores en clips/caras/<slug>/ con su emoción.

Así la miniatura nunca depende de que en ESE stream la cara salga bien (muchas veces sale chiquita, movida o oscura).
Uso:  python caras.py --canal westcol [--lives 4] [--por-live 60] [--guardar 12]
"""
import argparse, base64, io, json, os, subprocess, sys, tempfile, urllib.request
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import clips as C


def nitidez(img_bgr):
    import cv2
    return float(cv2.Laplacian(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def candidatos(stream, dur, n, carpeta, prefijo):
    """n cuadros repartidos en el live; deja solo los que tienen una cara grande (>= 16% del alto) y nítida."""
    import cv2
    det = None; buenos = []
    # Leer la lista de pedazos UNA vez y sacar cada cuadro de su pedazo (.ts): con un live de 72 h, abrir la lista
    # entera para cada cuadro tardaba horas.
    pl = urllib.request.urlopen(urllib.request.Request(stream, headers=C.UA), timeout=60).read().decode()
    pedazos, acum, dur_p = [], 0.0, 0.0
    for l in pl.splitlines():
        if l.startswith("#EXTINF:"): dur_p = float(l.split(":")[1].split(",")[0])
        elif l and not l.startswith("#"): pedazos.append((acum, dur_p, stream.rsplit("/", 1)[0] + "/" + l)); acum += dur_p
    if not pedazos: return []
    for k in range(n):
        t = acum * (k + 0.5) / n
        ini, d, url = next((p for p in pedazos if p[0] <= t < p[0] + p[1]), pedazos[-1])
        f = carpeta / f"{prefijo}-{k:03d}.jpg"
        try:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0, t - ini):.2f}", "-i", url, "-frames:v", "1", "-q:v", "2", str(f)],
                           capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            continue  # pedazo lento: se salta
        im = cv2.imread(str(f)) if f.exists() else None
        if im is None: continue
        if det is None: det = cv2.FaceDetectorYN.create(str(AQUI / "modelos" / "yunet.onnx"), "", (im.shape[1], im.shape[0]), 0.8)
        det.setInputSize((im.shape[1], im.shape[0]))
        _, caras = det.detect(im)
        if caras is None or not len(caras): f.unlink(); continue
        x, y, w, h = max(caras, key=lambda c: c[2] * c[3])[:4]
        if h < im.shape[0] * 0.16: f.unlink(); continue  # cara chiquita: no sirve para miniatura
        cara = im[max(0, int(y)):int(y + h), max(0, int(x)):int(x + w)]
        nit = nitidez(cara)
        if nit < 60: f.unlink(); continue  # movida o borrosa
        buenos.append((f, nit, (float(x), float(y), float(w), float(h))))
    print(f"  {prefijo}: {len(buenos)} cuadros con la cara grande y nítida", flush=True)
    return buenos


def calificar(nombre, cuadros):
    """La IA mira los cuadros y dice cuáles sirven para miniatura (streamer, expresión, calidad) y su emoción."""
    key = os.environ.get("GEMINI_API_KEY")
    from PIL import Image
    salida = {}
    for i in range(0, len(cuadros), 16):
        lote = cuadros[i:i + 16]
        partes = [{"text": f"Cuadros numerados 0 a {len(lote) - 1} de streams de {nombre}. Para MINIATURAS de YouTube califica "
                           f"cada uno del 1 al 10: ¿sale {nombre} (no otra persona), grande, nítido, bien iluminado y con una expresión "
                           "fuerte y llamativa (risa, sorpresa, grito, cara de 'no puede ser', seriedad intensa)? Caras neutras, "
                           "ojos cerrados, boca a medio hablar, borrosas o tapadas = nota baja. Nada de alcohol, armas ni logos de casino.\n"
                           "Responde SOLO un JSON: lista de {\"i\": n, \"nota\": n, \"emocion\": \"risa|sorpresa|serio|enojo|feliz\"}"}]
        for f, _, _ in lote:
            im = Image.open(f).convert("RGB"); im.thumbnail((640, 360)); b = io.BytesIO(); im.save(b, "JPEG", quality=80)
            partes.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(b.getvalue()).decode()}})
        body = {"contents": [{"parts": partes}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
        for modelo in ("gemini-flash-latest", "gemini-flash-lite-latest", "gemini-flash-latest"):
            try:
                req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={key}",
                                             json.dumps(body).encode(), {"Content-Type": "application/json"})
                r = json.loads(json.loads(urllib.request.urlopen(req, timeout=180).read())["candidates"][0]["content"]["parts"][0]["text"])
                for x in r:
                    salida[lote[int(x["i"])][0]] = (int(x.get("nota") or 0), x.get("emocion", "feliz"))
                break
            except Exception as e:
                print(f"  IA ocupada ({modelo}): {str(e)[:80]}", flush=True)
                import time; time.sleep(25)
    return salida


def recortar(f, caja, dst):
    """Persona sin fondo, alrededor de su cara (medio cuerpo), con bordes suavizados."""
    from PIL import Image, ImageFilter
    from rembg import remove, new_session
    foto = Image.open(f).convert("RGB")
    x, y, w, h = caja
    ancho = w * 4.6
    foto = foto.crop((max(0, int(x + w / 2 - ancho / 2)), max(0, int(y - h * 0.9)),
                      min(foto.width, int(x + w / 2 + ancho / 2)), min(foto.height, int(y + h * 5.5))))
    p = remove(foto, session=new_session("u2net_human_seg"), alpha_matting=True)
    a = p.split()[3].filter(ImageFilter.GaussianBlur(0.8)); p.putalpha(a)
    caja2 = p.getbbox()
    if not caja2: return False
    p.crop(caja2).save(dst)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canal", required=True); ap.add_argument("--lives", type=int, default=4)
    ap.add_argument("--por-live", type=int, default=60); ap.add_argument("--guardar", type=int, default=12)
    a = ap.parse_args()
    canales = json.loads((AQUI / "gangaclips.json").read_text(encoding="utf-8"))
    canal = next(c for c in canales if a.canal.lower() in (c["kick"] + c["nombre"]).lower())
    nombre, slug = canal["nombre"], canal["kick"]
    destino = AQUI / "caras" / slug; destino.mkdir(parents=True, exist_ok=True)
    vids = [v for v in C.lives(slug) if not v.get("is_live")][:a.lives]
    with tempfile.TemporaryDirectory() as d:
        d = Path(d); todos = []
        for k, v in enumerate(vids):
            base = v["source"].rsplit("/", 1)[0]
            print(f"live {k + 1}/{len(vids)}: {v['session_title']}", flush=True)
            todos += candidatos(base + "/1080p60/playlist.m3u8", (v.get("duration") or 0) / 1000, a.por_live, d, f"v{k}")
        todos.sort(key=lambda x: -x[1]); todos = todos[:64]  # los 64 más nítidos van a la IA
        notas = calificar(nombre, todos)
        mejores = sorted([t for t in todos if notas.get(t[0], (0,))[0] >= 7], key=lambda t: -notas[t[0]][0])[:a.guardar]
        indice = []
        for i, (f, nit, caja) in enumerate(mejores):
            nota, emo = notas[f]
            dst = destino / f"{emo}-{i:02d}.png"
            if recortar(f, caja, dst): indice.append({"archivo": dst.name, "emocion": emo, "nota": nota})
        (destino / "indice.json").write_text(json.dumps(indice, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"banco de {nombre}: {len(indice)} caras → {destino}", flush=True)
        mandar_muestra(nombre, destino, indice)


def mandar_muestra(nombre, destino, indice):
    """Mosaico de las caras guardadas a Telegram, para revisarlas desde el celular."""
    import requests
    from PIL import Image, ImageDraw
    import telegram_envio as TG
    cfg = TG._cfg(C.BOT)
    if not (cfg and cfg.get("token") and cfg.get("chat_id")): return
    if not indice:
        TG.texto(f"🖼️ Banco de caras de {nombre}: no encontré cuadros buenos (cara grande, nítida y expresiva). "
                 "Se puede repetir con más lives.", bot=C.BOT); return
    lado = 300; cols = min(4, len(indice)); filas = (len(indice) + cols - 1) // cols
    lienzo = Image.new("RGB", (cols * lado, filas * lado), (30, 30, 36)); d = ImageDraw.Draw(lienzo)
    for k, x in enumerate(indice):
        im = Image.open(destino / x["archivo"]).convert("RGBA"); im.thumbnail((lado - 10, lado - 30))
        cx, cy = (k % cols) * lado, (k // cols) * lado
        lienzo.paste(im, (cx + (lado - im.width) // 2, cy + 5), im)
        d.text((cx + 8, cy + lado - 22), f"{x['emocion']} · {x['nota']}/10", fill=(83, 252, 24))
    tmp = destino / "_muestra.jpg"; lienzo.save(tmp, "JPEG", quality=85)
    with open(tmp, "rb") as f:
        requests.post(f"https://api.telegram.org/bot{cfg['token']}/sendPhoto",
                      data={"chat_id": cfg["chat_id"], "caption": f"🖼️ Banco de caras de {nombre}: {len(indice)} fotos para las miniaturas "
                                                                     "de los videos largos. Si alguna no te gusta, dime cuál y la quito."},
                      files={"photo": f}, timeout=60)
    tmp.unlink()


if __name__ == "__main__":
    main()
