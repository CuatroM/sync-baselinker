"""
Elimina (cierra y borra) las publicaciones que ML dio de baja por "Era igual a otra de tus publicaciones",
más las 2 viejas prohibidas ya republicadas que no se pudieron borrar. Pedido y confirmado por el usuario.
Lista: datos/eliminar_duplicadas.json [{item, sku, vivas:[otra publicación del mismo SKU]}]

Antes de borrar cada una se vuelve a comprobar en ML, en el momento:
  - que el SKU coincida, que siga prohibida (sub_status forbidden) y que no tenga ventas;
  - que al menos una de las "vivas" del mismo SKU exista y NO esté prohibida, cerrada ni borrada.
Si algo no se cumple, no se toca. DRY_RUN=1 solo informa.
"""
import csv
import json
import os
import re
import time
from collections import Counter

from ml.cliente import ML, log, sku_de

dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
lista = json.load(open("datos/eliminar_duplicadas.json", encoding="utf-8"))
ml = ML(os.environ.get("ML_CODIGO"))
log(f"{len(lista)} publicaciones | {'SIMULACIÓN' if dry else 'REAL'}")


def base(s):
    return (s or "").upper().replace("#FEMA", "").strip()


filas = []
for n, x in enumerate(lista, 1):
    f = {"sku": x["sku"], "publicacion": x["item"], "titulo": x["titulo"], "viva": "", "estado_viva": "", "resultado": ""}
    try:
        it = ml.get(f"/items/{x['item']}")
        if base(sku_de(it)) != base(x["sku"]):
            raise RuntimeError(f"el SKU en ML es {sku_de(it)!r}")
        if "forbidden" not in (it.get("sub_status") or []):
            raise RuntimeError(f"ya no está prohibida ({it.get('status')} {it.get('sub_status')})")
        if (it.get("sold_quantity") or 0) > 0:
            raise RuntimeError("tiene ventas")
        viva = None
        for v in x["vivas"]:
            iv = ml.get(f"/items/{v}")
            if base(sku_de(iv)) == base(x["sku"]) and iv.get("status") in ("active", "paused", "under_review") \
                    and "forbidden" not in (iv.get("sub_status") or []) and not iv.get("deleted"):
                viva = iv
                break
        if not viva:
            raise RuntimeError("no hay otra publicación viva del mismo SKU")
        f["viva"], f["estado_viva"] = viva["id"], viva.get("status")
        if dry:
            f["resultado"] = "SE ELIMINARÍA"
        else:
            ok, r = False, None
            for intento in range(3):
                if it.get("status") != "closed":
                    ml.put(f"/items/{x['item']}", {"status": "closed"})
                    time.sleep(1)
                r = ml.put(f"/items/{x['item']}", {"deleted": "true"})
                if r.status_code in (200, 201):
                    ok = True
                    break
                time.sleep(5 * (intento + 1))
            f["resultado"] = "ELIMINADA" if ok else f"NO SE PUDO ELIMINAR: {r.status_code} {r.text[:150]}"
    except Exception as e:
        f["resultado"] = f"NO SE TOCA: {e}"[:300]
    if n % 50 == 0 or not f["resultado"].startswith(("SE ELIMINARÍA", "ELIMINADA")):
        log(f"{n}/{len(lista)} {x['sku']} {x['item']}: {f['resultado']}")
    filas.append(f)

os.makedirs("salida", exist_ok=True)
with open("salida/eliminar_duplicadas.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Resumen: " + str(Counter(re.split(r"[:(]", f["resultado"])[0].strip() for f in filas)))
