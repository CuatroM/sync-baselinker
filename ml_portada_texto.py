"""
Saca los textos y logos escritos de la PORTADA (primera foto) de las publicaciones de un proveedor:
marca arriba, medidas, "Incluye packaging", etiquetas con flechas (ver sin_texto.py).
Solo cambia la primera foto; las demás quedan igual. Guarda el id de la portada anterior para volver atrás.
LISTA: datos/jya_creadas.json o datos/xp_creadas.json [{item, sku}]. DESDE/LIMITE para partir.
Con "original" en la lista (datos/portadas_rehacer.json) parte de esa foto y reemplaza la portada actual.
DRY_RUN=1: procesa y guarda ejemplos antes/después en salida/ sin cambiar nada.
"""
import csv
import io
import os
import time
from collections import Counter

import requests
from PIL import Image

import json
from ml.cliente import ML, log
from sin_texto import IDIOMA, limpiar

dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
desde, limite = int(os.environ.get("DESDE") or 0), int(os.environ.get("LIMITE") or 0)
lista = json.load(open(os.environ.get("LISTA") or "datos/jya_creadas.json", encoding="utf-8"))
lista = lista[desde:desde + limite] if limite else lista[desde:]
ml = ML(os.environ.get("ML_CODIGO"))
web = requests.Session()
os.makedirs("salida", exist_ok=True)
log(f"{len(lista)} publicaciones | {'SIMULACIÓN' if dry else 'REAL'} | OCR: {IDIOMA}")


def bajar(p):
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
    raise RuntimeError("no pude bajar la portada")


filas, ejemplos = [], 0
for n, x in enumerate(lista, 1):
    f = {"sku": x["sku"], "publicacion": x["item"], "borrados": 0, "portada_anterior": "", "resultado": ""}
    try:
        it = ml.get(f"/items/{x['item']}", attributes="id,pictures,status")
        pics = it.get("pictures") or []
        if not pics:
            raise RuntimeError("sin fotos")
        rehacer = x.get("original")          # rehacer desde la portada original (antes de la primera limpieza)
        f["portada_anterior"] = rehacer or pics[0]["id"]
        original = bajar({"id": rehacer} if rehacer else pics[0])
        nueva, k = limpiar(original)
        f["borrados"] = k
        if not k and rehacer:
            if dry:
                f["resultado"] = "SE RESTAURARÍA LA ORIGINAL"
            else:
                r = ml.put(f"/items/{x['item']}", {"pictures": [{"id": rehacer}] + [{"id": p["id"]} for p in pics[1:]]})
                f["resultado"] = "ORIGINAL RESTAURADA" if r.status_code in (200, 201) else f"ERROR {r.status_code} {r.text[:200]}"
        elif not k:
            f["resultado"] = "sin textos"
        else:
            if dry and ejemplos < 40:
                open(f"salida/{x['sku'].replace('/', '_')}_antes.jpg", "wb").write(original)
                open(f"salida/{x['sku'].replace('/', '_')}_despues.jpg", "wb").write(nueva)
                ejemplos += 1
            if dry:
                f["resultado"] = "SE LIMPIARÍA"
            else:
                up = ml.pedir("POST", "/pictures/items/upload", files={"file": (f"{x['item']}_portada.jpg", nueva)})
                if up.status_code not in (200, 201) and "500 p" in up.text and rehacer:
                    # sin el texto el producto queda muy chico para ML: se vuelve a la original
                    r = ml.put(f"/items/{x['item']}", {"pictures": [{"id": rehacer}] + [{"id": p["id"]} for p in pics[1:]]})
                    f["resultado"] = "ORIGINAL RESTAURADA" if r.status_code in (200, 201) else f"ERROR {r.status_code} {r.text[:200]}"
                    filas.append(f)
                    continue
                if up.status_code not in (200, 201):
                    raise RuntimeError(f"no pude subir: {up.status_code} {up.text[:150]}")
                r = ml.put(f"/items/{x['item']}", {"pictures": [{"id": up.json()["id"]}] + [{"id": p["id"]} for p in pics[1:]]})
                if r.status_code in (200, 201):
                    f["resultado"] = "PORTADA LIMPIA"
                elif "not modifiable" in r.text:
                    f["resultado"] = f"ML NO PERMITE CAMBIAR LAS FOTOS ({it.get('status')})"
                else:
                    f["resultado"] = f"ERROR {r.status_code} {r.text[:200]}"
                time.sleep(0.3)
    except Exception as e:
        f["resultado"] = f"ERROR: {e}"[:300]
    if n % 50 == 0 or f["resultado"].startswith(("ERROR", "ML NO")):
        log(f"{n}/{len(lista)} {x['sku']}: {f['resultado']}")
    filas.append(f)
with open("salida/portada_texto.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Resumen: " + str(Counter(r["resultado"].split(":")[0].split(" (")[0] for r in filas)))
