"""
Solo lectura: métricas de todas las publicaciones de la cuenta (activas, pausadas y en revisión).
Visitas (total, 30 y 7 días), ventas, preguntas, precio, stock, estado, categoría, marca, foto.
Salida: salida/metricas.json (para el panel) y salida/metricas.csv
"""
import csv
import json
import os
from collections import Counter
from datetime import date, timedelta

from ml.cliente import ML, log, sku_de

ml = ML(os.environ.get("ML_CODIGO"))
hoy = date.today()
con_caja = set(json.load(open("datos/con_caja.json"))) if os.path.exists("datos/con_caja.json") else set()

ids = []
for est in ("active", "paused", "under_review"):
    try:
        lote = ml.ids_publicaciones(status=est)
    except Exception as e:
        log(f"{est}: no se pudo listar ({e})")
        lote = []
    log(f"{est}: {len(lote)}")
    ids += lote
ids = list(dict.fromkeys(ids))

_cat = {}


def cat_nombre(c):
    if c not in _cat:
        r = ml.pedir("GET", f"/categories/{c}")
        _cat[c] = r.json().get("name", "") if r.status_code == 200 else ""
    return _cat[c]


items = {}
for it in ml.publicaciones(ids, atributos="id,title,status,sub_status,price,available_quantity,sold_quantity,"
                                         "category_id,permalink,thumbnail,pictures,date_created,attributes,"
                                         "seller_custom_field,listing_type_id"):
    marca = next((a.get("value_name") for a in it.get("attributes") or [] if a.get("id") == "BRAND"), "") or ""
    items[it["id"]] = {
        "id": it["id"], "sku": sku_de(it).replace("#FEMA", ""), "fema": sku_de(it).upper().endswith("#FEMA"),
        "titulo": it.get("title"), "estado": it.get("status"), "sub": ",".join(it.get("sub_status") or []),
        "precio": it.get("price"), "stock": it.get("available_quantity"), "vendidos": it.get("sold_quantity") or 0,
        "cat": cat_nombre(it.get("category_id")), "marca": marca, "link": it.get("permalink"),
        "thumb": (it.get("thumbnail") or "").replace("http://", "https://"), "fotos": len(it.get("pictures") or []),
        "creada": (it.get("date_created") or "")[:10], "caja": it["id"] in con_caja,
        "v_total": 0, "v_30": 0, "v_7": 0, "preguntas": 0,
    }
log(f"{len(items)} publicaciones con detalle")

lista = list(items)
for i in range(0, len(lista), 50):
    grupo = lista[i:i + 50]
    r = ml.pedir("GET", "/visits/items", params={"ids": ",".join(grupo)})
    if r.status_code == 200:
        for k, v in (r.json() or {}).items():
            if k in items:
                items[k]["v_total"] = v or 0
    for dias, campo in ((30, "v_30"), (7, "v_7")):
        r = ml.pedir("GET", "/items/visits", params={"ids": ",".join(grupo), "date_from": str(hoy - timedelta(days=dias)),
                                                     "date_to": str(hoy)})
        if r.status_code == 200:
            for x in r.json() or []:
                if x.get("item_id") in items:
                    items[x["item_id"]][campo] = x.get("total_visits") or 0
    if i % 1000 == 0:
        log(f"  visitas: {i}/{len(lista)}")

# preguntas recibidas (todas las del vendedor, contadas por publicación)
cnt, offset = Counter(), 0
while offset < 20000:
    r = ml.pedir("GET", "/questions/search", params={"seller_id": ml.user_id, "api_version": 4, "limit": 50,
                                                      "offset": offset, "sort_fields": "date_created", "sort_types": "DESC"})
    if r.status_code != 200:
        log(f"preguntas: {r.status_code} {r.text[:150]}")
        break
    qs = r.json().get("questions") or []
    if not qs:
        break
    for q in qs:
        cnt[q.get("item_id")] += 1
    offset += 50
for k, n in cnt.items():
    if k in items:
        items[k]["preguntas"] = n
log(f"preguntas contadas: {sum(cnt.values())}")

os.makedirs("salida", exist_ok=True)
datos = sorted(items.values(), key=lambda x: -x["v_total"])
json.dump({"generado": str(hoy), "items": datos}, open("salida/metricas.json", "w", encoding="utf-8"), ensure_ascii=False)
with open("salida/metricas.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(datos[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(datos)
log(f"Visitas totales: {sum(x['v_total'] for x in datos)} | 30 días: {sum(x['v_30'] for x in datos)} | "
    f"vendidos: {sum(x['vendidos'] for x in datos)}")
