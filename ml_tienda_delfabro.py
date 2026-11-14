"""
Lee (solo lectura) las publicaciones de la tienda oficial de Delfabro en Mercado Libre para completar datos técnicos
de nuestras fichas: atributos, SKU/código del vendedor y descripción. No modifica nada.
Salida: salida/delfabro_ml.json y salida/delfabro_ml.csv
"""
import csv
import json
import os
import re
import time
from collections import Counter

from ml.cliente import ML, log

ml = ML(os.environ.get("ML_CODIGO"))


def buscar(**params):
    r = ml.pedir("GET", "/sites/MLA/search", params={**params, "limit": 50})
    if r.status_code != 200:
        log(f"  búsqueda {params} -> {r.status_code} {r.text[:200]}")
        return {}
    return r.json()


# 1) identificar al vendedor de la tienda oficial "delfabro"
votos, info = Counter(), {}
for q in ("delfabro", "delfabro crique", "delfabro prensa hidraulica", "dario delfabro"):
    for off in (0, 50):
        for x in buscar(q=q, offset=off).get("results") or []:
            s = (x.get("seller") or {}).get("id")
            if s and "delfabro" in (x.get("title") or "").lower():
                votos[s] += 2 if x.get("official_store_id") else 1
                info.setdefault(s, {"nick": (x.get("seller") or {}).get("nickname"), "tienda": x.get("official_store_name"),
                                    "store_id": x.get("official_store_id")})
log(f"Vendedores candidatos: {[(s, n, info[s]) for s, n in votos.most_common(6)]}")
seller = int(os.environ.get("SELLER_ID") or (votos.most_common(1)[0][0] if votos else 0))
if not seller:
    raise SystemExit("No encontré la tienda de Delfabro")
log(f"Vendedor elegido: {seller} {info.get(seller)}")

# 2) todas sus publicaciones
ids, off = [], 0
while off < 1000:
    d = buscar(seller_id=seller, offset=off)
    res = d.get("results") or []
    ids += [x["id"] for x in res]
    tot = (d.get("paging") or {}).get("total", 0)
    off += 50
    if not res or off >= tot:
        break
ids = list(dict.fromkeys(ids))
log(f"{len(ids)} publicaciones del vendedor (total informado {tot})")

# 3) detalle: atributos y descripción
salida = []
for i in range(0, len(ids), 20):
    r = ml.pedir("GET", "/items", params={"ids": ",".join(ids[i:i + 20])})
    for b in (r.json() if r.status_code == 200 else []):
        it = b.get("body") or {}
        if b.get("code") != 200:
            continue
        attrs = {a.get("name"): a.get("value_name") for a in it.get("attributes") or [] if a.get("value_name")}
        sku = attrs.get("SKU") or next((a.get("value_name") for a in it.get("attributes") or [] if a.get("id") == "SELLER_SKU"), "")
        desc = ""
        rd = ml.pedir("GET", f"/items/{it['id']}/description")
        if rd.status_code == 200:
            desc = (rd.json() or {}).get("plain_text") or ""
        salida.append({"id": it["id"], "titulo": it.get("title"), "precio": it.get("price"), "estado": it.get("status"),
                       "sku": sku or "", "categoria": it.get("category_id"), "atributos": attrs, "descripcion": desc[:5000],
                       "link": it.get("permalink")})
        time.sleep(0.15)
    log(f"  {len(salida)} leídas")
os.makedirs("salida", exist_ok=True)
json.dump(salida, open("salida/delfabro_ml.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
with open("salida/delfabro_ml.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["ID", "SKU", "TITULO", "PRECIO", "ESTADO", "LINK", "CANT ATRIBUTOS"])
    for s in salida:
        w.writerow([s["id"], s["sku"], s["titulo"], s["precio"], s["estado"], s["link"], len(s["atributos"])])
log(f"Listo: {len(salida)} publicaciones con {sum(1 for s in salida if s['sku'])} SKU informados")
