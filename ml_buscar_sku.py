"""Solo lectura: busca en las publicaciones propias de ML cada SKU (con y sin sufijo) y dice si ya está publicado."""
import csv
import json
import os

from ml.cliente import ML, log

ml = ML(os.environ.get("ML_CODIGO"))
suf = (os.environ.get("SUFIJO") or "#FEMA").strip()
skus = [s.strip() for s in (os.environ.get("SKUS") or "").replace("\n", ",").split(",") if s.strip()]
filas = []
for s in skus:
    base = s.split("#")[0]
    ids = []
    for q in dict.fromkeys([base + suf, base]):
        r = ml.pedir("GET", f"/users/{ml.user_id}/items/search", params={"seller_sku": q})
        if r.status_code == 200:
            ids += [i for i in r.json().get("results") or [] if i not in ids]
    if not ids:
        filas.append([base, "", "NO PUBLICADO", "", ""])
        log(f"{base}: no publicado")
        continue
    for it in ml.publicaciones(ids, atributos="id,title,status,sub_status,price"):
        filas.append([base, it["id"], it.get("status"), ",".join(it.get("sub_status") or []), it.get("title"), it.get("price")])
        log(f"{base}: {it['id']} {it.get('status')} {it.get('sub_status')} | {it.get('title')} | ${it.get('price')}")
os.makedirs("salida", exist_ok=True)
with open("salida/buscar_sku.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["SKU", "MLA", "ESTADO", "SUB ESTADO", "TITULO", "PRECIO"])
    w.writerows(filas)
log(f"Listo: {sum(1 for f in filas if f[1])} publicaciones encontradas para {len(skus)} SKU")
