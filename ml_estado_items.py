"""Solo lectura: estado actual en ML de las publicaciones de datos/revisar_estado.json."""
import csv
import json
import os
from collections import Counter

from ml.cliente import ML, log, sku_de

lista = json.load(open("datos/revisar_estado.json", encoding="utf-8"))
ml = ML(os.environ.get("ML_CODIGO"))
filas = []
ids = [x["item"] for x in lista]
info = {}
for i in range(0, len(ids), 20):
    for x in ml.get("/items", ids=",".join(ids[i:i + 20]),
                    attributes="id,status,sub_status,tags,title,category_id,attributes,seller_custom_field,last_updated"):
        b = x.get("body") or {}
        info[b.get("id")] = (x.get("code"), b)
for x in lista:
    code, b = info.get(x["item"], (None, {}))
    filas.append({"publicacion": x["item"], "sku": x["sku"], "titulo": x["titulo"], "http": code,
                  "estado": b.get("status"), "sub_estado": ",".join(b.get("sub_status") or []),
                  "sku_ml": sku_de(b) if b else "", "tags": ",".join(t for t in (b.get("tags") or []) if "poor" in t or "review" in t or "moderat" in t),
                  "ultima_modificacion": b.get("last_updated")})
os.makedirs("salida", exist_ok=True)
with open("salida/estado_items.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Estados: " + str(Counter(f"{x['estado']} / {x['sub_estado']}" for x in filas).most_common()))
log("SKU en ML distinto: " + str(sum(1 for x in filas if x["sku_ml"] and not x["sku_ml"].upper().startswith(x["sku"].upper()))))
