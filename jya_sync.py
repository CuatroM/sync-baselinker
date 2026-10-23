"""
Sincroniza precio y stock de J&A Distribuidora (tienda Empretienda jyadistribuidora.ar) con BaseLinker.

1. Lee la página pública de cada producto (la dirección está en datos/jya_fichas.json, campo "web").
   La página trae el stock y el precio de cada variante (variable "stock" del HTML).
   - stock = suma de las variantes (ilimitado -> STOCK_ILIMITADO)
   - precio = el que muestra la web sin IVA (si hay oferta activa, el de oferta)
   - producto que ya no existe en la web (404) -> stock 0 (BaseLinker lo pasa a ML, que pausa la publicación)
   - productos nuevos de la web (no están en las fichas): solo se informan, se agregan a mano
2. Compara con el inventario J&A de BaseLinker (config.yaml, sección jya) y actualiza solo lo que cambió.

Seguridad: si fallan muchas páginas (web caída o bloqueada) no escribe nada.
DRY_RUN=1: solo informa. STOCK_MODO=real (lo de la web) | uno (1 si hay stock, 0 si no).
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
modo = (os.environ.get("STOCK_MODO") or "real").strip().lower()
STOCK_ILIMITADO = 99
MAX_FALLAS = 0.10          # si falla más del 10 % de las páginas, no se escribe nada
SUFIJO = "#JYA"

cfg = yaml.safe_load(open(BASE / "config.yaml", encoding="utf-8"))
b = cfg["jya"]
inv, grupo = int(b["inventory_id"]), str(b["price_group_id"])

fichas = [f for f in json.load(open(BASE / "datos/jya_fichas.json", encoding="utf-8")) if f.get("web")]
log(f"{len(fichas)} productos J&A con página en la tienda | {'SIMULACIÓN' if dry else 'REAL'} | stock: {modo}")

web = requests.Session()
web.headers["User-Agent"] = "Mozilla/5.0 (sincronización de stock)"
RE_STOCK = re.compile(r"var\s+stock\s*=\s*(\[.*?\]);\s*\n", re.S)


PAUSA = float(os.environ.get("PAUSA_WEB") or 1.3)   # la tienda bloquea (403) si se le piden páginas muy rápido
_ultimo = [0.0]


def pedir(url, **kw):
    """GET respetando una pausa entre pedidos; si la tienda responde 403 (bloqueo por exceso), espera y reintenta."""
    for espera in (0, 90, 180, 300):
        if espera:
            log(f"La tienda respondió 403: espero {espera} s")
            time.sleep(espera)
        falta = PAUSA - (time.time() - _ultimo[0])
        if falta > 0:
            time.sleep(falta)
        _ultimo[0] = time.time()
        r = web.get(url, timeout=40, **kw)
        if r.status_code != 403:
            return r
    return r


def leer(f):
    """(sku, {'stock', 'precio'} | 'no existe' | 'error: ...')"""
    for intento in range(3):
        try:
            r = pedir(f["web"])
            if r.status_code == 404:
                return f["sku"], "no existe"
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            m = RE_STOCK.search(r.text)
            if not m:
                raise RuntimeError("la página no trae el stock")
            var = json.loads(m.group(1))
            if not var:
                return f["sku"], {"stock": 0, "precio": None}
            st = STOCK_ILIMITADO if any(v.get("s_ilimitado") for v in var) else \
                sum(max(int(v.get("s_cantidad") or 0), 0) for v in var)
            # precio tal cual se ve en la web: si la variante tiene oferta activa, el precio de oferta
            precios = [float(v["s_precio_oferta"]) if v.get("s_oferta") and float(v.get("s_precio_oferta") or 0) > 0
                       else float(v["s_precio"]) for v in var if v.get("s_precio")]
            return f["sku"], {"stock": st, "precio": max(precios) if precios else None}
        except Exception as e:
            err = e
            time.sleep(3 * (intento + 1))
    return f["sku"], f"error: {err}"


t0 = time.time()
leidos = {}
for n, f in enumerate(fichas, 1):
    k, v = leer(f)
    leidos[k] = v
    if n % 200 == 0:
        log(f"  {n}/{len(fichas)} páginas leídas")
fallas = [k for k, v in leidos.items() if isinstance(v, str) and v.startswith("error")]
log(f"Leídos en {time.time() - t0:.0f} s | ok: {sum(isinstance(v, dict) for v in leidos.values())} | "
    f"ya no existen: {sum(v == 'no existe' for v in leidos.values())} | con error: {len(fallas)}")
for k in fallas[:10]:
    log(f"  {k}: {leidos[k]}")
if len(fallas) > MAX_FALLAS * len(fichas):
    log("Demasiadas páginas con error (¿la web está caída o bloqueó el acceso?): no se cambia nada.")
    sys.exit(1)

web_datos = {}
for k, v in leidos.items():
    if isinstance(v, dict):
        st = v["stock"] if modo == "real" else (1 if v["stock"] > 0 else 0)
        web_datos[normalizar_codigo(k)] = {"stock": st, "precio": v["precio"]}
    elif v == "no existe":
        web_datos[normalizar_codigo(k)] = {"stock": 0, "precio": None}

# ---------------- productos nuevos en la web (solo se informan)
conocidas = {f["web"].rstrip("/") for f in fichas}
nuevos, vistos = [], set()
for pag in range(1, 80):
    try:
        r = pedir("https://www.jyadistribuidora.ar/productos", params={"page": pag})
        links = set(re.findall(r'href="(https://www\.jyadistribuidora\.ar/[a-z0-9-]+/[a-z0-9-]+)"', r.text))
    except Exception as e:
        log(f"Listado página {pag}: {e}")
        break
    links -= vistos
    if not links:
        break
    vistos |= links
nuevos = sorted(u for u in vistos if u.rstrip("/") not in conocidas)
log(f"Listado de la tienda: {len(vistos)} productos | nuevos (no publicados): {len(nuevos)}")

# ---------------- BaseLinker
bl = BaseLinker(os.environ["BL_TOKEN"])
catalogo = bl.catalogo(inv)
depositos = Counter(d for p in catalogo.values() for d in (p.get("stock") or {}))
deposito = str(b.get("warehouse_id") or (depositos.most_common(1)[0][0] if depositos else ""))
log(f"Depósito de stock: {deposito} (encontrados: {dict(depositos)})")
indice = {}
for pid, p in catalogo.items():
    c = normalizar_codigo(p.get("sku"))
    if c.endswith(SUFIJO):
        c = c[: -len(SUFIJO)]
    if c:
        indice[c] = (pid, p)

cambios_stock, cambios_precio, reporte, sin_vincular = {}, {}, [], []
for cod, d in web_datos.items():
    if cod not in indice:
        sin_vincular.append(cod)
        continue
    pid, p = indice[cod]
    st_bl = (p.get("stock") or {}).get(deposito)
    pr_bl = (p.get("prices") or {}).get(grupo)
    if st_bl is None or int(st_bl) != d["stock"]:
        cambios_stock[pid] = {deposito: d["stock"]}
        reporte.append([cod + SUFIJO, pid, "stock", st_bl, d["stock"]])
    if d["precio"] and (pr_bl is None or abs(float(pr_bl) - d["precio"]) >= 0.01):
        cambios_precio[pid] = {grupo: d["precio"]}
        reporte.append([cod + SUFIJO, pid, "precio", pr_bl, d["precio"]])
log(f"Cambios: {len(cambios_stock)} de stock, {len(cambios_precio)} de precio | "
    f"productos de la web que no están en BaseLinker: {len(sin_vincular)}")

advertencias = []
if not dry:
    for metodo, cambios in (("updateInventoryProductsStock", cambios_stock),
                            ("updateInventoryProductsPrices", cambios_precio)):
        ids = list(cambios)
        for i in range(0, len(ids), LOTE):
            parte = {pid: cambios[pid] for pid in ids[i:i + LOTE]}
            r = bl.llamar(metodo, {"inventory_id": inv, "products": parte})
            log(f"{metodo}: {r.get('counter', 0)} actualizados")
            advertencias += [[pid, metodo, e] for pid, e in (r.get("warnings") or {}).items()]
else:
    log("SIMULACIÓN: no se escribió nada en BaseLinker")

carpeta = BASE / "reportes"
carpeta.mkdir(exist_ok=True)
sello = datetime.now().strftime("%Y%m%d-%H%M")
with open(carpeta / f"jya-cambios-{sello}.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh, delimiter=";")
    w.writerow(["sku", "product_id", "campo", "antes", "ahora", "(simulado)" if dry else ""])
    w.writerows(reporte)
with open(carpeta / f"jya-sin-vincular-{sello}.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh, delimiter=";")
    w.writerow(["codigo_web", "motivo"])
    w.writerows([[c, "no está en BaseLinker"] for c in sorted(sin_vincular)] +
                [[k, leidos[k]] for k in fallas])
with open(carpeta / f"jya-nuevos-{sello}.csv", "w", newline="", encoding="utf-8-sig") as fh:
    csv.writer(fh, delimiter=";").writerows([["link_producto_nuevo"]] + [[u] for u in nuevos])
if advertencias:
    with open(carpeta / f"jya-advertencias-{sello}.csv", "w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh, delimiter=";").writerows([["product_id", "metodo", "mensaje"]] + advertencias)
log("Listo.")
