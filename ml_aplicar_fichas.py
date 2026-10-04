"""
Aplica en Mercado Libre la propuesta de datos/propuesta_fichas.json:
  - atributos de la ficha técnica (completar vacíos y corregir según catálogo FEMA)
  - descripción con el formato acordado

Seguridad: en la primera publicación compara antes/después; si cambió algo que no estaba
previsto (título, precio, stock, estado u otros atributos) se detiene sin tocar más.
DRY_RUN=1 solo informa. LIMITE=N procesa N publicaciones. SKUS=a,b filtra por SKU.
"""
import csv
import json
import os
import sys

from ml.cliente import ML, log
from ml.fichas_map import mismo

dry = os.environ.get("DRY_RUN", "1") == "1"
limite = int(os.environ.get("LIMITE") or 0)
skus = {s.strip() for s in (os.environ.get("SKUS") or "").split(",") if s.strip()}
hacer_desc = os.environ.get("DESCRIPCION", "1") == "1"
hacer_attr = os.environ.get("ATRIBUTOS", "1") == "1"

prop = json.load(open("datos/propuesta_fichas.json", encoding="utf-8"))
if skus:
    prop = [p for p in prop if p["sku"] in skus]
if limite:
    prop = prop[:limite]
log(f"Publicaciones a procesar: {len(prop)} | atributos: {hacer_attr} | descripción: {hacer_desc} | simulación: {dry}")

ml = ML(os.environ.get("ML_CODIGO"))


def foto(iid):
    it = ml.get(f"/items/{iid}")
    return {"title": it.get("title"), "price": it.get("price"), "qty": it.get("available_quantity"),
            "status": it.get("status"), "attrs": {a["id"]: a.get("value_name") for a in it.get("attributes") or []}}


res = []
for i, p in enumerate(prop):
    iid, cambios = p["item_id"], (p["atributos"] if hacer_attr else [])
    if dry:
        for a in cambios:
            res.append([p["sku"], iid, "atributo", a["id"], a.get("antes") or "", a["value_name"], "SIMULADO"])
        if hacer_desc:
            res.append([p["sku"], iid, "descripción", "", "", f"{len(p['descripcion'])} caracteres", "SIMULADO"])
        continue
    previo = foto(iid) if i == 0 else None
    if cambios:
        r = ml.put(f"/items/{iid}", {"attributes": [{"id": a["id"], "value_name": a["value_name"]} for a in cambios]})
        if r.status_code == 200:
            res += [[p["sku"], iid, "atributo", a["id"], a.get("antes") or "", a["value_name"], "OK"] for a in cambios]
        else:
            # reintento atributo por atributo para salvar los que sí se aceptan
            for a in cambios:
                r1 = ml.put(f"/items/{iid}", {"attributes": [{"id": a["id"], "value_name": a["value_name"]}]})
                estado = "OK" if r1.status_code == 200 else f"ERROR {r1.status_code}: {r1.text[:250]}"
                res.append([p["sku"], iid, "atributo", a["id"], a.get("antes") or "", a["value_name"], estado])
    if hacer_desc:
        r = ml.pedir("PUT", f"/items/{iid}/description", params={"api_version": 2},
                     json={"plain_text": p["descripcion"]})
        if r.status_code == 404:
            r = ml.pedir("POST", f"/items/{iid}/description", json={"plain_text": p["descripcion"]})
        estado = "OK" if r.status_code in (200, 201) else f"ERROR {r.status_code}: {r.text[:250]}"
        res.append([p["sku"], iid, "descripción", "", "", f"{len(p['descripcion'])} caracteres", estado])
    if i == 0:
        post = foto(iid)
        previstos = {a["id"] for a in cambios}
        raros = [k for k in ("title", "price", "qty", "status") if previo[k] != post[k]]
        raros += [k for k in set(previo["attrs"]) | set(post["attrs"])
                  if k not in previstos and k != "SELLER_SKU" and previo["attrs"].get(k) != post["attrs"].get(k)]
        if raros:
            log(f"PRUEBA FALLIDA en {iid}: cambió además {raros}. Me detengo.")
            res.append([p["sku"], iid, "prueba", ",".join(raros), "", "", "DETENIDO"])
            break
        aplicados = [a for a in cambios if mismo(post["attrs"].get(a["id"]), a["value_name"])]
        log(f"Prueba OK en {iid}: no cambió nada fuera de lo previsto "
            f"({len(aplicados)}/{len(cambios)} atributos verificados)")
    if (i + 1) % 25 == 0:
        log(f"  procesadas {i + 1}/{len(prop)}")

os.makedirs("salida", exist_ok=True)
with open("salida/ml-fichas-resultado.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["sku", "publicacion", "tipo", "atributo", "antes", "ahora", "resultado"])
    w.writerows(res)
ok = sum(1 for r in res if r[6] == "OK")
err = [r for r in res if r[6].startswith("ERROR") or r[6] == "DETENIDO"]
log(f"Listo. OK: {ok} | Simulados: {sum(1 for r in res if r[6] == 'SIMULADO')} | Errores: {len(err)}")
for r in err[:25]:
    print("   ", r)
sys.exit(1 if any(r[6] == "DETENIDO" for r in err) else 0)
