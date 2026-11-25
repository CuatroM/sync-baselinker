"""
TORREON: sincroniza STOCK y PRECIO de BaseLinker con la web de TORREON (Tiendanube).

Stock (pedido por el usuario: con stock se suman 5 unidades para que BaseLinker no pause):
  - la web muestra stock N > 0 y en BaseLinker está en 0 o menos  -> N + 5
  - la web dice "Sin stock" (o el producto ya no existe) y en BaseLinker hay stock -> 0
  - en cualquier otro caso no se cambia (se respeta lo que se descuenta con las ventas)
Precio: precio de la web − 30 % (redondeado), si difiere del de BaseLinker.
Inventario: config.yaml sección torreon (inventory_id; price_group_id o el grupo por defecto del inventario).
Productos: datos/torreon_fichas.json (campo "web"). Si falla más del 10 % de las páginas no escribe nada.
DRY_RUN=1: solo informa.
"""
import csv
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

from sync import LOTE, BaseLinker, log, normalizar_codigo
from torreon_web import leer

BASE = Path(__file__).parent
dry = (os.environ.get("DRY_RUN") or "0").strip() == "1"
EXTRA_STOCK, DESCUENTO, MAX_FALLAS, SUFIJO = 5, 0.30, 0.10, "#TORREON"

cfg = yaml.safe_load(open(BASE / "config.yaml", encoding="utf-8"))
b = cfg.get("torreon") or {}
fichas = [f for f in json.load(open(BASE / "datos/torreon_fichas.json", encoding="utf-8")) if f.get("web")]
log(f"{len(fichas)} productos TORREON | {'SIMULACIÓN' if dry else 'REAL'}")

leidos = {}
for f in fichas:
    leidos[f["sku"]] = leer(f["web"])
    time.sleep(0.6)
fallas = [k for k, v in leidos.items() if v.get("error") and v["error"] != "no existe"]
log(f"Web: {sum(1 for v in leidos.values() if v.get('disponible'))} con stock, "
    f"{sum(1 for v in leidos.values() if not v.get('error') and not v.get('disponible'))} sin stock, {len(fallas)} con error")
if len(fallas) > MAX_FALLAS * len(fichas):
    log("Demasiadas páginas con error (¿web caída o bloqueada?): no se cambia nada.")
    sys.exit(1)

bl = BaseLinker(os.environ["BL_TOKEN"])
inv = int(b["inventory_id"])
grupo = str(b.get("price_group_id") or "")
if not grupo:
    i = next((x for x in bl.llamar("getInventories", {}).get("inventories", []) if int(x["inventory_id"]) == inv), {})
    grupo = str(i.get("default_price_group") or (i.get("price_groups") or [""])[0])
    log(f"Inventario {inv} {i.get('name')!r} | grupo de precio: {grupo}")
catalogo = bl.catalogo(inv)
depositos = Counter(d for p in catalogo.values() for d in (p.get("stock") or {}))
deposito = str(b.get("warehouse_id") or (depositos.most_common(1)[0][0] if depositos else ""))
log(f"{len(catalogo)} productos en BaseLinker | depósito: {deposito}")
indice = {normalizar_codigo(p.get("sku"))[: -len(SUFIJO)]: (pid, p) for pid, p in catalogo.items()
          if normalizar_codigo(p.get("sku")).endswith(SUFIJO)}

c_stock, c_precio, reporte, sin_vincular = {}, {}, [], []
for sku, w in leidos.items():
    if sku in fallas:
        continue
    cod = normalizar_codigo(sku)
    if cod not in indice:
        sin_vincular.append(sku)
        continue
    pid, p = indice[cod]
    st = int((p.get("stock") or {}).get(deposito) or 0)
    n = int(w.get("stock_web") or 0) if w.get("disponible") else 0
    if w.get("disponible") and w.get("stock_web") is None:
        n = 5   # disponible sin número en la web
    if n > 0 and st <= 0:
        c_stock[pid] = {deposito: n + EXTRA_STOCK}
        reporte.append([sku + SUFIJO, pid, "stock", st, n + EXTRA_STOCK, f"web con {n} en stock"])
    elif n == 0 and st > 0:
        c_stock[pid] = {deposito: 0}
        reporte.append([sku + SUFIJO, pid, "stock", st, 0, "sin stock en la web" if not w.get("error") else "ya no está en la web"])
    if w.get("precio_web"):
        nuevo = round(w["precio_web"] * (1 - DESCUENTO))
        actual = float((p.get("prices") or {}).get(grupo) or 0)
        if abs(nuevo - actual) > max(1, actual * 0.001):
            c_precio[pid] = {grupo: nuevo}
            reporte.append([sku + SUFIJO, pid, "precio", actual, nuevo, f"web ${w['precio_web']:,.2f} − 30 %"])
log(f"Cambios: {len(c_stock)} de stock, {len(c_precio)} de precio | no están en BaseLinker: {len(sin_vincular)}")
for r in reporte[:60]:
    log(f"  {r[0]} {r[2]}: {r[3]} -> {r[4]} ({r[5]})")

advertencias = []
if not dry:
    for metodo, cambios in (("updateInventoryProductsStock", c_stock), ("updateInventoryProductsPrices", c_precio)):
        ids = list(cambios)
        for i in range(0, len(ids), LOTE):
            r = bl.llamar(metodo, {"inventory_id": inv, "products": {pid: cambios[pid] for pid in ids[i:i + LOTE]}})
            log(f"{metodo}: {r.get('counter', 0)} actualizados")
            advertencias += [[pid, metodo, e] for pid, e in (r.get("warnings") or {}).items()]
else:
    log("SIMULACIÓN: no se escribió nada en BaseLinker")

carpeta = BASE / "reportes"
carpeta.mkdir(exist_ok=True)
sello = datetime.now().strftime("%Y%m%d-%H%M")
with open(carpeta / f"torreon-cambios-{sello}.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh, delimiter=";")
    w.writerow(["sku", "product_id", "campo", "antes", "ahora", "motivo", "(simulado)" if dry else ""])
    w.writerows(reporte)
with open(carpeta / f"torreon-sin-vincular-{sello}.csv", "w", newline="", encoding="utf-8-sig") as fh:
    csv.writer(fh, delimiter=";").writerows([["sku", "motivo"]] + [[s, "no está en BaseLinker"] for s in sorted(sin_vincular)]
                                            + [[k, leidos[k].get("error")] for k in fallas])
if advertencias:
    with open(carpeta / f"torreon-advertencias-{sello}.csv", "w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh, delimiter=";").writerows([["product_id", "método", "mensaje"]] + advertencias)
log("Listo.")
