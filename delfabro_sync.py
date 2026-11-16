"""
DELFABRO: sincroniza el STOCK de BaseLinker con la disponibilidad de la web de Delfabro (solo stock; el precio
es el de la lista PDF y no se toca).

Regla (pedida por el usuario):
  - la web dice "Sin existencias" (o el producto ya no existe) y en BaseLinker hay stock  -> 0
  - la web dice disponible y en BaseLinker está en 0                                     -> 10
  - en cualquier otro caso no se cambia (así se respeta lo que se va descontando con las ventas)
BaseLinker pasa el stock a Mercado Libre, que pausa o reactiva la publicación.

Inventario: el de BaseLinker cuyo nombre contiene "DELFABRO" (o config.yaml, sección delfabro).
Productos: los de datos/delfabro_fichas.json (campo "web").
Seguridad: si falla más del 10 % de las páginas (web caída o bloqueada) no escribe nada.
DRY_RUN=1: solo informa.
"""
import csv
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import requests
import yaml

from sync import LOTE, BaseLinker, log, normalizar_codigo

BASE = Path(__file__).parent
dry = (os.environ.get("DRY_RUN") or "0").strip() == "1"
STOCK_DISPONIBLE = int(os.environ.get("STOCK_DISPONIBLE") or 10)
MAX_FALLAS = 0.10
SUFIJO = "#DELFABRO"

cfg = yaml.safe_load(open(BASE / "config.yaml", encoding="utf-8"))
fichas = [f for f in json.load(open(BASE / "datos/delfabro_fichas.json", encoding="utf-8")) if f.get("web")]
log(f"{len(fichas)} productos DELFABRO con página en la web | {'SIMULACIÓN' if dry else 'REAL'}")

web = requests.Session()
web.headers["User-Agent"] = "Mozilla/5.0 (sincronización de stock)"


def disponible(url):
    """True / False / 'no existe' / 'error: ...'"""
    err = None
    for intento in range(3):
        try:
            r = web.get(url, timeout=40)
            if r.status_code == 404:
                return "no existe"
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            h = r.text
            m = re.search(r'<p class="stock ([^"]*)"[^>]*>(.*?)</p>', h, re.S)
            txt = (m.group(1) + " " + re.sub(r"<[^>]+>", " ", m.group(2))) if m else ""
            clases = " ".join(re.findall(r'class="[^"]*\b(?:type-product|product)\b[^"]*"', h)[:3])
            if re.search(r"sin existencia|agotado|out-of-stock|out of stock", txt, re.I) or "outofstock" in clases:
                return False
            if "type-product" in h or "product_title" in h:
                return True
            raise RuntimeError("la página no parece un producto")
        except Exception as e:
            err = e
            time.sleep(3 * (intento + 1))
    return f"error: {err}"


leidos = {}
for k, f in enumerate(fichas, 1):
    leidos[f["sku"]] = disponible(f["web"])
    time.sleep(0.7)
    if k % 50 == 0:
        log(f"  {k}/{len(fichas)} páginas leídas")
fallas = [k for k, v in leidos.items() if isinstance(v, str) and v.startswith("error")]
c = Counter("disponible" if v is True else "sin existencias" if v is False else v if v == "no existe" else "error"
            for v in leidos.values())
log(f"Web: {dict(c)}")
for k in fallas[:10]:
    log(f"  {k}: {leidos[k]}")
if len(fallas) > MAX_FALLAS * len(fichas):
    log("Demasiadas páginas con error (¿web caída o bloqueada?): no se cambia nada.")
    sys.exit(1)

# ---------------- BaseLinker
bl = BaseLinker(os.environ["BL_TOKEN"])
b = cfg.get("delfabro") or {}
inv = b.get("inventory_id")
if not inv:
    invs = bl.llamar("getInventories", {}).get("inventories", [])
    cand = [i for i in invs if "DELFABRO" in (i.get("name") or "").upper()]
    if not cand:
        log(f"No encontré un inventario con DELFABRO en el nombre. Hay: {[i.get('name') for i in invs]}")
        sys.exit(1)
    inv = cand[0]["inventory_id"]
    log(f"Inventario: {inv} {cand[0].get('name')!r} | depósito por defecto: {cand[0].get('default_warehouse')}")
inv = int(inv)
catalogo = bl.catalogo(inv)
depositos = Counter(d for p in catalogo.values() for d in (p.get("stock") or {}))
deposito = str(b.get("warehouse_id") or (depositos.most_common(1)[0][0] if depositos else ""))
log(f"{len(catalogo)} productos en BaseLinker | depósito de stock: {deposito} (encontrados: {dict(depositos)})")
indice = {}
for pid, p in catalogo.items():
    cod = normalizar_codigo(p.get("sku"))
    if cod.endswith(SUFIJO):
        indice[cod[: -len(SUFIJO)]] = (pid, p)

cambios, reporte, sin_vincular = {}, [], []
for sku, v in leidos.items():
    if isinstance(v, str) and v.startswith("error"):
        continue
    cod = normalizar_codigo(sku)
    if cod not in indice:
        sin_vincular.append(sku)
        continue
    pid, p = indice[cod]
    st = int((p.get("stock") or {}).get(deposito) or 0)
    if v is True and st <= 0:
        cambios[pid] = {deposito: STOCK_DISPONIBLE}
        reporte.append([sku + SUFIJO, pid, st, STOCK_DISPONIBLE, "volvió a estar disponible en la web"])
    elif v is not True and st > 0:
        cambios[pid] = {deposito: 0}
        reporte.append([sku + SUFIJO, pid, st, 0, "sin existencias en la web" if v is False else "ya no está en la web"])
log(f"Cambios de stock: {len(cambios)} (a 0: {sum(1 for r in reporte if r[3] == 0)}, "
    f"a {STOCK_DISPONIBLE}: {sum(1 for r in reporte if r[3] != 0)}) | no están en BaseLinker: {len(sin_vincular)}")
for r in reporte[:40]:
    log(f"  {r[0]}: {r[2]} -> {r[3]} ({r[4]})")

advertencias = []
if not dry and cambios:
    ids = list(cambios)
    for i in range(0, len(ids), LOTE):
        parte = {pid: cambios[pid] for pid in ids[i:i + LOTE]}
        r = bl.llamar("updateInventoryProductsStock", {"inventory_id": inv, "products": parte})
        log(f"updateInventoryProductsStock: {r.get('counter', 0)} actualizados")
        advertencias += [[pid, e] for pid, e in (r.get("warnings") or {}).items()]
elif dry:
    log("SIMULACIÓN: no se escribió nada en BaseLinker")

carpeta = BASE / "reportes"
carpeta.mkdir(exist_ok=True)
sello = datetime.now().strftime("%Y%m%d-%H%M")
with open(carpeta / f"delfabro-cambios-{sello}.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh, delimiter=";")
    w.writerow(["sku", "product_id", "stock antes", "stock ahora", "motivo", "(simulado)" if dry else ""])
    w.writerows(reporte)
with open(carpeta / f"delfabro-sin-vincular-{sello}.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh, delimiter=";")
    w.writerow(["sku", "motivo"])
    w.writerows([[s, "no está en BaseLinker"] for s in sorted(sin_vincular)] + [[k, leidos[k]] for k in fallas])
if advertencias:
    with open(carpeta / f"delfabro-advertencias-{sello}.csv", "w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh, delimiter=";").writerows([["product_id", "mensaje"]] + advertencias)
log("Listo.")
