"""
Pide a Mercado Libre que reactive publicaciones pausadas por moderación que ya se corrigieron
(por ejemplo, "La portada tiene logos y/o textos" cuando la portada ya se limpió). Pedido por el usuario.
LISTA: datos/reactivar_portada.json [{item, sku}]
No toca las que no tienen stock (seguirían pausadas igual) ni las que ya están activas.
DRY_RUN=1 solo informa el estado. Salida: salida/reactivar.csv con lo que respondió ML.
"""
import csv
import json
import os
import re
import time
from collections import Counter

from ml.cliente import ML, log

dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
lista = json.load(open(os.environ.get("LISTA") or "datos/reactivar_portada.json", encoding="utf-8"))
ml = ML(os.environ.get("ML_CODIGO"))
log(f"{len(lista)} publicaciones | {'SIMULACIÓN' if dry else 'REAL'}")


def motivo(item_id):
    r = ml.pedir("GET", f"/moderations/infractions/{ml.user_id}", params={"related_item_id": item_id})
    if r.status_code != 200:
        return ""
    d = r.json()
    lst = d.get("infractions") if isinstance(d, dict) else d
    return " || ".join(str(i.get("reason")) for i in lst or [] if isinstance(i, dict) and i.get("reason"))[:200]


filas = []
for n, x in enumerate(lista, 1):
    f = {"sku": x["sku"], "publicacion": x["item"], "estado_antes": "", "stock": "", "estado_despues": "",
         "motivo": "", "resultado": ""}
    try:
        it = ml.get(f"/items/{x['item']}", attributes="id,status,sub_status,available_quantity")
        f["estado_antes"] = f"{it.get('status')}/{','.join(it.get('sub_status') or [])}"
        f["stock"] = it.get("available_quantity") or 0
        if it.get("status") == "active":
            f["resultado"] = "ya estaba activa"
        elif not f["stock"]:
            f["resultado"] = "sin stock: sigue pausada"
        elif dry:
            f["resultado"] = "SE PEDIRÍA REACTIVAR"
        else:
            r = ml.put(f"/items/{x['item']}", {"status": "active"})
            time.sleep(2)
            d = ml.get(f"/items/{x['item']}", attributes="id,status,sub_status")
            f["estado_despues"] = f"{d.get('status')}/{','.join(d.get('sub_status') or [])}"
            if r.status_code in (200, 201) and d.get("status") == "active":
                f["resultado"] = "REACTIVADA"
            elif r.status_code in (200, 201):
                f["resultado"] = f"ML ACEPTÓ EL PEDIDO, QUEDÓ {f['estado_despues']}"
            else:
                f["resultado"] = f"RECHAZADA {r.status_code} {r.text[:200]}"
        if f["resultado"] not in ("REACTIVADA", "ya estaba activa"):
            f["motivo"] = motivo(x["item"])
    except Exception as e:
        f["resultado"] = f"ERROR: {e}"[:300]
    log(f"{n}/{len(lista)} {x['sku']} {x['item']}: {f['resultado']}")
    filas.append(f)

os.makedirs("salida", exist_ok=True)
with open("salida/reactivar.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Resumen: " + str(Counter(re.split(r"[:,]| \d", f["resultado"])[0].strip() for f in filas)))
