"""
Agrega una foto (archivo del repo, ej. datos/fotos_extra/X.jpg) a UNA publicación de Mercado Libre, sin tocar las demás.
ITEM = MLA..., ARCHIVO = ruta del jpg, POSICION = "final" (por defecto) o un número (2 = después de la portada).
No reemplaza la portada. Si la categoría ya está en el máximo de fotos, no hace nada.
DRY_RUN=1 (por defecto): solo muestra cómo quedaría.
"""
import os

from ml.cliente import ML, log, sku_de

dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
iid = "MLA" + "".join(c for c in os.environ["ITEM"] if c.isdigit())
archivo = os.environ["ARCHIVO"].strip()
pos = (os.environ.get("POSICION") or "final").strip().lower()
ml = ML(os.environ.get("ML_CODIGO"))
it = ml.get(f"/items/{iid}", attributes="id,title,pictures,variations,category_id,attributes,seller_custom_field")
fotos = [p["id"] for p in it.get("pictures") or []]
tope = int(ml.get(f"/categories/{it['category_id']}").get("settings", {}).get("max_pictures_per_item") or 10)
log(f"{iid} | {sku_de(it)} | {it.get('title')} | fotos {len(fotos)} de {tope}")
if len(fotos) >= tope:
    raise SystemExit(f"La publicación ya tiene {len(fotos)} fotos (máximo {tope}): no se agrega.")
i = len(fotos) if pos == "final" else max(1, min(int(pos) - 1, len(fotos)))
if dry:
    log(f"SIMULACIÓN: se agregaría {archivo} en la posición {i + 1}")
    raise SystemExit(0)
up = ml.pedir("POST", "/pictures/items/upload", files={"file": (os.path.basename(archivo), open(archivo, "rb").read(), "image/jpeg")})
if up.status_code not in (200, 201):
    raise SystemExit(f"ERROR al subir la foto: {up.status_code} {up.text[:200]}")
nueva = up.json()["id"]
todas = fotos[:i] + [nueva] + fotos[i:]
cuerpo = {"pictures": [{"id": x} for x in todas]}
if it.get("variations"):
    cuerpo["variations"] = [{"id": v["id"], "picture_ids": (v.get("picture_ids") or [])[:i] + [nueva] + (v.get("picture_ids") or [])[i:]}
                            for v in it["variations"]]
r = ml.put(f"/items/{iid}", cuerpo)
if r.status_code not in (200, 201):
    raise SystemExit(f"ERROR: ML rechazó el cambio: {r.status_code} {r.text[:250]}")
ahora = [p["id"] for p in ml.get(f"/items/{iid}", attributes="pictures").get("pictures") or []]
log(f"LISTO: foto agregada en la posición {ahora.index(nueva) + 1 if nueva in ahora else '?'} | fotos ahora: {len(ahora)}")
