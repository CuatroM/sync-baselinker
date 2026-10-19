"""
Agrega un sufijo (ej. "#EXTRAPOWER") al SKU de los productos de un inventario de BaseLinker que no lo tengan.
Pedido por el usuario. Solo cambia el SKU: con el primer producto verifica que ningún otro dato
(precio, stock, EAN, nombre, fotos...) haya cambiado; si cambió algo, se detiene.
Omite SKUs vacíos o que son el encabezado del Excel ("SINONIMO").
DRY_RUN=1 solo informa. Salida: salida/sufijo_resultado.csv
"""
import csv
import os

import sync

bl = sync.BaseLinker(os.environ["BL_TOKEN"])
inv = int(os.environ["INVENTARIO"])
sufijo = os.environ.get("SUFIJO") or "#EXTRAPOWER"
dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
CONTROL = ("ean", "prices", "stock", "images", "text_fields", "tax_rate", "weight")

nombre = next((i.get("name") for i in bl.llamar("getInventories", {}).get("inventories") or []
               if int(i["inventory_id"]) == inv), None)
if not nombre:
    raise SystemExit(f"No existe el inventario {inv}")
sync.log(f"Inventario {inv}: {nombre} | sufijo {sufijo} | {'SIMULACIÓN' if dry else 'REAL'}")
prods = bl.catalogo(inv)
pend, omit = [], []
for pid, p in prods.items():
    sku = str(p.get("sku") or "").replace("﻿", "").strip()
    if not sku or sku.upper() in ("SINONIMO", "SKU", "CODIGO"):
        omit.append((pid, sku, "vacío o encabezado del Excel"))
    elif not sku.upper().endswith(sufijo.upper()):
        pend.append((pid, sku, sku + sufijo))
sync.log(f"A renombrar: {len(pend)} | omitidos: {omit}")


def datos(pid):
    return (bl.llamar("getInventoryProductsData", {"inventory_id": inv, "products": [int(pid)]})
            .get("products") or {}).get(str(pid)) or {}


filas = []
for i, (pid, antes, nuevo) in enumerate(pend):
    if dry:
        filas.append({"product_id": pid, "antes": antes, "despues": nuevo, "resultado": "SE RENOMBRARÍA"})
        continue
    previo = datos(pid) if i == 0 else None
    try:
        bl.llamar("addInventoryProduct", {"inventory_id": str(inv), "product_id": str(pid), "sku": nuevo})
        res = "OK"
    except RuntimeError as e:
        res = f"ERROR {e}"
        if i == 0:
            raise
    if i == 0:
        post = datos(pid)
        cambios = [c for c in CONTROL if previo.get(c) != post.get(c)]
        if post.get("sku") != nuevo or cambios:
            raise SystemExit(f"Prueba en {pid}: cambiaron {cambios} (sku {post.get('sku')!r}). Se detiene.")
        sync.log(f"Prueba OK en {pid}: solo cambió el SKU ({antes} -> {nuevo})")
    filas.append({"product_id": pid, "antes": antes, "despues": nuevo, "resultado": res})
    if (i + 1) % 500 == 0:
        sync.log(f"  {i + 1}/{len(pend)}")
os.makedirs("salida", exist_ok=True)
with open("salida/sufijo_resultado.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=["product_id", "antes", "despues", "resultado"], delimiter=";")
    w.writeheader()
    w.writerows(filas)
from collections import Counter
sync.log("Resumen: " + str(Counter(f["resultado"][:20] for f in filas)))
