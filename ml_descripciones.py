"""
Reemplaza la descripción de TODAS las publicaciones (activas y pausadas) de los SKU indicados,
con el formato vigente de ml/fichas_map.descripcion(). No toca nada más.
"""
import csv
import json
import os
import sys

from ml.cliente import ML, log, sku_de
from ml import fichas_map as fm

SUFIJO = "#FEMA"
dry = os.environ.get("DRY_RUN", "1") == "1"
skus = [s.strip() for s in os.environ["SKUS"].split(",") if s.strip()]
F = {f["sku"]: f for f in json.load(open("datos/fichas_fema.json", encoding="utf-8"))}


def texto_ml(t):
    for a, b in (("≤", "hasta "), ("≥", "desde "), ("±", "+/-"), ("’", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-"), ("×", "x")):
        t = t.replace(a, b)
    return t


ml = ML(os.environ.get("ML_CODIGO"))
res = []
for sku in skus:
    if sku not in F:
        res.append([sku, "", "SIN FICHA"])
        continue
    texto = texto_ml(fm.descripcion(F[sku]))
    ids = []
    for est in ("active", "paused"):
        ids += ml.get(f"/users/{ml.user_id}/items/search", seller_sku=sku + SUFIJO, status=est).get("results") or []
    if not ids:
        res.append([sku, "", "SIN PUBLICACIÓN"])
    for iid in ids:
        if dry:
            res.append([sku, iid, "SIMULADO"])
            continue
        r = ml.pedir("PUT", f"/items/{iid}/description", params={"api_version": 2}, json={"plain_text": texto})
        if r.status_code == 404:
            r = ml.pedir("POST", f"/items/{iid}/description", json={"plain_text": texto})
        res.append([sku, iid, "OK" if r.status_code in (200, 201) else f"ERROR {r.status_code}: {r.text[:250]}"])
    log(f"{sku}: {len(ids)} publicaciones")
os.makedirs("salida", exist_ok=True)
with open("salida/ml-descripciones-resultado.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh, delimiter=";")
    w.writerow(["sku", "publicacion", "resultado"])
    w.writerows(res)
for r in res:
    print("   ", r)
ok = sum(1 for r in res if r[2] == "OK")
log(f"Listo. OK: {ok} | Total: {len(res)}")
sys.exit(1 if any(r[2].startswith("ERROR") for r in res) else 0)
