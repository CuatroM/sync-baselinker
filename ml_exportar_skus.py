"""Exporta (solo lectura) el SKU de todas las publicaciones de la cuenta, por estado."""
import csv
import os

from ml.cliente import ML, log, sku_de

ml = ML(os.environ.get("ML_CODIGO"))
filas = []
for estado in ("active", "paused", "under_review", "inactive", "closed"):
    ids = ml.ids_publicaciones(status=estado)
    log(f"{estado}: {len(ids)} publicaciones")
    for it in ml.publicaciones(ids, atributos="id,title,status,seller_custom_field,attributes,date_created"):
        filas.append([it["id"], it.get("status"), sku_de(it), it.get("title"), it.get("date_created")])
os.makedirs("salida", exist_ok=True)
with open("salida/ml-skus.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["item_id", "estado", "sku", "titulo", "creada"])
    w.writerows(filas)
log(f"Total exportado: {len(filas)}")
