"""
Deja las fotos de UNA publicación exactamente en el orden indicado (para corregir casos puntuales).
ITEM = MLA..., FOTOS = ids de foto separados por coma (en orden; la primera es la portada).
DRY_RUN=1 (por defecto): solo muestra las fotos actuales y cómo quedarían.
"""
import os

from ml.cliente import ML, log

dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
iid = os.environ["ITEM"].strip()
nuevas = [x.strip() for x in os.environ["FOTOS"].split(",") if x.strip()]
ml = ML(os.environ.get("ML_CODIGO"))
it = ml.get(f"/items/{iid}", attributes="id,title,pictures,variations,category_id")
antes = [x["id"] for x in it.get("pictures") or []]
tope = int(ml.get(f"/categories/{it['category_id']}").get("settings", {}).get("max_pictures_per_item") or 10)
log(f"{iid} | {it.get('title')} | la categoría admite {tope} fotos")
log(f"Fotos actuales ({len(antes)}): " + ", ".join(antes))
log(f"Fotos nuevas   ({len(nuevas)}): " + ", ".join(nuevas))
if len(nuevas) > tope:
    raise SystemExit(f"ERROR: son {len(nuevas)} fotos y la categoría admite {tope}")
if dry:
    log("SIMULACIÓN: no se cambió nada")
else:
    cuerpo = {"pictures": [{"id": x} for x in nuevas]}
    if it.get("variations"):
        cuerpo["variations"] = [{"id": v["id"], "picture_ids": nuevas} for v in it["variations"]]
    r = ml.put(f"/items/{iid}", cuerpo)
    if r.status_code not in (200, 201):
        raise SystemExit(f"ERROR: ML rechazó el cambio: {r.status_code} {r.text[:300]}")
    ahora = [x["id"] for x in ml.get(f"/items/{iid}", attributes="pictures").get("pictures") or []]
    log(f"LISTO. Fotos ahora ({len(ahora)}): " + ", ".join(ahora))
