"""
Solo lectura: lista los inventarios de BaseLinker y exporta los productos del que coincida con BUSCAR
(por defecto "EXTRAPOWER"): SKU, nombre, EAN, precio, stock, fotos y características que tenga cargadas.
No escribe nada en BaseLinker. Salida: salida/bl_inventario.csv y salida/bl_inventario.json
"""
import csv
import json
import os

import sync

bl = sync.BaseLinker(os.environ["BL_TOKEN"])
buscar = (os.environ.get("BUSCAR") or "EXTRAPOWER").upper()
invs = bl.llamar("getInventories", {}).get("inventories") or []
for i in invs:
    sync.log(f"Inventario {i.get('inventory_id')}: {i.get('name')}")
elegidos = [i for i in invs if buscar in (i.get("name") or "").upper()]
if not elegidos:
    raise SystemExit(f"No hay inventario con '{buscar}' en el nombre")
inv = int(elegidos[0]["inventory_id"])
sync.log(f"Exportando inventario {inv}: {elegidos[0].get('name')}")
lista = bl.catalogo(inv)
ids = list(lista)
filas, crudo = [], {}
for k in range(0, len(ids), 100):
    d = bl.llamar("getInventoryProductsData", {"inventory_id": inv, "products": [int(x) for x in ids[k:k + 100]]})
    for pid, p in (d.get("products") or {}).items():
        tf = p.get("text_fields") or {}
        crudo[pid] = p
        filas.append({"product_id": pid, "sku": p.get("sku"), "ean": p.get("ean"), "nombre": tf.get("name"),
                      "precio": json.dumps(p.get("prices"), ensure_ascii=False), "stock": json.dumps(p.get("stock")),
                      "fotos": len(p.get("images") or {}), "descripcion": (tf.get("description") or "")[:200],
                      "caracteristicas": json.dumps(tf.get("features"), ensure_ascii=False)[:300],
                      "categoria_bl": p.get("category_id"), "peso": p.get("weight")})
os.makedirs("salida", exist_ok=True)
with open("salida/bl_inventario.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()) if filas else ["sku"], delimiter=";")
    w.writeheader()
    w.writerows(filas)
json.dump({"inventory_id": inv, "nombre": elegidos[0].get("name"), "productos": crudo},
          open("salida/bl_inventario.json", "w", encoding="utf-8"), ensure_ascii=False)
sync.log(f"{len(filas)} productos | con fotos: {sum(1 for f in filas if f['fotos'])} | con EAN: {sum(1 for f in filas if f['ean'])}")
