"""
Revisa y arregla la portada de publicaciones puntuales que el usuario indica (ITEMS: números separados por espacio
o coma, con o sin MLA). Para cada una: estado, stock, etiquetas, motivo de moderación y tamaño de la portada.
Prepara la portada corregida según el motivo:
  - "logos y/o textos": saca textos y logos sueltos (sin_texto.limpiar)
  - "incumple tamaño" o foto chica: agranda el producto a 1100 px en 1200x1200 (con super-resolución)
DRY_RUN=1: solo guarda en salida/ la portada actual y la corregida para revisarlas.
DRY_RUN=0: sube la portada corregida (solo la primera foto) y, si tiene stock, pide reactivarla.
"""
import csv
import io
import os
import re
import time

from PIL import Image

from ml.cliente import ML, log, sku_de
import ml_portada_util as pu
from sin_texto import limpiar

dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
ids = ["MLA" + re.sub(r"\D", "", x) for x in re.split(r"[\s,;]+", os.environ.get("ITEMS") or "") if re.sub(r"\D", "", x)]
ml = ML(os.environ.get("ML_CODIGO"))
os.makedirs("salida", exist_ok=True)
log(f"{len(ids)} publicaciones | {'SIMULACIÓN' if dry else 'REAL'}")


def motivo(item_id):
    textos = []
    for ruta, params in ((f"/moderations/infractions/{ml.user_id}", {"related_item_id": item_id}),
                         (f"/items/{item_id}/moderations", {})):
        r = ml.pedir("GET", ruta, params=params)
        if r.status_code != 200:
            continue
        d = r.json()
        lst = d.get("infractions") if isinstance(d, dict) else d
        for i in lst or []:
            if isinstance(i, dict):
                for k in ("reason", "remedy"):
                    if i.get(k):
                        textos.append(re.sub(r"<[^>]+>", " ", str(i[k])))
    return " || ".join(dict.fromkeys(t.strip() for t in textos if t.strip()))[:500]


filas = []
for iid in ids:
    f = {"publicacion": iid, "sku": "", "titulo": "", "estado": "", "stock": "", "tags": "", "motivo": "",
         "portada_px": "", "producto_px": "", "accion": "", "resultado": ""}
    try:
        it = ml.get(f"/items/{iid}")
        pics = it.get("pictures") or []
        f.update(sku=sku_de(it), titulo=it.get("title"), estado=f"{it.get('status')}/{','.join(it.get('sub_status') or [])}",
                 stock=it.get("available_quantity") or 0, tags=",".join(it.get("tags") or []), motivo=motivo(iid))
        if not pics:
            raise RuntimeError("sin fotos")
        original = pu.bajar(pics[0])
        im = Image.open(io.BytesIO(original)).convert("RGB")
        f["portada_px"] = f"{im.width}x{im.height}"
        caja = pu.contenido(im)
        if caja:
            f["producto_px"] = f"{caja[2] - caja[0]}x{caja[3] - caja[1]}"
        m = f["motivo"].lower()
        nueva, acciones = original, []
        if "logo" in m or "texto" in m:
            limpia, k = limpiar(original)
            if k:
                nueva = limpia
                acciones.append(f"saqué {k} textos/logos")
            else:
                acciones.append("no encontré textos sueltos para sacar")
        chica = not caja or max(caja[2] - caja[0], caja[3] - caja[1]) < 720 or min(im.size) < 500
        if "tamaño" in m or "poor_quality_thumbnail" in f["tags"] or chica or nueva is not original:
            im2 = Image.open(io.BytesIO(nueva)).convert("RGB")
            c2 = pu.contenido(im2)
            if c2:
                nueva = pu.agrandar(im2, c2, 1100, 1200)
                acciones.append("agrandé a 1200x1200")
        f["accion"] = "; ".join(acciones) or "nada para corregir"
        open(f"salida/{iid}_antes.jpg", "wb").write(original)
        if nueva is not original:
            open(f"salida/{iid}_despues.jpg", "wb").write(nueva)
        if nueva is original:
            f["resultado"] = "sin cambios"
        elif dry:
            f["resultado"] = "SE CAMBIARÍA LA PORTADA"
        else:
            up = ml.pedir("POST", "/pictures/items/upload", files={"file": (f"{iid}_portada.jpg", nueva)})
            if up.status_code not in (200, 201):
                raise RuntimeError(f"no pude subir: {up.status_code} {up.text[:150]}")
            r = ml.put(f"/items/{iid}", {"pictures": [{"id": up.json()["id"]}] + [{"id": p["id"]} for p in pics[1:]]})
            if r.status_code not in (200, 201):
                raise RuntimeError(f"no pude cambiar la portada: {r.status_code} {r.text[:200]}")
            f["resultado"] = "PORTADA CAMBIADA"
            if f["stock"] and it.get("status") != "active":
                time.sleep(2)
                a = ml.put(f"/items/{iid}", {"status": "active"})
                f["resultado"] += " Y REACTIVADA" if a.status_code in (200, 201) else f" (no se pudo reactivar: {a.text[:150]})"
            elif not f["stock"]:
                f["resultado"] += " (sin stock: sigue pausada)"
    except Exception as e:
        f["resultado"] = f"ERROR: {e}"[:300]
    log(f"{iid} {f['sku']} | {f['estado']} | stock {f['stock']} | portada {f['portada_px']} producto {f['producto_px']}")
    log(f"   motivo: {f['motivo'][:300]}")
    log(f"   {f['accion']} -> {f['resultado']}")
    filas.append(f)

with open("salida/revisar_items.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()) if filas else ["publicacion"], delimiter=";")
    w.writeheader()
    w.writerows(filas)
