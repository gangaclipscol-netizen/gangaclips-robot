"""Videos LARGOS para YouTube: de cada stream, 1 o 2 "historias" completas (15-40 min) bien editadas.

Lo que funciona en los canales grandes (WestClips, ElReyWithTheClips, La Sapa: 25-50 mil vistas por video) es UN segmento
del stream sobre UNA cosa que pasó, con título concreto y miniatura con caras exageradas y 2-3 palabras gigantes.
Los "mejores momentos" mezclados y los títulos genéricos (WESTCOLAZOS, ResumChanty) no pasan de 100 vistas.

Uso:  python largo.py --canal westcol [--vod 0] [--solo-plan]
  1. escoge las historias con la transcripción + el chat de todo el stream (IA)
  2. corta el segmento en 1080p, quita lo muerto, pone un adelanto de 10 s al inicio y arma los capítulos
  3. hace la miniatura (cara más expresiva recortada + palabras gigantes) y el texto para YouTube
"""
import argparse, datetime, json, os, re, shutil, subprocess, sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import clips as C  # transcripcion, chat_picos, ia, lives, get, NO_CATEG

SALIDA = Path(os.environ.get("ADFLOW_SALIDA", AQUI / "salida-largos"))
TMP = Path(os.environ.get("ADFLOW_TMP", AQUI / "tmp-largos"))

GUIA = """LO QUE FUNCIONA EN YOUTUBE CON STREAMS (datos de los canales de clips con más vistas):
- UN video = UNA historia o actividad completa del stream (salir a la calle con el robot, la inauguración de la discoteca,
  la pelea, visitar un lugar, una conversación fuerte con un invitado), de 15 a 40 minutos. NO "mejores momentos" mezclados.
- Tiene que tener arranque, desarrollo y cierre, y momentos fuertes adentro (risas, sorpresa, conflicto, algo épico).
- El chat explotado (muchas risas KEKW/jaja, OOOO/NOOO) marca los momentos fuertes.
- TÍTULOS que funcionan: QUIÉN + QUÉ PASÓ (concreto) + CON QUIÉN o DÓNDE + final que da curiosidad, en mayúsculas lo clave:
    "REYDELACITY LLEGA AL CASERÍO DE YVNG CHIMI Y LO CONOCE POR PRIMERA VEZ… NO SE LO ESPERABA 😳"
    "ESTE ROBOT DESTRUYÓ MI PENTHOUSE Y ME PEGÓ EN LA CABEZA 🤣 | WESTCOL X BLESSD"
    "RETOS EN LA PISCINA CON LA GANGA 🔥"
  Los que fracasan suenan genéricos: "LOS MOMENTOS MÁS LOCOS DE WESTCOL", "HABLÓ DE X Y EXPLOTÓ EL CHAT".
- MINIATURA: 1 a 3 palabras GIGANTES que resumen la emoción o el suceso ("¿QUÉ PASÓ?", "NOS ASUSTÓ", "GANA LA PELEA",
  "DESTRUYE", "EN EL CAPITOLIO") + la cara del streamer con expresión exagerada."""


