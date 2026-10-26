"""
Aplica un descuento (%) al precio actual en BaseLinker de una lista de SKU. Pedido por el usuario.
- INVENTARIO: extrapower | jya | fema (de config.yaml). Se busca el SKU con el sufijo (#EXTRAPOWER, #JYA, #FEMA).
- LISTA: archivo de texto con un código por línea (sin sufijo). PORCENTAJE: por ejemplo 5.
- Toca solo el grupo de precio del inventario en config.yaml (EXTRAPOWER: 66282).
- Protección contra aplicarlo dos veces: guarda en datos/descuentos_aplicados.json lo aplicado
  (lista + porcentaje + precio antes/después). Si ya se aplicó esa misma lista con ese %, no hace nada
  salvo que se pida FORZAR=1. REVERTIR=1 vuelve a los precios "antes" guardados.
DRY_RUN=1 (por defecto): solo informa precio actual y nuevo.
"""
import csv
import json
import os
from datetime import datetime
from pathlib import Path

import yaml

from sync import LOTE, BaseLinker, log, normalizar_codigo

BASE = Path(__file__).parent
dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
revertir = (os.environ.get("REVERTIR") or "0").strip() == "1"
forzar = (os.environ.get("FORZAR") or "0").strip() == "1"
nombre_inv = (os.environ.get("INVENTARIO") or "extrapower").strip().lower()
lista_f = (os.environ.get("LISTA") or "datos/xp_descuento_5.txt").strip()
pct = float((os.environ.get("PORCENTAJE") or "5").replace(",", "."))

cfg = yaml.safe_load(open(BASE / "config.yaml", encoding="utf-8"))
sec = {"extrapower": "extrapower", "jya": "jya", "fema": "baselinker"}[nombre_inv]
sufijo = {"extrapower": "#EXTRAPOWER", "jya": "#JYA", "fema": "#FEMA"}[nombre_inv]
inv, grupo = int(cfg[sec]["inventory_id"]), str(cfg[sec]["price_group_id"])
codigos = [normalizar_codigo(l) for l in open(BASE / lista_f, encoding="utf-8") if l.strip()]
codigos = [c[:-len(sufijo)] if c.endswith(sufijo) else c for c in codigos]
clave = f"{nombre_inv}|{lista_f}|{pct:g}"
log(f"{len(codigos)} SKU | inventario {inv} grupo de precio {grupo} | descuento {pct:g}% | "
    f"{'REVERTIR' if revertir else 'aplicar'} | {'SIMULACIÓN' if dry else 'REAL'}")

registro_f = BASE / "datos/descuentos_aplicados.json"
registro = json.load(open(registro_f, encoding="utf-8")) if registro_f.exists() else {}

bl = BaseLinker(os.environ["BL_TOKEN"])
grupos = bl.llamar("getInventoryPriceGroups", {}).get("price_groups") or []
log("Grupos de precio de la cuenta: " + ", ".join(f"{g.get('price_group_id')}={g.get('name')}" for g in grupos))
catalogo = bl.catalogo(inv)
indice = {}
for pid, p in catalogo.items():
    s = normalizar_codigo(p.get("sku"))
    if s.endswith(sufijo):
        indice[s[:-len(sufijo)]] = (pid, p)
    elif s and s not in indice:
        indice[s] = (pid, p)

filas, cambios, faltan = [], {}, []
anterior = registro.get(clave, {}).get("precios", {})
if not revertir and clave in registro and not forzar:
    log(f"ESTE DESCUENTO YA SE APLICÓ el {registro[clave]['fecha']}. No se hace nada (FORZAR=1 para repetirlo).")
    codigos = []
for c in codigos:
    if c not in indice:
        faltan.append(c)
        filas.append([c + sufijo, "", "", "", "NO ESTÁ EN BASELINKER"])
        continue
    pid, p = indice[c]
    precios = p.get("prices") or {}
    actual = precios.get(grupo)
    otros = {k: v for k, v in precios.items() if k != grupo}
    if revertir:
        if c not in anterior:
            filas.append([c + sufijo, pid, actual, "", "sin precio guardado para revertir"])
            continue
        nuevo = anterior[c]["antes"]
    else:
        if actual in (None, "", 0):
            filas.append([c + sufijo, pid, actual, "", "sin precio en el grupo"])
            continue
        nuevo = round(float(actual) * (1 - pct / 100), 2)
    cambios[pid] = {grupo: nuevo}
    anterior.setdefault(c, {"antes": actual})
    anterior[c]["despues"] = nuevo
    filas.append([c + sufijo, pid, actual, nuevo, f"otros grupos: {otros}" if otros else ""])
    log(f"{c}{sufijo}: {actual} -> {nuevo}" + (f" | otros grupos de precio: {otros}" if otros else ""))

log(f"A cambiar: {len(cambios)} | no están en BaseLinker: {len(faltan)} {faltan}")
if not dry and cambios:
    ids, n = list(cambios), 0
    for i in range(0, len(ids), LOTE):
        r = bl.llamar("updateInventoryProductsPrices",
                      {"inventory_id": inv, "products": {pid: cambios[pid] for pid in ids[i:i + LOTE]}})
        n += int(r.get("counter") or 0)
        for pid, e in (r.get("warnings") or {}).items():
            log(f"advertencia {pid}: {e}")
    log(f"BaseLinker: {n} precios actualizados")
    if revertir:
        registro.pop(clave, None)
    else:
        registro[clave] = {"fecha": datetime.now().strftime("%Y-%m-%d %H:%M"), "precios": anterior}
    json.dump(registro, open(registro_f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

os.makedirs("salida", exist_ok=True)
with open("salida/descuento.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh, delimiter=";")
    w.writerow(["sku", "product_id", "precio_antes", "precio_nuevo", "nota"])
    w.writerows(filas)
