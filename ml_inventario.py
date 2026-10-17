"""
Solo lectura: inventario completo de la cuenta en Mercado Libre, en TODOS los estados que la API informe
(activas, pausadas, en revisión, inactivas, cerradas), incluidas las publicaciones anteriores a este trabajo.
Para las que están en revisión trae el motivo de la moderación.
Salida: salida/inventario.csv y un resumen en el log.
"""
import csv
import os
from collections import Counter

from ml.cliente import ML, log, sku_de

ml = ML(os.environ.get("ML_CODIGO"))
base = f"/users/{ml.user_id}/items/search"

# 1) qué estados informa ML para esta cuenta (con cantidades)
r = ml.pedir("GET", base, params={"include_filters": "true", "limit": 1})
estados = {}
if r.status_code == 200:
    d = r.json()
    log(f"Total informado sin filtro: {(d.get('paging') or {}).get('total')}")
    for f in (d.get("available_filters") or []) + (d.get("filters") or []):
        log(f"Filtro disponible: {f.get('id')} -> " + ", ".join(f"{v.get('id')}({v.get('results')})" for v in f.get("values") or []))
        if f.get("id") == "status":
            for v in f.get("values") or []:
                estados[v["id"]] = v.get("results")
else:
    log(f"include_filters: {r.status_code} {r.text[:200]}")
for e in ("active", "paused", "under_review", "inactive", "closed", "not_yet_active", "payment_required"):
    estados.setdefault(e, None)

# 2) IDs por estado
ids = {}
for e, n in estados.items():
    try:
        lote = ml.ids_publicaciones(status=e)
    except Exception as ex:
        log(f"{e}: no se pudo listar ({ex})")
        continue
    log(f"Estado {e}: {len(lote)} (ML informa {n})")
    for i in lote:
        ids.setdefault(i, e)

# 3) detalle
filas = []
lista = list(ids)
for it in ml.publicaciones(lista, atributos="id,title,status,sub_status,category_id,price,available_quantity,"
                                           "sold_quantity,date_created,last_updated,attributes,seller_custom_field,"
                                           "permalink"):
    sku = sku_de(it)
    filas.append({"publicacion": it["id"], "sku": sku, "fema": "si" if sku.upper().endswith("#FEMA") else "no",
                  "titulo": it.get("title"), "estado": it.get("status"), "sub_estado": ",".join(it.get("sub_status") or []),
                  "categoria": it.get("category_id"), "precio": it.get("price"), "stock": it.get("available_quantity"),
                  "vendidos": it.get("sold_quantity"), "creada": (it.get("date_created") or "")[:10],
                  "modificada": (it.get("last_updated") or "")[:10], "motivo": "", "link": it.get("permalink")})
log(f"Detalle de {len(filas)} publicaciones")

# 4) motivo de moderación de las que están en revisión
for x in filas:
    if x["estado"] != "under_review":
        continue
    r = ml.pedir("GET", f"/moderations/infractions/{ml.user_id}", params={"related_item_id": x["publicacion"]})
    if r.status_code == 200:
        lst = r.json().get("infractions") if isinstance(r.json(), dict) else r.json()
        x["motivo"] = " || ".join(str(i.get("reason")) for i in (lst or []) if isinstance(i, dict) and i.get("reason"))[:300]

os.makedirs("salida", exist_ok=True)
with open("salida/inventario.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Por estado: " + str(Counter(f"{x['estado']} / {x['sub_estado']}" for x in filas).most_common()))
log("En revisión, #FEMA vs otras: " + str(Counter(x["fema"] for x in filas if x["estado"] == "under_review")))
log("Motivos: " + str(Counter(x["motivo"][:90] for x in filas if x["estado"] == "under_review").most_common(10)))
