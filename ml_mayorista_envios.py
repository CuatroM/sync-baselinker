"""
Precio mayorista y costos de envío para "acordar la entrega" en Mercado Libre (pedido del usuario).

1) Precio por cantidad (solo compradores empresa; la cuenta tiene que estar habilitada por ML, tag "business"):
   desde 2 unidades = precio publicado − 2 %.
2) Envío personalizado SOLO en las publicaciones que hoy tienen envío "a acordar" (not_specified) o ya personalizado:
   - Correo Credifin a domicilio/sucursal: 7 % del costo en BaseLinker, mínimo $20.000
   - Vía Cargo a sucursal: 20 % del costo en BaseLinker, mínimo $40.000
   - Cruz del Sur R. Negro/Patagonia: 12 % del costo en BaseLinker, mínimo $30.000
   Costo = precio del grupo de precios de BaseLinker de cada proveedor (FEMA, EXTRAPOWER, J&A), según el sufijo del SKU.
QUE: "ambos" | "mayorista" | "envios". DRY_RUN=1 (por defecto): solo informa qué haría. LIMITE para probar con pocas.
"""
import csv
import os
import time
from collections import Counter

import yaml

import sync
from ml.cliente import ML, log, sku_de

dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
que = (os.environ.get("QUE") or "ambos").strip().lower()
limite = int(os.environ.get("LIMITE") or 0)
DESC_MAYORISTA, MIN_UNIDADES = 0.02, 2
ENVIOS = [("Correo Credifin a domicilio/sucursal", 0.07, 20000),
          ("Vía Cargo a sucursal", 0.20, 40000),
          ("Cruz del Sur R. Negro/Patagonia", 0.12, 30000)]

ml = ML(os.environ.get("ML_CODIGO"))
yo = ml.get(f"/users/{ml.user_id}")
business = "business" in (yo.get("tags") or [])
log(f"Cuenta {ml.user_id} | tags: {yo.get('tags')} | habilitada para precio por cantidad: {'SÍ' if business else 'NO'}")
log("SIMULACIÓN" if dry else "REAL")

# costos de BaseLinker por SKU (con sufijo)
cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
bl = sync.BaseLinker(os.environ["BL_TOKEN"])
costo = {}
for sec in ("baselinker", "extrapower", "jya"):
    b = cfg[sec]
    for p in bl.catalogo(int(b["inventory_id"])).values():
        v = float((p.get("prices") or {}).get(str(b["price_group_id"])) or 0)
        if v > 0:
            costo[sync.normalizar_codigo(p.get("sku"))] = v
log(f"Costos de BaseLinker: {len(costo)} SKU")
suf = {"baselinker": "#FEMA", "extrapower": "#EXTRAPOWER", "jya": "#JYA"}


def costo_de(sku):
    s = sync.normalizar_codigo(sku)
    if s in costo:
        return costo[s]
    base = s.split("#")[0]
    for x in suf.values():
        if base + x in costo:
            return costo[base + x]
    return costo.get(base)


ids = []
for est in ("active", "paused"):
    ids += ml.ids_publicaciones(status=est)
ids = list(dict.fromkeys(ids))
if limite:
    ids = ids[:limite]
log(f"Publicaciones activas y pausadas: {len(ids)}")

filas = []
for it in ml.publicaciones(ids, "id,title,status,price,currency_id,shipping,seller_custom_field,attributes,tags,catalog_listing"):
    sku = sku_de(it)
    modo = (it.get("shipping") or {}).get("mode")
    f = {"publicacion": it["id"], "sku": sku, "titulo": it.get("title"), "estado": it.get("status"), "precio": it.get("price"),
         "envio_actual": modo, "costo_base": "", "mayorista_2u": "", "credifin": "", "via_cargo": "", "cruz_del_sur": "",
         "resultado_mayorista": "", "resultado_envios": ""}
    # 1) precio por cantidad
    if que in ("ambos", "mayorista"):
        pm = round(float(it["price"]) * (1 - DESC_MAYORISTA), 2)
        f["mayorista_2u"] = pm
        if not business:
            f["resultado_mayorista"] = "cuenta no habilitada por ML"
        elif "standard_price_by_quantity" in (it.get("tags") or []):
            f["resultado_mayorista"] = "ya tiene precio por cantidad"
        elif dry:
            f["resultado_mayorista"] = "SE CARGARÍA"
        else:
            r = ml.pedir("POST", f"/items/{it['id']}/prices/standard/quantity", json={"prices": [{
                "amount": pm, "currency_id": it.get("currency_id", "ARS"),
                "conditions": {"context_restrictions": ["channel_marketplace", "user_type_business"], "min_purchase_unit": MIN_UNIDADES}}]})
            f["resultado_mayorista"] = "CARGADO" if r.status_code in (200, 201) else f"ERROR {r.status_code} {r.text[:150]}"
            time.sleep(0.3)
    # 2) envío personalizado (solo las de "acordar la entrega")
    if que in ("ambos", "envios"):
        if modo not in ("not_specified", "custom"):
            f["resultado_envios"] = f"no aplica (envío {modo})"
        else:
            c = costo_de(sku)
            if not c:
                f["resultado_envios"] = "sin costo en BaseLinker"
            else:
                f["costo_base"] = c
                costos = [{"description": d, "cost": round(max(c * p, minimo), 2)} for d, p, minimo in ENVIOS]
                f["credifin"], f["via_cargo"], f["cruz_del_sur"] = (x["cost"] for x in costos)
                if dry:
                    f["resultado_envios"] = "SE CARGARÍA"
                else:
                    r = ml.put(f"/items/{it['id']}", {"shipping": {"mode": "custom", "local_pick_up": bool((it.get("shipping") or {}).get("local_pick_up")),
                                                                   "free_shipping": False, "methods": [], "costs": costos}})
                    f["resultado_envios"] = "CARGADO" if r.status_code in (200, 201) else f"ERROR {r.status_code} {r.text[:200]}"
                    time.sleep(0.3)
    filas.append(f)

os.makedirs("salida", exist_ok=True)
with open("salida/mayorista_envios.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()) if filas else ["publicacion"], delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Envío actual: " + str(Counter(f["envio_actual"] for f in filas)))
log("Mayorista: " + str(Counter(f["resultado_mayorista"].split(" ")[0] if f["resultado_mayorista"].startswith("ERROR") else f["resultado_mayorista"] for f in filas)))
log("Envíos: " + str(Counter(f["resultado_envios"][:40] for f in filas)))
for f in [x for x in filas if str(x["resultado_mayorista"]).startswith("ERROR") or str(x["resultado_envios"]).startswith("ERROR")][:10]:
    log(f"  {f['publicacion']} {f['sku']}: {f['resultado_mayorista']} | {f['resultado_envios']}")
