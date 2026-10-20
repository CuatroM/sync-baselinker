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
                    attributes="id,status,sub_status,tags,title,category_id,attributes,seller_custom_field,last_updated,pictures"):
        b = x.get("body") or {}
        info[b.get("id")] = (x.get("code"), b)
for x in lista:
    code, b = info.get(x["item"], (None, {}))
    filas.append({"publicacion": x["item"], "sku": x["sku"], "titulo": x["titulo"], "http": code,
                  "estado": b.get("status"), "sub_estado": ",".join(b.get("sub_status") or []),
                  "sku_ml": sku_de(b) if b else "", "tags": ",".join(t for t in (b.get("tags") or []) if "poor" in t or "review" in t or "moderat" in t),
                  "ultima_modificacion": b.get("last_updated"), "fotos": len(b.get("pictures") or []),
                  "fotos_ok": sum(1 for p in b.get("pictures") or [] if (p.get("max_size") or p.get("size")) and "-F" not in (p.get("id") or "")) })
# motivo de la moderación (API de infracciones de ML)
for x in filas:
    if x["estado"] != "under_review":
        continue
    motivos = []
    for ruta, params in ((f"/moderations/infractions/{ml.user_id}", {"related_item_id": x["publicacion"]}),
                         (f"/items/{x['publicacion']}/moderations", {})):
        r = ml.pedir("GET", ruta, params=params)
        if r.status_code == 200:
            d = r.json()
            lst = d.get("infractions") if isinstance(d, dict) else d
            for inf in (lst or []):
                if isinstance(inf, dict):
                    motivos.append(" | ".join(str(inf.get(k)) for k in ("reason", "remedy", "element_type", "element_id",
                                                                         "filter_subgroup", "note") if inf.get(k)))
            if motivos:
                break
        elif not motivos:
            motivos.append(f"{ruta.split('/')[1]} {r.status_code}: {r.text[:150]}")
    x["motivo"] = " || ".join(motivos)[:600]
os.makedirs("salida", exist_ok=True)
with open("salida/estado_items.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Estados: " + str(Counter(f"{x['estado']} / {x['sub_estado']}" for x in filas).most_common()))
log("Motivos: " + str(Counter((x.get("motivo") or "")[:120] for x in filas).most_common(15)))
log("SKU en ML distinto: " + str(sum(1 for x in filas if x["sku_ml"] and not x["sku_ml"].upper().startswith(x["sku"].upper()))))
