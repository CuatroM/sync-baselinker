"""
Auditoría (solo lectura) de la ficha técnica de todas las publicaciones #FEMA (activas y pausadas).
Para cada publicación compara los atributos cargados con los que admite su categoría.
Salida: salida/auditoria_fichas.csv (una fila por publicación) y salida/auditoria_resumen.csv.
"""
import csv
import os
from collections import Counter

from ml.cliente import ML, log, sku_de

SUFIJO = "#FEMA"
ml = ML(os.environ.get("ML_CODIGO"))
NO_CUENTAN = {"hidden", "read_only", "fixed", "others", "inferred", "restricted_values"}
IGNORAR = {"SELLER_SKU", "ITEM_CONDITION", "GTIN", "EMPTY_GTIN_REASON", "PRODUCT_FEATURES", "ADDITIONAL_INFO"}

ids = []
for estado in ("active", "paused"):
    lote = ml.ids_publicaciones(status=estado)
    log(f"{estado}: {len(lote)} publicaciones")
    ids += lote

_cats = {}


def atributos_cat(cat):
    if cat not in _cats:
        r = ml.pedir("GET", f"/categories/{cat}/attributes")
        lista = r.json() if r.status_code == 200 else []
        _cats[cat] = {}
        for a in lista:
            tags = a.get("tags") or {}
            if any(tags.get(t) for t in NO_CUENTAN) or a["id"] in IGNORAR:
                continue
            nivel = ("obligatorio" if tags.get("required") or tags.get("catalog_required") or tags.get("conditional_required")
                     else "recomendado" if (a.get("relevance") == 1) else "opcional")
            _cats[cat][a["id"]] = (a.get("name"), nivel)
        nombre = ml.pedir("GET", f"/categories/{cat}")
        _cats[cat]["__nombre"] = (nombre.json().get("name") if nombre.status_code == 200 else "", "")
    return _cats[cat]


filas, falta_oblig, falta_rec = [], Counter(), Counter()
for n, it in enumerate(ml.publicaciones(ids, atributos="id,title,status,category_id,attributes,seller_custom_field,permalink"), 1):
    sku = sku_de(it)
    if not sku.upper().endswith(SUFIJO):
        continue
    cat = it.get("category_id")
    ca = atributos_cat(cat)
    cargados = {a["id"] for a in it.get("attributes") or [] if (a.get("value_name") or a.get("value_id"))
                and str(a.get("value_id")) != "-1"}
    pos = {k: v for k, v in ca.items() if not k.startswith("__")}
    ob = [k for k, (_, nv) in pos.items() if nv == "obligatorio"]
    rec = [k for k, (_, nv) in pos.items() if nv == "recomendado"]
    f_ob = [pos[k][0] for k in ob if k not in cargados]
    f_rec = [pos[k][0] for k in rec if k not in cargados]
    for x in f_ob:
        falta_oblig[(ca["__nombre"][0], x)] += 1
    for x in f_rec:
        falta_rec[(ca["__nombre"][0], x)] += 1
    total = len(ob) + len(rec)
    hechos = total - len(f_ob) - len(f_rec)
    filas.append({
        "publicacion": it["id"], "sku": sku[: -len(SUFIJO)], "estado": it.get("status"), "titulo": it.get("title"),
        "categoria": f"{cat} {ca['__nombre'][0]}",
        "completitud_%": round(100 * hechos / total) if total else 100,
        "faltan_obligatorios": len(f_ob), "faltan_recomendados": len(f_rec),
        "obligatorios_sin_cargar": ", ".join(f_ob), "recomendados_sin_cargar": ", ".join(f_rec),
        "opcionales_sin_cargar": len([k for k, (_, nv) in pos.items() if nv == "opcional" and k not in cargados]),
        "link": it.get("permalink"),
    })
    if n % 500 == 0:
        log(f"  {n} revisadas")

os.makedirs("salida", exist_ok=True)
with open("salida/auditoria_fichas.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
with open("salida/auditoria_resumen.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["tipo", "categoria", "atributo", "publicaciones_sin_dato"])
    for (c, a), k in falta_oblig.most_common():
        w.writerow(["obligatorio", c, a, k])
    for (c, a), k in falta_rec.most_common():
        w.writerow(["recomendado", c, a, k])
completas = sum(1 for x in filas if x["faltan_obligatorios"] == 0 and x["faltan_recomendados"] == 0)
log(f"#FEMA: {len(filas)} | completas (obligatorios y recomendados): {completas} | "
    f"con obligatorios faltantes: {sum(1 for x in filas if x['faltan_obligatorios'])} | "
    f"con recomendados faltantes: {sum(1 for x in filas if x['faltan_recomendados'])}")
