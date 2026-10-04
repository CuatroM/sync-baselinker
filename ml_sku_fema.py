"""
Agrega '#FEMA' al SKU de las publicaciones de Mercado Libre que no lo tienen.

- Revisa publicaciones activas y pausadas.
- Solo toca las que tienen un SKU que existe en el inventario de BaseLinker
  (INVENTARIO COMPLETO FEMA 2026 MATIAS) como CÓDIGO#FEMA.
- Cambia ÚNICAMENTE el SKU. En la primera publicación verifica, antes de seguir, que no
  haya cambiado título, precio, stock, envío ni ningún otro atributo.
- DRY_RUN=1: solo informa qué cambiaría.
"""
import csv
import os
import sys

import yaml

import sync
from ml.cliente import ML, log, sku_de

SUFIJO = "#FEMA"
dry_run = os.environ.get("DRY_RUN", "0") == "1"
limite = int(os.environ.get("LIMITE") or 0)

# 1) SKU válidos del inventario de BaseLinker
cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
bl = sync.BaseLinker(os.environ["BL_TOKEN"])
catalogo = bl.catalogo(int(cfg["baselinker"]["inventory_id"]))
validos = {sync.normalizar_codigo(p.get("sku")) for p in catalogo.values()}
validos = {s[: -len(SUFIJO)] for s in validos if s.endswith(SUFIJO)}
log(f"Códigos con {SUFIJO} en BaseLinker: {len(validos)}")

# 2) Publicaciones a corregir
ml = ML(os.environ.get("ML_CODIGO"))
ids = ml.ids_publicaciones(status="active") + ml.ids_publicaciones(status="paused")
log(f"Publicaciones activas + pausadas: {len(ids)}")
pendientes, omitidas = [], []
for it in ml.publicaciones(ids):
    sku = sku_de(it).strip()
    if not sku or sku.upper().endswith(SUFIJO):
        continue
    fila = [it["id"], it.get("status"), sku, sku + SUFIJO, it.get("title")]
    (pendientes if sync.normalizar_codigo(sku) in validos else omitidas).append(fila)
log(f"Para corregir: {len(pendientes)} | Omitidas (código no está en BaseLinker): {len(omitidas)}")
if limite:
    pendientes = pendientes[:limite]


def foto(item_id):
    it = ml.get(f"/items/{item_id}")
    attrs = {a["id"]: a.get("value_name") for a in it.get("attributes") or []
             if a.get("id") != "SELLER_SKU"}
    return {"title": it.get("title"), "price": it.get("price"),
            "available_quantity": it.get("available_quantity"), "status": it.get("status"),
            "shipping": (it.get("shipping") or {}).get("mode"), "attrs": attrs,
            "sku": sku_de(it)}


resultado = []
for i, (iid, estado, antes, nuevo, titulo) in enumerate(pendientes):
    if dry_run:
        resultado.append(["SIMULADO", iid, estado, antes, nuevo, titulo])
        continue
    previo = foto(iid) if i == 0 else None
    cuerpo = {"attributes": [{"id": "SELLER_SKU", "value_name": nuevo}]}
    r = ml.put(f"/items/{iid}", cuerpo)
    if r.status_code != 200:
        resultado.append(["ERROR", iid, estado, antes, f"{r.status_code}: {r.text[:300]}", titulo])
        if i == 0:
            log(f"La primera publicación falló, me detengo: {r.status_code} {r.text}")
            break
        continue
    if i == 0:
        post = foto(iid)
        cambios = [k for k in previo if k != "sku" and previo[k] != post[k]]
        if post["sku"] != nuevo or cambios:
            log(f"PRUEBA FALLIDA en {iid}: sku ahora {post['sku']!r}, cambió además {cambios}. "
                "Me detengo sin tocar más publicaciones.")
            resultado.append(["DETENIDO", iid, estado, antes, post["sku"], titulo])
            break
        log(f"Prueba OK en {iid}: solo cambió el SKU ({antes} -> {nuevo})")
    resultado.append(["OK", iid, estado, antes, nuevo, titulo])

os.makedirs("salida", exist_ok=True)
with open("salida/ml-sku-fema-resultado.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["resultado", "item_id", "estado", "sku_antes", "sku_ahora", "titulo"])
    w.writerows(resultado)
    w.writerows([["OMITIDA (no está en BaseLinker)", *x[:3], "", x[4]] for x in omitidas])
ok = sum(r[0] == "OK" for r in resultado)
mal = [r for r in resultado if r[0] in ("ERROR", "DETENIDO")]
log(f"Listo. OK: {ok} | Simuladas: {sum(r[0] == 'SIMULADO' for r in resultado)} | "
    f"Errores: {len(mal)} | Omitidas: {len(omitidas)}")
for r in mal[:20]:
    print("   ", r)
sys.exit(1 if mal else 0)
