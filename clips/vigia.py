"""Vigía de GangaClips: revisa en Kick si algún streamer de gangaclips.json terminó un live que aún no tiene clips.
Es liviano (solo consulta la API de Kick, sin ffmpeg ni IA). Imprime los slugs de Kick con lives nuevos, uno por línea;
el workflow vigia.yml arranca el robot completo (gangaclips.yml) si imprime algo.
"""
import datetime, json, sys, urllib.request
from pathlib import Path

AQUI = Path(__file__).resolve().parent
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"}


def lives(slug):
    req = urllib.request.Request(f"https://kick.com/api/v2/channels/{slug}/videos", headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace"))


def reciente(v, horas=24):
    """El live terminó hace menos de `horas` (start_time de Kick va en UTC; duration en ms)."""
    try:
        ini = datetime.datetime.strptime(v["start_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return True
    fin = ini + datetime.timedelta(milliseconds=v.get("duration") or 0)
    return datetime.datetime.now(datetime.timezone.utc) - fin < datetime.timedelta(hours=horas)


def main():
    canales = json.loads((AQUI / "gangaclips.json").read_text(encoding="utf-8"))
    estado = AQUI / "gangaclips-estado.json"
    est = json.loads(estado.read_text(encoding="utf-8")) if estado.exists() else {}
    for c in canales:
        slug = c.get("kick")
        if not slug: continue
        try:
            vids = [v for v in lives(slug) if not v.get("is_live")][:3]  # igual que clips.py: los 3 más recientes
        except Exception as e:
            print(f"{slug}: no pude revisar ({e})", file=sys.stderr); continue
        nuevos = [v for v in vids if v["slug"] not in set(est.get(slug, [])) and reciente(v)]
        if nuevos:
            print(slug)
            print(f"{slug}: {len(nuevos)} live(s) nuevo(s): {nuevos[0].get('session_title', '')}", file=sys.stderr)


if __name__ == "__main__":
    main()
