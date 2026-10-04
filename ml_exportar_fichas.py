"""
Exporta de Mercado Libre (solo lectura) las publicaciones de los SKU de datos/fichas_fema.json:
categoría, título, atributos actuales, descripción actual, ventas, y los atributos que admite
cada categoría (con tipo, unidades, valores permitidos y si son obligatorios).
Salida: salida/ml_fichas_export.json
"""
import json
import os

from ml.cliente import ML, log, sku_de

SUFIJO = "#FEMA"
fichas = json.load(open("datos/fichas_fema.json", encoding="utf-8"))
ml = ML(os.environ.get("ML_CODIGO"))

items, no_encontrados, categorias = {}, [], {}
for f in fichas:
    sku = f["sku"] + SUFIJO
    ids = []
    for estado in ("active", "paused"):
        d = ml.get(f"/users/{ml.user_id}/items/search", seller_sku=sku, status=estado)
        ids += d.get("results") or []
    if not ids:
        no_encontrados.append(f["sku"])
        continue
    for iid in ids:
        it = ml.get(f"/items/{iid}")
        if sku_de(it).upper() != sku.upper():
            continue
        r = ml.pedir("GET", f"/items/{iid}/description")
        desc = r.json().get("plain_text", "") if r.status_code == 200 else ""
        items[iid] = {
            "sku": f["sku"], "id": iid, "title": it.get("title"), "status": it.get("status"),
            "category_id": it.get("category_id"), "sold_quantity": it.get("sold_quantity"),
            "catalog_listing": it.get("catalog_listing"), "listing_type_id": it.get("listing_type_id"),
            "shipping_mode": (it.get("shipping") or {}).get("mode"), "permalink": it.get("permalink"),
            "attributes": [{"id": a.get("id"), "name": a.get("name"), "value_id": a.get("value_id"),
                            "value_name": a.get("value_name")} for a in it.get("attributes") or []],
            "description": desc,
        }
        categorias.setdefault(it.get("category_id"), None)
log(f"Publicaciones encontradas: {len(items)} | SKU sin publicación: {len(no_encontrados)}")

for cat in list(categorias):
    info = ml.get(f"/categories/{cat}")
    attrs = ml.get(f"/categories/{cat}/attributes")
    categorias[cat] = {
        "name": info.get("name"),
        "path": " > ".join(x["name"] for x in info.get("path_from_root") or []),
        "attributes": [{
            "id": a.get("id"), "name": a.get("name"), "value_type": a.get("value_type"),
            "tags": a.get("tags"), "hierarchy": a.get("hierarchy"),
            "allowed_units": [u.get("id") for u in a.get("allowed_units") or []],
            "values": [{"id": v.get("id"), "name": v.get("name")} for v in (a.get("values") or [])][:60],
        } for a in attrs],
    }
log(f"Categorías: {len(categorias)}")
for c, v in categorias.items():
    n = sum(1 for i in items.values() if i["category_id"] == c)
    print(f"   {c} {v['path']} ({n} publicaciones)")

os.makedirs("salida", exist_ok=True)
json.dump({"items": items, "categorias": categorias, "sin_publicacion": no_encontrados},
          open("salida/ml_fichas_export.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
