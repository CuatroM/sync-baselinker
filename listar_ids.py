"""Lista catálogos (inventarios), grupos de precio y depósitos de BaseLinker."""
import json
import os
import sync

bl = sync.BaseLinker(os.environ["BL_TOKEN"])
inv = bl.llamar("getInventories", {}).get("inventories", [])
grupos = {g["price_group_id"]: g for g in
          bl.llamar("getInventoryPriceGroups", {}).get("price_groups", [])}
depositos = {w["warehouse_type"] + "_" + str(w["warehouse_id"]): w for w in
             bl.llamar("getInventoryWarehouses", {}).get("warehouses", [])}

print("\n=== CATÁLOGOS / INVENTARIOS ===")
for i in inv:
    print(f"\ninventory_id={i['inventory_id']}  nombre={i.get('name')!r}  "
          f"por defecto={i.get('is_default')}")
    print(f"  grupos de precio: {i.get('price_groups')} (por defecto {i.get('default_price_group')})")
    print(f"  depósitos: {i.get('warehouses')} (por defecto {i.get('default_warehouse')})")
    d = bl.llamar("getInventoryProductsList", {"inventory_id": i["inventory_id"], "page": 1})
    prods = d.get("products") or {}
    print(f"  productos (1ra página, máx 1000): {len(prods)}")
    for pid, p in list(prods.items())[:3]:
        print(f"    ej: id={pid} sku={p.get('sku')!r} stock={p.get('stock')} precios={p.get('prices')}")

print("\n=== GRUPOS DE PRECIO ===")
for gid, g in grupos.items():
    print(f"  price_group_id={gid}  nombre={g.get('name')!r}  moneda={g.get('currency')}  "
          f"por defecto={g.get('is_default')}")

print("\n=== DEPÓSITOS ===")
for wid, w in depositos.items():
    print(f"  warehouse_id={wid}  nombre={w.get('name')!r}  stock editable={w.get('stock_edition')}  "
          f"por defecto={w.get('is_default')}")