def plan(nombre, v, trans, chat, n=2, tono="español colombiano"):
    """La IA escoge hasta n historias del stream. Devuelve lista de dicts (inicio/fin absolutos en segundos del live)."""
    lineas = "\n".join(f"[{int(t)}] {x}" for t, x in trans)
    dur = int(trans[-1][0]) if trans else 0
    prompt = f"""Eres editor jefe de un canal de YouTube que sube los mejores segmentos de los streams de {nombre} (Kick).
Live: "{v['session_title']}" ({dur // 60} minutos). Transcripción automática (puede tener errores): cada línea [segundo].

{GUIA}

{C.CRITERIO_VIRAL}

NORMAS (la cuenta ya tiene una advertencia): nada de apuestas/casino/Stake, consumo de alcohol o drogas como tema central,
violencia real, contenido sexual, menores en pantalla ni datos personales. Si la historia lo tiene, escoge otra o ninguna.

Escoge HASTA {n} historias (mejor 1 excelente que 2 regulares; si ninguna merece 9 o 10, devuelve []).
Una historia puede abarcar 2 horas del live (por ejemplo todo un recorrido), pero el VIDEO dura 15 a 40 minutos:
tú escoges las PARTES que se conservan, en orden, y todo lo demás se corta. Para cada historia:
- "partes": lista de [inicio, fin] (segundos del live, en orden, cada una de 30 s o más) que SE CONSERVAN. Juntas deben
  sumar entre 900 y 2400 segundos (15 a 40 min). Deja fuera esperas, silencios, leer donaciones sin gracia, hablar de otra
  cosa, publicidad, casino, cargar el juego. Cada parte debe empezar y terminar en un punto natural (no a mitad de frase).
  Mejor 18 minutos intensos que 35 flojos. Si es una historia corta, puede ser una sola parte.
- "adelanto": [inicio, fin] de 6 a 12 segundos con el momento MÁS fuerte de la historia (va al principio del video)
- "capitulos": lista de {{"inicio": segundo del live, "titulo": "corto"}} (4 a 8, el primero en el inicio)
- "titulo" (máx 95 caracteres, estilo de los ejemplos, en {tono}, sin groserías), "texto_miniatura" (1 a 3 palabras, MAYÚSCULAS),
  "descripcion" (3-4 líneas que enganchen, sin groserías), "tags" (8 a 12), "puntaje" (1-10, sé exigente), "por_que".
Responde SOLO un JSON: lista de esos objetos, de mejor a peor.

{chat}

TRANSCRIPCIÓN:
{lineas}"""
    for intento in range(2):
        r = json.loads(re.search(r"\[.*\]", C.ia(prompt), re.S).group(0))
        buenas, cortas = [], []
        for h in r:
            try:
                t = tramos(h)
                total = sum(b - a for a, b in t)
                if not t or int(h.get("puntaje") or 0) < C.MIN_PUNTAJE:
                    print(f"  descartada: {h.get('titulo')} (puntaje {h.get('puntaje')})", flush=True)
                elif total < 840:
                    cortas.append(f"\"{h.get('titulo')}\" suma solo {total / 60:.0f} min"); print("  muy corta:", cortas[-1], flush=True)
                elif total <= 3000:
                    h["inicio"], h["fin"] = t[0][0], t[-1][1]; buenas.append(h)
            except Exception as e:
                print("  x historia mal armada:", e, flush=True)
        if buenas or not cortas: return buenas[:n]
        # muy cortas: una segunda vuelta pidiendo más partes buenas de la MISMA historia (el video debe durar 15-40 min)
        prompt += ("\n\nOJO: en tu respuesta anterior " + "; ".join(cortas) + ". El video debe sumar 15 a 40 minutos: agrega más"
                   " partes buenas de la misma historia (conversaciones, reacciones, recorrido) hasta pasar de 15 min, sin relleno.")
    return []


def tramos(h):
    """Partes que se conservan (segundos del live), en orden, sin solaparse y de 4 s o más."""
    partes = sorted((float(a), float(b)) for a, b in h.get("partes", []) if float(b) > float(a))
    out = []
    for a, b in partes:
        if out and a <= out[-1][1] + 2: out[-1] = (out[-1][0], max(out[-1][1], b))  # pegadas o solapadas: una sola
        else: out.append((a, b))
    return [(a, b) for a, b in out if b - a >= 4]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canal", required=True); ap.add_argument("--vod", type=int, default=0)
    ap.add_argument("--n", type=int, default=2); ap.add_argument("--solo-plan", action="store_true")
    a = ap.parse_args()
    canales = json.loads((AQUI / "gangaclips.json").read_text(encoding="utf-8"))
    canal = next(c for c in canales if a.canal.lower() in (c["kick"] + c["nombre"]).lower())
    nombre, slug = canal["nombre"], canal["kick"]
    v = [x for x in C.lives(slug) if not x.get("is_live")][a.vod]
    base = v["source"].rsplit("/", 1)[0]
    print(f"live: {v['session_title']} ({v['duration'] / 3.6e6:.1f} h)", flush=True)
    trans = C.transcripcion(base)
    chat = C.chat_picos(slug, v, 0, trans[-1][0] if trans else 0, paso=30)
    historias = plan(nombre, v, trans, chat, a.n)
    print(f"historias que valen la pena: {len(historias)}", flush=True)
    for h in historias:
        t = tramos(h); total = sum(b - a for a, b in t)
        print(f"\n[{h.get('puntaje')}] {h['titulo']}\n  min {h['inicio'] / 60:.0f} a {h['fin'] / 60:.0f} → {total / 60:.0f} min tras quitar lo muerto"
              f" ({len(t)} tramos) · miniatura: {h.get('texto_miniatura')}\n  {h.get('por_que', '')}", flush=True)
        for cap in h.get("capitulos", []): print(f"    · {cap.get('titulo')} (min {float(cap.get('inicio', 0)) / 60:.0f})")
    if a.solo_plan: return
    import largo_editar as E  # corte, adelanto, capítulos, miniatura y entrega
    for k, h in enumerate(historias):
        E.hacer(nombre, slug, v, base, h, tramos(h), TMP / f"{slug}-{k}", SALIDA)


if __name__ == "__main__":
    main()
