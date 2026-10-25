"""
"Inactiva para revisar - La portada incumple tamaño": ML pide que el producto de la foto principal mida
al menos 500 px de lado (después de recortarle los bordes blancos).
Revisa la foto principal de todas las publicaciones no activas (pausadas / en revisión). Si el producto
mide menos de 520 px, lo agranda y lo centra en una foto de 800x800, la sube y la pone como portada
(las demás fotos quedan igual). Solo toca la portada. DRY_RUN=1: solo informa.

MODO=moderacion: solo las que ML marcó "La portada incumple tamaño" (motivo de moderación). A esas se les
regenera la portada SIEMPRE, más grande (producto de 1100 px en una foto de 1200x1200), ampliando la imagen
con super-resolución (red neuronal FSRCNN/EDSR de OpenCV) para que no quede borrosa. Después, si tiene stock,
pide reactivarla. Con DRY_RUN=1 guarda ejemplos antes/después en salida/.
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
modo = (os.environ.get("MODO") or "").strip().lower()
ml = ML(os.environ.get("ML_CODIGO"))
web = requests.Session()
MIN = 520
os.makedirs("salida", exist_ok=True)


def contenido(im):
    a = np.asarray(im.convert("RGB")).astype(np.int16)
    nb = np.argwhere(~((a.min(axis=2) > 235) & (np.ptp(a, axis=2) < 20)))
    if not len(nb):
        return None
    (y0, x0), (y1, x1) = nb.min(0), nb.max(0) + 1
    return x0, y0, x1, y1


SR = {}


def superres(rec):
    """Amplía con una red neuronal (OpenCV dnn_superres). Si no está disponible, devuelve la misma imagen."""
    if SR.get("falla"):
        return rec
    try:
        import cv2
        lado = max(rec.size)
        esc = 4 if lado < 400 else 2 if lado < 900 else 0
        if not esc:
            return rec
        if esc not in SR:
            modelo = f"/tmp/EDSR_x{esc}.pb" if lado < 250 else f"/tmp/FSRCNN_x{esc}.pb"
            if not os.path.exists(modelo):
                url = ("https://raw.githubusercontent.com/Saafke/EDSR_Tensorflow/master/models/" if "EDSR" in modelo
                       else "https://raw.githubusercontent.com/Saafke/FSRCNN_Tensorflow/master/models/") + os.path.basename(modelo)
                open(modelo, "wb").write(web.get(url, timeout=120).content)
            sr = cv2.dnn_superres.DnnSuperResImpl_create()
            sr.readModel(modelo)
            sr.setModel("edsr" if "EDSR" in modelo else "fsrcnn", esc)
            SR[esc] = sr
        a = cv2.cvtColor(np.asarray(rec), cv2.COLOR_RGB2BGR)
        b = SR[esc].upsample(a)
        return Image.fromarray(cv2.cvtColor(b, cv2.COLOR_BGR2RGB))
    except Exception as e:
        log(f"super-resolución no disponible ({e}); uso ampliación común")
        SR["falla"] = True
        return rec


def agrandar(im, caja, lado=640, lienzo_px=800):
    rec = im.convert("RGB").crop(caja)
    if max(rec.size) < lado:
        rec = superres(rec)
    f = lado / max(rec.size)
    rec = rec.resize((max(1, round(rec.width * f)), max(1, round(rec.height * f))), Image.LANCZOS)
    lienzo = Image.new("RGB", (lienzo_px, lienzo_px), "white")
    lienzo.paste(rec, ((lienzo_px - rec.width) // 2, (lienzo_px - rec.height) // 2))
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
items = list(ml.publicaciones(ids, atributos="id,title,status,sub_status,tags,pictures,seller_custom_field,attributes,available_quantity"))


def motivo(item_id):
    r = ml.pedir("GET", f"/moderations/infractions/{ml.user_id}", params={"related_item_id": item_id})
    if r.status_code != 200:
        return ""
    d = r.json()
    lst = d.get("infractions") if isinstance(d, dict) else d
    return " || ".join(str(i.get("reason")) for i in lst or [] if isinstance(i, dict) and i.get("reason"))


if modo == "moderacion":
    items = [it for it in items if "incumple tamaño" in motivo(it["id"]).lower()]
    log(f"Marcadas por ML 'La portada incumple tamaño': {len(items)}")
ejemplos = 0
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
        mala = "poor_quality_thumbnail" in (it.get("tags") or [])
        forzar = modo == "moderacion"
        nueva = agrandar(im, caja, 1100, 1200) if forzar else None
        if forzar and dry and ejemplos < 30:
            im.convert("RGB").save(f"salida/{it['id']}_antes.jpg", quality=90)
            open(f"salida/{it['id']}_despues.jpg", "wb").write(nueva)
            ejemplos += 1
        if not forzar and lado >= MIN and not (mala and lado < 720):
            f["resultado"] = "ok"
        elif dry:
            f["resultado"] = "SE AGRANDARÍA"
        else:
            up = ml.pedir("POST", "/pictures/items/upload", files={"file": (f"{it['id']}_portada.jpg", nueva or agrandar(im, caja))})
            if up.status_code not in (200, 201):
                raise RuntimeError(f"no pude subir: {up.status_code} {up.text[:150]}")
            nuevas = [{"id": up.json()["id"]}] + [{"id": p["id"]} for p in pics[1:]]
            r = ml.put(f"/items/{it['id']}", {"pictures": nuevas})
            if r.status_code in (200, 201):
                f["resultado"] = "PORTADA AGRANDADA"
                if forzar and (it.get("available_quantity") or 0) > 0 and it.get("status") == "paused":
                    time.sleep(2)
                    a = ml.put(f"/items/{it['id']}", {"status": "active"})
                    f["resultado"] += " Y REACTIVADA" if a.status_code in (200, 201) else f" (no se pudo reactivar: {a.status_code})"
                elif forzar:
                    f["resultado"] += " (sin stock: sigue pausada)"
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

with open("salida/portada.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Resumen: " + str(Counter(r["resultado"].split(":")[0].split(" (")[0] for r in filas)))
chicas = [r for r in filas if r["resultado"] != "ok"]
log("Proveedor de las corregidas/pendientes: " + str(Counter((r["sku"] or "").split("#")[-1] if "#" in (r["sku"] or "") else "otro" for r in chicas)))
