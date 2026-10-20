"""
EXTRAPOWER: corrige las fotos de publicaciones que quedaron con "descarga de imagen pendiente".
Las fotos con carpeta en MAYÚSCULAS (/N/A/NA8009.jpg) Mercado Libre no las logra bajar; se reemplazan por
la versión que muestra la web (/cache/1/image/800x800/.../N/A/NA8009.jpg). Solo toca las fotos.
Lista: datos/revisar_estado.json [{item, sku}]  — solo procesa las que siguen con picture_download_pending.
DRY_RUN=1 solo informa.
"""
import csv
import io
import json
import os
import time
from collections import Counter

import requests
from PIL import Image

from ml.cliente import ML, log

CACHE = "/media/catalog/product/cache/1/image/800x800/9df78eab33525d08d6e5fb8d27136e95/"
dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
fichas = {f["sku"]: f for f in json.load(open("datos/xp_fichas.json", encoding="utf-8"))}
lista = json.load(open("datos/revisar_estado.json", encoding="utf-8"))
ml = ML(os.environ.get("ML_CODIGO"))


web = requests.Session()
web.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


def variantes(u):
    """Distintas versiones de la misma foto en la web: 800x800, original y 370x411."""
    base, rel = u.split("/media/catalog/product/", 1)
    if rel.startswith("cache/"):
        rel = rel.split("/", 5)[5] if rel.count("/") >= 5 else rel
        rel = rel.split("9df78eab33525d08d6e5fb8d27136e95/", 1)[-1]
    out = [base + CACHE + rel, f"{base}/media/catalog/product/{rel}",
           base + "/media/catalog/product/cache/1/image/370x411/9df78eab33525d08d6e5fb8d27136e95/" + rel]
    if rel[:1].islower():   # la web a veces guarda la carpeta en mayúsculas
        r2 = rel[:3].upper() + rel[3:]
        out.append(f"{base}/media/catalog/product/{r2}")
    return out


def subir(u, nombre):
    """Baja la foto desde GitHub (la web volvió a responder), la agranda a 800 px si es chica y la sube a ML."""
    for v in variantes(u):
        try:
            r = web.get(v, timeout=30)
        except requests.RequestException:
            continue
        if r.status_code != 200 or len(r.content) < 2000 or "placeholder" in v:
            continue
        try:
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:
            continue
        if max(im.size) < 800:
            esc = 800 / max(im.size)
            im = im.resize((max(1, round(im.width * esc)), max(1, round(im.height * esc))), Image.LANCZOS)
        lienzo = Image.new("RGB", (max(im.width, 600), max(im.height, 600)), "white")
        lienzo.paste(im, ((lienzo.width - im.width) // 2, (lienzo.height - im.height) // 2))
        buf = io.BytesIO()
        lienzo.save(buf, "JPEG", quality=90)
        up = ml.pedir("POST", "/pictures/items/upload", files={"file": (nombre + ".jpg", buf.getvalue())})
        time.sleep(0.3)
        if up.status_code in (200, 201):
            return up.json()["id"]
    return None


def arreglar(u):
    """Versión de la web (800x800). A las que siguen pendientes se les aplica a todas las fotos:
    la web también agranda las fotos chicas, que Mercado Libre rechaza (mínimo 500 px)."""
    base, rel = u.split("/media/catalog/product/", 1)
    return u if rel.startswith("cache/") else base + CACHE + rel


filas = []
for x in lista:
    f = {"publicacion": x["item"], "sku": x["sku"], "resultado": ""}
    try:
        it = ml.get(f"/items/{x['item']}")
        ss = it.get("sub_status") or []
        if "picture_download_pending" not in ss and not (it.get("status") == "under_review" and "waiting_for_patch" in ss):
            f["resultado"] = "fotos ya cargadas: no se toca"
        else:
            urls = [arreglar(u) for u in (fichas.get(x["sku"]) or {}).get("fotos") or []][:10]
            if not urls:
                raise RuntimeError("sin fotos en la ficha")
            if dry:
                f["resultado"] = "SE CAMBIARÍAN: " + " | ".join(urls)[:200]
            else:
                ids = [i for i in (subir(u, f"{x['sku']}_{n}") for n, u in enumerate(urls, 1)) if i]
                if not ids:
                    raise RuntimeError("no se pudo bajar ninguna foto de la web")
                r = ml.put(f"/items/{x['item']}", {"pictures": [{"id": i} for i in ids]})
                f["resultado"] = "FOTOS ACTUALIZADAS" if r.status_code in (200, 201) else f"ERROR {r.status_code} {r.text[:200]}"
    except Exception as e:
        f["resultado"] = f"ERROR: {e}"[:300]
    log(f"{x['sku']} {x['item']}: {f['resultado'][:120]}")
    filas.append(f)
os.makedirs("salida", exist_ok=True)
with open("salida/xp_fotos.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Resumen: " + str(Counter(f["resultado"].split(":")[0] for f in filas)))
