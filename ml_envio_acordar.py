"""
Pasa publicaciones de Mercado Envíos (me2) a envío "a acordar con el vendedor" (not_specified), para que después
la planilla les cargue los costos de Credifin / Vía Cargo / Cruz del Sur.
LISTA: archivo JSON en datos/ con los MLA (lista o diccionario SKU -> MLA). Por defecto datos/delfabro_publicadas.json.
DRY_RUN=1 (por defecto): solo informa el tipo de envío actual de cada una y qué haría.
"""
import csv
import json
import os
import time
from collections import Counter

from ml.cliente import ML, log

dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
lista = (os.environ.get("LISTA") or "datos/delfabro_publicadas.json").strip()
d = json.load(open(lista, encoding="utf-8"))
ids = list(d.values()) if isinstance(d, dict) else list(d)
sku = {v: k for k, v in d.items()} if isinstance(d, dict) else {}
ml = ML(os.environ.get("ML_CODIGO"))
log(f"{len(ids)} publicaciones de {lista} | {'SIMULACIÓN' if dry else 'REAL'}")
res, cuenta = [], Counter()
for k, iid in enumerate(ids, 1):
    it = {}
    for intento in range(4):
        try:
            it = ml.get(f"/items/{iid}")
            break
        except Exception as e:
            log(f"  no se pudo leer {iid} ({e}); reintento en 20 s")
            time.sleep(20)
    if not it:
        res.append({"sku": sku.get(iid, ""), "mla": iid, "estado_pub": "", "envio_antes": "", "envio_gratis": "", "precio": "",
                    "resultado": "no se pudo leer"})
        cuenta["no se pudo leer"] += 1
        continue
    sh = it.get("shipping") or {}
    modo = sh.get("mode")
    fila = {"sku": sku.get(iid, ""), "mla": iid, "estado_pub": it.get("status"), "envio_antes": modo,
            "envio_gratis": sh.get("free_shipping"), "precio": it.get("price"), "resultado": ""}
    if modo == "me2":
        if dry:
            fila["resultado"] = "se pasaría a acordar"
        else:
            r = ml.put(f"/items/{iid}", {"shipping": {"mode": "not_specified", "local_pick_up": bool(sh.get("local_pick_up")),
                                                      "free_shipping": False}})
            if r.status_code in (200, 201):
                nuevo = ((r.json() or {}).get("shipping") or {}).get("mode")
                fila["resultado"] = "pasada a acordar" if nuevo == "not_specified" else f"ML la dejó en {nuevo}"
            else:
                fila["resultado"] = f"ERROR {r.status_code} {r.text[:200]}"
            time.sleep(0.3)
    else:
        fila["resultado"] = "ya está a acordar" if modo in ("not_specified", "custom") else f"sin cambio ({modo})"
    cuenta[fila["resultado"].split(" {")[0][:40]] += 1
    res.append(fila)
    log(f"[{k}/{len(ids)}] {fila['sku']} {iid}: {modo} -> {fila['resultado']}")
os.makedirs("salida", exist_ok=True)
with open("salida/envio_acordar.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(res[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(res)
log(f"Resumen: {dict(cuenta)}")
