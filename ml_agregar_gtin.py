"""
Agrega el código de barras (GTIN) a publicaciones ya creadas. Lista: GTIN_LISTA (por defecto
datos/jya_gtin_creadas.json) [{item, sku, gtin}]. Antes de tocar comprueba que el SKU de la publicación
coincida. Solo cambia GTIN (y quita el motivo de "sin código"). DRY_RUN=1 solo informa.
"""
import csv
import json
import os
from collections import Counter

from ml.cliente import ML, log, sku_de

dry = (os.environ.get("DRY_RUN") or "0").strip() != "0"
lista = json.load(open(os.environ.get("GTIN_LISTA") or "datos/jya_gtin_creadas.json", encoding="utf-8"))
ml = ML(os.environ.get("ML_CODIGO"))
log(f"{len(lista)} publicaciones | {'SIMULACIÓN' if dry else 'REAL'}")
filas = []
for x in lista:
    f = {"sku": x["sku"], "publicacion": x["item"], "gtin": x["gtin"], "gtin_anterior": "", "resultado": ""}
    try:
        it = ml.get(f"/items/{x['item']}", attributes="id,status,attributes,seller_custom_field")
        s = (sku_de(it) or "").upper()
        if s.split("#")[0] != x["sku"].upper():
            raise RuntimeError(f"el SKU en ML es {s!r}")
        act = {a["id"]: a.get("value_name") for a in it.get("attributes") or []}
        f["gtin_anterior"] = act.get("GTIN") or ""
        if act.get("GTIN") == x["gtin"]:
            f["resultado"] = "ya lo tenía"
        elif dry:
            f["resultado"] = "SE AGREGARÍA"
        else:
            cuerpo = [{"id": "GTIN", "value_name": x["gtin"]}]
            if "EMPTY_GTIN_REASON" in act:
                cuerpo.append({"id": "EMPTY_GTIN_REASON", "value_id": None, "value_name": None})
            r = ml.put(f"/items/{x['item']}", {"attributes": cuerpo})
            if r.status_code not in (200, 201) and len(cuerpo) > 1:
                r = ml.put(f"/items/{x['item']}", {"attributes": cuerpo[:1]})
            f["resultado"] = "GTIN AGREGADO" if r.status_code in (200, 201) else f"ERROR {r.status_code} {r.text[:250]}"
    except Exception as e:
        f["resultado"] = f"ERROR: {e}"[:300]
    if not f["resultado"].startswith(("GTIN", "SE AGREG", "ya")):
        log(f"{x['sku']} {x['item']}: {f['resultado']}")
    filas.append(f)
os.makedirs("salida", exist_ok=True)
with open("salida/agregar_gtin.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Resumen: " + str(Counter(r["resultado"].split(":")[0].split(" {")[0] for r in filas)))
