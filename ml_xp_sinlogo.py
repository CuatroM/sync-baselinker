"""
EXTRAPOWER: quita el logo del proveedor (esquina inferior izquierda) de las fotos de las publicaciones.
Para cada publicación de datos/xp_creadas.json: lee sus fotos actuales en ML, les quita el logo y, si alguna
cambió, sube las nuevas y reemplaza las fotos (mismo orden). Solo toca las fotos.
DESDE/LIMITE para partir el trabajo. DRY_RUN=1: procesa y guarda ejemplos en salida/ sin cambiar nada.
"""
import csv
import json
import os
import time
from collections import Counter

import io

import numpy as np
import requests
from PIL import Image
from scipy import ndimage

from ml.cliente import ML, log
from sin_logo import quitar_logo

dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
desde, limite = int(os.environ.get("DESDE") or 0), int(os.environ.get("LIMITE") or 0)
lista = json.load(open("datos/xp_creadas.json", encoding="utf-8"))
lista = lista[desde:desde + limite] if limite else lista[desde:]
ml = ML(os.environ.get("ML_CODIGO"))
web = requests.Session()
os.makedirs("salida", exist_ok=True)
log(f"{len(lista)} publicaciones | {'SIMULACIÓN' if dry else 'REAL'}")
filas, ejemplos, dudosas = [], 0, 0


def posible_logo(datos):
    """Sin coincidencia con las plantillas: ¿hay una mancha suelta abajo a la izquierda? (para revisar a mano)"""
    a = np.asarray(Image.open(io.BytesIO(datos)).convert("RGB")).astype(np.int16)
    h, w = a.shape[:2]
    fondo = (a.min(axis=2) > 235) & (np.ptp(a, axis=2) < 20)
    lab, n = ndimage.label(ndimage.binary_dilation(~fondo, iterations=3))
    for sl in ndimage.find_objects(lab):
        y0, y1, x0, x1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
        if x0 < 70 and y0 > h * 0.45 and 60 < (x1 - x0) < 320 and (y1 - y0) < 160:
            return (x0, y0, x1, y1)
    return None


def tamano_ml(datos):
    """ML recorta los bordes blancos y pide 500 px como mínimo: si el producto queda chico, lo agranda."""
    im = Image.open(io.BytesIO(datos)).convert("RGB")
    a = np.asarray(im)
    nb = np.argwhere(~((a.min(axis=2) > 235) & (np.ptp(a.astype(np.int16), axis=2) < 20)))
    if not len(nb):
        return datos
    (y0, x0), (y1, x1) = nb.min(0), nb.max(0) + 1
    if max(y1 - y0, x1 - x0) >= 560:
        return datos
    rec = im.crop((x0, y0, x1, y1))
    f = 640 / max(rec.size)
    rec = rec.resize((max(1, round(rec.width * f)), max(1, round(rec.height * f))), Image.LANCZOS)
    lienzo = Image.new("RGB", (800, 800), "white")
    lienzo.paste(rec, ((800 - rec.width) // 2, (800 - rec.height) // 2))
    out = io.BytesIO()
    lienzo.save(out, "JPEG", quality=92)
    return out.getvalue()


def bajar(p):
    """Foto de ML en su tamaño original; si esa versión no es una imagen válida, la publicada."""
    for u in ((p.get("secure_url") or "").replace("-O.", "-F."), p.get("secure_url"), p.get("url"),
              f"https://http2.mlstatic.com/D_{p['id']}-F.jpg", f"https://http2.mlstatic.com/D_{p['id']}-O.jpg"):
        if not u:
            continue
        r = web.get(u, timeout=60)
        if r.status_code == 200:
            try:
                Image.open(io.BytesIO(r.content)).verify()
                return r.content
            except Exception:
                pass
    raise RuntimeError("no pude bajar la foto")
for n, x in enumerate(lista, 1):
    f = {"sku": x["sku"], "publicacion": x["item"], "fotos": 0, "con_logo": 0, "logos": "", "resultado": "", "revisar": "", "fotos_anteriores": ""}
    try:
        it = ml.get(f"/items/{x['item']}", attributes="id,pictures,status,sub_status,sold_quantity")
        pics = it.get("pictures") or []
        f["fotos"] = len(pics)
        f["fotos_anteriores"] = ",".join(p["id"] for p in pics)   # para poder volver atrás
        nuevas, cambio = [], False
        for i, p in enumerate(pics):
            original = bajar(p)
            datos, c = quitar_logo(original)
            if c:
                datos = tamano_ml(datos)
            if not c and dudosas < 80:
                caja = posible_logo(original)
                if caja:
                    f["revisar"] = f"posible logo en {caja}"
                    open(f"salida/revisar_{x['sku']}_{i}.jpg", "wb").write(original)
                    dudosas += 1
            if c:
                f["con_logo"] += 1
                f["logos"] += c + " "
                cambio = True
                if dry and ejemplos < 20:
                    open(f"salida/{x['sku']}_{i}_antes.jpg", "wb").write(original)
                    open(f"salida/{x['sku']}_{i}_despues.jpg", "wb").write(datos)
                    ejemplos += 1
                if not dry:
                    up = ml.pedir("POST", "/pictures/items/upload", files={"file": (f"{x['sku']}_{i}.jpg", datos)})
                    if up.status_code not in (200, 201):
                        raise RuntimeError(f"no pude subir la foto {i}: {up.status_code} {up.text[:150]}")
                    nuevas.append({"id": up.json()["id"]})
                    time.sleep(0.3)
                    continue
            nuevas.append({"id": p["id"]})
        if not cambio:
            f["resultado"] = "sin logo"
        elif dry:
            f["resultado"] = f"SE LIMPIARÍAN {f['con_logo']} fotos"
        else:
            r = ml.put(f"/items/{x['item']}", {"pictures": nuevas})
            if r.status_code in (200, 201):
                f["resultado"] = "FOTOS LIMPIAS"
            elif "not modifiable" in r.text:
                f["resultado"] = f"ML NO PERMITE CAMBIAR LAS FOTOS (estado {it.get('status')})"
            else:
                f["resultado"] = f"ERROR {r.status_code} {r.text[:200]}"
    except Exception as e:
        f["resultado"] = f"ERROR: {e}"[:300]
    if n % 50 == 0 or f["resultado"].startswith("ERROR"):
        log(f"{n}/{len(lista)} {x['sku']}: {f['resultado']}")
    filas.append(f)
with open("salida/xp_sinlogo.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Resumen: " + str(Counter(r["resultado"].split(":")[0].split(" fotos")[0].split(" (")[0] for r in filas)))
log("Logos encontrados: " + str(Counter(l.split(":")[0] for r in filas for l in r["logos"].split())))
log(f"Fotos sin logo reconocido pero con algo abajo a la izquierda (guardadas para revisar): {dudosas}")
