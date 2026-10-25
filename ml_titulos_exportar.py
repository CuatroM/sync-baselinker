"""
Solo lectura: datos para proponer títulos nuevos.
- Publicaciones activas, pausadas y en revisión: id, SKU, título, estado, ventas, categoría, atributos.
- Por categoría: nombre y ruta, largo máximo del título, y las búsquedas más populares en ML (tendencias).
Salida: salida/titulos_datos.json
"""
import json
import os

from ml.cliente import ML, log, sku_de

ml = ML(os.environ.get("ML_CODIGO"))
ids = []
for e in ("active", "paused", "under_review"):
    try:
        lote = ml.ids_publicaciones(status=e)
    except Exception as ex:
        log(f"{e}: {ex}")
        lote = []
    log(f"{e}: {len(lote)}")
    ids += lote
ids = list(dict.fromkeys(ids))
items = []
for it in ml.publicaciones(ids, atributos="id,title,status,sub_status,sold_quantity,category_id,domain_id,"
                                          "attributes,seller_custom_field,listing_type_id,catalog_listing,tags"):
    items.append({"id": it["id"], "sku": sku_de(it), "titulo": it.get("title"), "estado": it.get("status"),
                  "ventas": it.get("sold_quantity") or 0, "categoria": it.get("category_id"),
                  "dominio": it.get("domain_id"), "catalogo": bool(it.get("catalog_listing")),
                  "atributos": {a.get("name"): a.get("value_name") for a in it.get("attributes") or []
                                if a.get("value_name") and a.get("id") not in ("SELLER_SKU", "GTIN", "EMPTY_GTIN_REASON",
                                                                                "PART_NUMBER", "MPN", "IMPORT_DUTY")}})
log(f"{len(items)} publicaciones")
cats = {}
for c in sorted({i["categoria"] for i in items if i["categoria"]}):
    d = {"nombre": "", "ruta": "", "max_titulo": 60, "tendencias": []}
    r = ml.pedir("GET", f"/categories/{c}")
    if r.status_code == 200:
        j = r.json()
        d["nombre"] = j.get("name")
        d["ruta"] = " > ".join(p["name"] for p in j.get("path_from_root") or [])
        d["max_titulo"] = (j.get("settings") or {}).get("max_title_length") or 60
    t = ml.pedir("GET", f"/trends/MLA/{c}")
    if t.status_code == 200:
        d["tendencias"] = [x.get("keyword") for x in (t.json() or [])][:30]
    cats[c] = d
log(f"{len(cats)} categorías | con tendencias: {sum(1 for d in cats.values() if d['tendencias'])}")
os.makedirs("salida", exist_ok=True)
json.dump({"items": items, "categorias": cats}, open("salida/titulos_datos.json", "w", encoding="utf-8"),
          ensure_ascii=False)
