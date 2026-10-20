"""
EXTRAPOWER: corrige las fotos de publicaciones que quedaron con "descarga de imagen pendiente".
Las fotos con carpeta en MAYÚSCULAS (/N/A/NA8009.jpg) Mercado Libre no las logra bajar; se reemplazan por
la versión que muestra la web (/cache/1/image/800x800/.../N/A/NA8009.jpg). Solo toca las fotos.
Lista: datos/revisar_estado.json [{item, sku}]  — solo procesa las que siguen con picture_download_pending.
DRY_RUN=1 solo informa.
"""
import csv
import json
import os
from collections import Counter

from ml.cliente import ML, log

CACHE = "/media/catalog/product/cache/1/image/800x800/9df78eab33525d08d6e5fb8d27136e95/"
dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
fichas = {f["sku"]: f for f in json.load(open("datos/xp_fichas.json", encoding="utf-8"))}
lista = json.load(open("datos/revisar_estado.json", encoding="utf-8"))
ml = ML(os.environ.get("ML_CODIGO"))


def arreglar(u):
    base, rel = u.split("/media/catalog/product/", 1)
    return base + CACHE + rel if rel[:1].isupper() else u


filas = []
for x in lista:
    f = {"publicacion": x["item"], "sku": x["sku"], "resultado": ""}
    try:
        it = ml.get(f"/items/{x['item']}")
        if "picture_download_pending" not in (it.get("sub_status") or []):
            f["resultado"] = "fotos ya cargadas: no se toca"
        else:
            urls = [arreglar(u) for u in (fichas.get(x["sku"]) or {}).get("fotos") or []][:10]
            if not urls:
                raise RuntimeError("sin fotos en la ficha")
            if dry:
                f["resultado"] = "SE CAMBIARÍAN: " + " | ".join(urls)[:200]
            else:
                r = ml.put(f"/items/{x['item']}", {"pictures": [{"source": u} for u in urls]})
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
