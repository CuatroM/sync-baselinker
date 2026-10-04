"""Compara los SKU del inventario Connect (#FEMA) con el inventario completo y la lista FEMA."""
import os
import yaml
import sync

bl = sync.BaseLinker(os.environ["BL_TOKEN"])
connect = bl.catalogo(56397)
completo = bl.catalogo(61608)
cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
lista = sync.leer_lista(sync.descargar_lista(cfg), cfg)

def norm(s):
    return sync.normalizar_codigo(s)

sku_connect = [norm(p.get("sku")) for p in connect.values()]
con_sufijo = [s for s in sku_connect if s.endswith("#FEMA")]
base_connect = {s[:-5] for s in con_sufijo}
sku_completo = {norm(p.get("sku")) for p in completo.values()}
sku_lista = set(lista)

print(f"Connect: {len(connect)} productos, {len(con_sufijo)} con '#FEMA'")
print("Ejemplos Connect:", sku_connect[:15])
print("Ejemplos Completo:", sorted(sku_completo)[:15])
print(f"Base Connect (sin #FEMA) que están en Completo: {len(base_connect & sku_completo)}")
print(f"Base Connect (sin #FEMA) que están en lista FEMA: {len(base_connect & sku_lista)}")
print("Ej. coincidencias:", sorted(base_connect & sku_lista)[:10])
print("Ej. NO coinciden:", sorted(base_connect - sku_lista)[:15])
# ¿Coincidencia por EAN o nombre?
ej = list(connect.items())[:5]
d = bl.llamar("getInventoryProductsData", {"inventory_id": 56397, "products": [int(k) for k, _ in ej]})
for pid, p in (d.get("products") or {}).items():
    tf = p.get("text_fields") or {}
    print(f"  {pid}: sku={p.get('sku')} ean={p.get('ean')} nombre={tf.get('name')!r} "
          f"extra={ {k: v for k, v in tf.items() if k.startswith('features') or 'extra' in k} }")
