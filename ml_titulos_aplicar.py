"""
Cambia el título de las publicaciones según LISTA (por defecto datos/titulos_aprobados.json):
[{item, sku, titulo, titulo_anterior}]. Antes de cambiar comprueba que el SKU coincida y que el título
actual sea el anterior esperado (si alguien lo cambió a mano, no se pisa). Guarda el título anterior.
DRY_RUN=1 solo informa.
"""
import csv
import json
import os
import time
from collections import Counter

from ml.cliente import ML, log, sku_de

dry = (os.environ.get("DRY_RUN") or "0").strip() != "0"
lista = json.load(open(os.environ.get("LISTA") or "datos/titulos_aprobados.json", encoding="utf-8"))
ml = ML(os.environ.get("ML_CODIGO"))
log(f"{len(lista)} títulos | {'SIMULACIÓN' if dry else 'REAL'}")
filas = []
for n, x in enumerate(lista, 1):
    f = {"sku": x["sku"], "publicacion": x["item"], "titulo_anterior": "", "titulo_nuevo": x["titulo"], "resultado": ""}
    try:
        it = ml.get(f"/items/{x['item']}", attributes="id,title,status,sold_quantity,attributes,seller_custom_field")
        f["titulo_anterior"] = it.get("title")
        if (sku_de(it) or "").upper() != x["sku"].upper():
            raise RuntimeError(f"el SKU en ML es {sku_de(it)!r}")
        if it.get("title") == x["titulo"]:
            f["resultado"] = "ya tenía ese título"
        elif x.get("titulo_anterior") and it.get("title") != x["titulo_anterior"]:
            f["resultado"] = "NO SE TOCA: el título cambió desde la exportación"
        elif dry:
            f["resultado"] = "SE CAMBIARÍA"
        else:
            r = ml.put(f"/items/{x['item']}", {"title": x["titulo"]})
            if r.status_code in (200, 201):
                f["resultado"] = "TÍTULO CAMBIADO"
            else:
                f["resultado"] = f"ERROR {r.status_code} {r.text[:250]}"
            time.sleep(0.2)
    except Exception as e:
        f["resultado"] = f"ERROR: {e}"[:300]
    if n % 100 == 0 or f["resultado"].startswith(("ERROR", "NO SE")):
        log(f"{n}/{len(lista)} {x['sku']}: {f['resultado']}")
    filas.append(f)
os.makedirs("salida", exist_ok=True)
with open("salida/titulos_aplicados.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Resumen: " + str(Counter(r["resultado"].split(":")[0].split(" {")[0] for r in filas)))
