"""
"Inactiva para revisar - La portada incumple tamaño": ML pide que el producto de la foto principal mida
al menos 500 px de lado (después de recortarle los bordes blancos).
Revisa la foto principal de todas las publicaciones no activas (pausadas / en revisión). Si el producto
mide menos de 520 px, lo agranda y lo centra en una foto de 800x800, la sube y la pone como portada
(las demás fotos quedan igual). Solo toca la portada. DRY_RUN=1: solo informa.
"""
import csv
import io
import os
import time
from collections import Counter

import numpy as np
import requests
from PIL import Image

from ml.cliente import ML, log, sku_de

dry = (os.environ.get("DRY_RUN") or "0").strip() != "0"
ml = ML(os.environ.get("ML_CODIGO"))
web = requests.Session()
MIN = 520


def contenido(im):
    a = np.asarray(im.convert("RGB")).astype(np.int16)
    nb = np.argwhere(~((a.min(axis=2) > 235) & (np.ptp(a, axis=2) < 20)))
    if not len(nb):
        return None
    (y0, x0), (y1, x1) = nb.min(0), nb.max(0) + 1
    return x0, y0, x1, y1


def agrandar(im, caja):
    rec = im.convert("RGB").crop(caja)
    f = 640 / max(rec.size)
    rec = rec.resize((max(1, round(rec.width * f)), max(1, round(rec.height * f))), Image.LANCZOS)
    lienzo = Image.new("RGB", (800, 800), "white")
    lienzo.paste(rec, ((800 - rec.width) // 2, (800 - rec.height) // 2))
    out = io.BytesIO()
    lienzo.save(out, "JPEG", quality=92)
    return out.getvalue()


def bajar(p):
    for u in ((p.get("secure_url") or "").replace("-O.", "-F."), p.get("secure_url"), p.get("url"),
              f"https://http2.mlstatic.com/D_{p['id']}-F.jpg", f"https://http2.mlstatic.com/D_{p['id']}-O.jpg"):
        if not u:
            continue
        r = web.get(u, timeout=60)
        if r.status_code == 200:
            try:
                im = Image.open(io.BytesIO(r.content))
                im.load()
                return im
            except Exception:
                pass
    raise RuntimeError("no pude bajar la portada")


ids = []
for e in ("paused", "under_review", "inactive"):
    try:
        lote = ml.ids_publicaciones(status=e)
    except Exception as ex:
        log(f"{e}: {ex}")
        lote = []
    log(f"{e}: {len(lote)}")
    ids += lote
ids = list(dict.fromkeys(ids))
items = list(ml.publicaciones(ids, atributos="id,title,status,sub_status,tags,pictures,seller_custom_field,attributes"))
log(f"{len(items)} publicaciones no activas | {'SIMULACIÓN' if dry else 'REAL'}")
log("sub_status: " + str(Counter(s for it in items for s in (it.get("sub_status") or ["-"])).most_common(15)))
log("tags: " + str(Counter(t for it in items for t in (it.get("tags") or [])).most_common(25)))

filas = []
for n, it in enumerate(items, 1):
    pics = it.get("pictures") or []
    f = {"sku": sku_de(it), "publicacion": it["id"], "titulo": it.get("title"), "estado": it.get("status"),
         "sub_status": ",".join(it.get("sub_status") or []), "tags": ",".join(it.get("tags") or []),
         "portada_ml": (pics[0].get("max_size") or pics[0].get("size")) if pics else "", "producto_px": "",
         "portada_anterior": pics[0]["id"] if pics else "", "resultado": ""}
    try:
        if not pics:
            raise RuntimeError("sin fotos")
        im = bajar(pics[0])
        caja = contenido(im)
        if not caja:
            raise RuntimeError("portada en blanco")
        lado = max(caja[2] - caja[0], caja[3] - caja[1])
        f["producto_px"] = f"{caja[2] - caja[0]}x{caja[3] - caja[1]}"
        if lado >= MIN:
            f["resultado"] = "ok"
        elif dry:
            f["resultado"] = "SE AGRANDARÍA"
        else:
            up = ml.pedir("POST", "/pictures/items/upload", files={"file": (f"{it['id']}_portada.jpg", agrandar(im, caja))})
            if up.status_code not in (200, 201):
                raise RuntimeError(f"no pude subir: {up.status_code} {up.text[:150]}")
            nuevas = [{"id": up.json()["id"]}] + [{"id": p["id"]} for p in pics[1:]]
            r = ml.put(f"/items/{it['id']}", {"pictures": nuevas})
            if r.status_code in (200, 201):
                f["resultado"] = "PORTADA AGRANDADA"
            elif "not modifiable" in r.text:
                f["resultado"] = f"ML NO PERMITE CAMBIAR LAS FOTOS ({it.get('status')})"
            else:
                f["resultado"] = f"ERROR {r.status_code} {r.text[:200]}"
            time.sleep(0.3)
    except Exception as e:
        f["resultado"] = f"ERROR: {e}"[:300]
    if n % 200 == 0 or f["resultado"] not in ("ok",) and not f["resultado"].startswith(("PORTADA", "SE AGRAND")):
        log(f"{n}/{len(items)} {f['sku']} {it['id']}: {f['resultado']}")
    filas.append(f)

os.makedirs("salida", exist_ok=True)
with open("salida/portada.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Resumen: " + str(Counter(r["resultado"].split(":")[0].split(" (")[0] for r in filas)))
chicas = [r for r in filas if r["resultado"] != "ok"]
log("Proveedor de las corregidas/pendientes: " + str(Counter((r["sku"] or "").split("#")[-1] if "#" in (r["sku"] or "") else "otro" for r in chicas)))
