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

import requests

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
filas, ejemplos = [], 0
for n, x in enumerate(lista, 1):
    f = {"sku": x["sku"], "publicacion": x["item"], "fotos": 0, "con_logo": 0, "resultado": ""}
    try:
        it = ml.get(f"/items/{x['item']}", attributes="id,pictures,status")
        pics = it.get("pictures") or []
        f["fotos"] = len(pics)
        nuevas, cambio = [], False
        for i, p in enumerate(pics):
            u = p.get("secure_url") or p.get("url")
            u = u.replace("-O.", "-F.") if u else u      # versión original de ML
            r = web.get(u, timeout=60)
            if r.status_code != 200:
                r = web.get(p.get("secure_url") or p.get("url"), timeout=60)
            datos, c = quitar_logo(r.content)
            if c:
                f["con_logo"] += 1
                cambio = True
                if dry and ejemplos < 20:
                    open(f"salida/{x['sku']}_{i}_antes.jpg", "wb").write(r.content)
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
            f["resultado"] = "FOTOS LIMPIAS" if r.status_code in (200, 201) else f"ERROR {r.status_code} {r.text[:200]}"
    except Exception as e:
        f["resultado"] = f"ERROR: {e}"[:300]
    if n % 50 == 0 or f["resultado"].startswith("ERROR"):
        log(f"{n}/{len(lista)} {x['sku']}: {f['resultado']}")
    filas.append(f)
with open("salida/xp_sinlogo.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Resumen: " + str(Counter(r["resultado"].split(":")[0].split(" fotos")[0] for r in filas)))
