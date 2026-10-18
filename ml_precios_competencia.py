"""
Solo lectura: compara precio y envío de las publicaciones más importantes contra la competencia en ML.
Lista: datos/precios_lista.json [{item, sku, motivo}] (más vistas, con ventas y las de mayor precio).

Por cada publicación:
  1. Datos propios: precio, envío gratis, Full, tipo de publicación, producto de catálogo.
  2. Referencia de precio de ML (precio sugerido / comparación), si la API la da para ese ítem.
  3. Competencia: si es producto de catálogo, las otras ofertas del mismo producto; si no, la búsqueda
     del título en su categoría (y sin categoría si hay pocos resultados). Excluye nuestras publicaciones.
  4. Costo del envío gratis para nosotros (lo que cobraría ML si lo activamos).
No modifica nada. Salida: salida/precios_competencia.csv
"""
import csv
import os
import re
import statistics as st
from collections import Counter

import json

from ml.cliente import ML, log, sku_de

ml = ML(os.environ.get("ML_CODIGO"))
lista = json.load(open("datos/precios_lista.json", encoding="utf-8"))
log(f"{len(lista)} publicaciones a comparar")

VACIAS = {"de", "para", "con", "y", "a", "el", "la", "los", "las", "en", "x", "por", "fema", "color", "nuevo"}


def consulta(titulo, sku):
    t = re.sub(re.escape(sku), " ", titulo, flags=re.I)
    t = re.sub(r"\b[A-Z]{0,4}-?\d{5,}[A-Z0-9-]*\b", " ", t, flags=re.I)   # códigos internos
    pal = [w for w in re.findall(r"[\wáéíóúñü.,/]+", t) if w.lower() not in VACIAS]
    return " ".join(pal[:7])


def resultados(params):
    r = ml.pedir("GET", "/sites/MLA/search", params={**params, "limit": 50})
    if r.status_code != 200:
        return None, f"búsqueda {r.status_code}"
    return r.json().get("results") or [], ""


filas = []
for n, x in enumerate(lista, 1):
    f = {"sku": x["sku"], "publicacion": x["item"], "motivo": x["motivo"], "titulo": "", "estado": "", "precio": "",
         "envio_gratis": "", "full": "", "tipo": "", "catalogo": "", "ml_precio_sugerido": "", "ml_comparacion": "",
         "fuente": "", "busqueda": "", "competidores": 0, "comp_min": "", "comp_mediana": "", "comp_max": "",
         "dif_vs_mediana_%": "", "mas_baratos_que_yo": "", "comp_envio_gratis_%": "", "comp_full_%": "",
         "mi_posicion_busqueda": "", "costo_envio_gratis": "", "ejemplos": "", "nota": ""}
    try:
        it = ml.get(f"/items/{x['item']}")
        sh = it.get("shipping") or {}
        f.update(titulo=it["title"], estado=it.get("status"), precio=it.get("price"),
                 envio_gratis="si" if sh.get("free_shipping") else "no",
                 full="si" if sh.get("logistic_type") == "fulfillment" else "no",
                 tipo=it.get("listing_type_id"), catalogo=it.get("catalog_product_id") or "")
        precio = float(it.get("price") or 0)

        # 2) referencia de precio de ML
        rs = ml.pedir("GET", f"/suggestions/items/{x['item']}/details")
        if rs.status_code == 200:
            j = rs.json()
            sug = (j.get("suggested_price") or {}).get("amount") if isinstance(j.get("suggested_price"), dict) \
                else j.get("suggested_price")
            f["ml_precio_sugerido"] = sug or ""
            f["ml_comparacion"] = str({k: j.get(k) for k in ("status", "percent_difference", "applicable_suggestion")
                                       if j.get(k) is not None})[:150]
        elif n <= 3:
            log(f"  referencia de precio {rs.status_code}: {rs.text[:150]}")

        # 3) competencia
        comp, fuente = [], ""
        if it.get("catalog_product_id"):
            rp = ml.pedir("GET", f"/products/{it['catalog_product_id']}/items")
            if rp.status_code == 200:
                comp = [c for c in rp.json().get("results") or [] if c.get("seller_id") != ml.user_id]
                fuente = "mismo producto de catálogo"
        mios = []
        if not comp:
            q = consulta(it["title"], x["sku"])
            f["busqueda"] = q
            res, err = resultados({"q": q, "category": it.get("category_id")})
            if res is not None and len([c for c in res if c.get("seller", {}).get("id") != ml.user_id]) < 5:
                res2, _ = resultados({"q": q})
                res = res2 if res2 else res
                fuente = "búsqueda del título (sin categoría)"
            else:
                fuente = "búsqueda del título en su categoría"
            if res is None:
                raise RuntimeError(err)
            mios = [i for i, c in enumerate(res, 1) if (c.get("seller") or {}).get("id") == ml.user_id]
            comp = [c for c in res if (c.get("seller") or {}).get("id") != ml.user_id]
            f["mi_posicion_busqueda"] = mios[0] if mios else "no aparece en los 50 primeros"
        f["fuente"] = fuente
        precios = sorted(float(c["price"]) for c in comp if c.get("price"))
        f["competidores"] = len(precios)
        if precios:
            med = st.median(precios)
            f.update(comp_min=precios[0], comp_mediana=round(med, 2), comp_max=precios[-1],
                     mas_baratos_que_yo=sum(1 for p in precios if p < precio))
            if precio and med:
                f["dif_vs_mediana_%"] = round((precio / med - 1) * 100, 1)
            eg = [c for c in comp if (c.get("shipping") or {}).get("free_shipping")]
            fu = [c for c in comp if (c.get("shipping") or {}).get("logistic_type") == "fulfillment"]
            f["comp_envio_gratis_%"] = round(100 * len(eg) / len(comp))
            f["comp_full_%"] = round(100 * len(fu) / len(comp))
            f["ejemplos"] = " | ".join(f"{(c.get('title') or '')[:45]} ${c.get('price')} {c.get('permalink') or ''}"
                                       for c in sorted(comp, key=lambda c: float(c.get('price') or 0))[:3])[:900]

        # 4) costo del envío gratis
        rc = ml.pedir("GET", f"/users/{ml.user_id}/shipping_options/free", params={"item_id": x["item"]})
        if rc.status_code == 200:
            cov = (rc.json().get("coverage") or {}).get("all_country") or {}
            f["costo_envio_gratis"] = cov.get("list_cost") or ""
        elif n <= 3:
            log(f"  costo envío {rc.status_code}: {rc.text[:150]}")
    except Exception as e:
        f["nota"] = f"ERROR: {e}"[:300]
    log(f"{n}/{len(lista)} {x['sku']} ${f['precio']} vs mediana {f['comp_mediana']} ({f['dif_vs_mediana_%']}%) "
        f"[{f['competidores']} comp, {f['fuente']}] pos {f['mi_posicion_busqueda']} {f['nota']}")
    filas.append(f)

os.makedirs("salida", exist_ok=True)
with open("salida/precios_competencia.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
d = [f["dif_vs_mediana_%"] for f in filas if f["dif_vs_mediana_%"] != ""]
log(f"Con comparación: {len(d)} | más caras que la mediana: {sum(1 for v in d if v > 0)} | "
    f"más de 30% arriba: {sum(1 for v in d if v > 30)}")
log("Fuentes: " + str(Counter(f["fuente"] for f in filas)))
