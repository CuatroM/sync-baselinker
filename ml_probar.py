"""Prueba de conexión (solo lectura): cuenta, publicaciones, envíos y SKU sin '#FEMA'."""
import collections
import csv
import os
from ml.cliente import ML, log, sku_de

ml = ML(os.environ.get("ML_CODIGO"))
yo = ml.get("/users/me")
log(f"Cuenta: {yo.get('nickname')} (id {yo.get('id')}) | sitio {yo.get('site_id')}")

ids = ml.ids_publicaciones(status="active")
log(f"Publicaciones activas: {len(ids)}")
sin_sufijo, sin_sku, con_var, modos = [], 0, 0, collections.Counter()
for it in ml.publicaciones(ids):
    modos[(it.get("shipping") or {}).get("mode")] += 1
    if it.get("variations"):
        con_var += 1
    sku = sku_de(it)
    if not sku:
        sin_sku += 1
    elif not sku.upper().endswith("#FEMA"):
        sin_sufijo.append([it["id"], sku, it.get("title"),
                           (it.get("shipping") or {}).get("mode")])
log(f"Modo de envío: {dict(modos)}  (me2 = Mercado Envíos)")
log(f"Con variaciones: {con_var} | Sin SKU: {sin_sku} | SKU sin '#FEMA': {len(sin_sufijo)}")
os.makedirs("salida", exist_ok=True)
with open("salida/ml-sku-sin-fema.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["item_id", "sku", "titulo", "envio"])
    w.writerows(sin_sufijo)
for x in sin_sufijo[:20]:
    print("   ", x)
