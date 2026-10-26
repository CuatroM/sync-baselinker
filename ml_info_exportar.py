"""
Solo lectura: datos para generar las 2 imágenes informativas (ficha técnica + partes/usos) de FEMA y EXTRAPOWER.
Publicaciones activas, pausadas y en revisión que NO son de catálogo (a las de catálogo no se les pueden cambiar fotos).
Por publicación: id, SKU, título, estado, categoría, dominio, atributos, fotos (id y url), ventas.
Salida: salida/info_datos.json
"""
import json
import os

from ml.cliente import ML, log, sku_de

ml = ML(os.environ.get("ML_CODIGO"))
ids = []
for e in ("active", "paused"):
    ids += ml.ids_publicaciones(status=e)
try:
    ids += ml.ids_publicaciones(sub_status="waiting_for_patch")
except Exception as ex:
    log(f"waiting_for_patch: {ex}")
ids = list(dict.fromkeys(ids))
log(f"{len(ids)} publicaciones en total")
items = []
for it in ml.publicaciones(ids, atributos="id,title,status,sub_status,sold_quantity,category_id,domain_id,attributes,"
                                          "seller_custom_field,catalog_listing,pictures,family_name"):
    sku = sku_de(it) or ""
    if not sku.upper().endswith(("#FEMA", "#EXTRAPOWER")) or it.get("catalog_listing"):
        continue
    items.append({"id": it["id"], "sku": sku, "titulo": it.get("title"), "estado": it.get("status"),
                  "ventas": it.get("sold_quantity") or 0, "categoria": it.get("category_id"), "dominio": it.get("domain_id"),
                  "atributos": {a.get("name"): a.get("value_name") for a in it.get("attributes") or [] if a.get("value_name")},
                  "fotos": [{"id": p["id"], "url": p.get("secure_url"), "tam": p.get("max_size") or p.get("size")}
                            for p in it.get("pictures") or []]})
log(f"FEMA/EXTRAPOWER sin catálogo: {len(items)} | SKU distintos: {len({i['sku'] for i in items})}")
os.makedirs("salida", exist_ok=True)
json.dump(items, open("salida/info_datos.json", "w", encoding="utf-8"), ensure_ascii=False)
