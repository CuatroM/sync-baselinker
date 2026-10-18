"""
Solo lectura: diagnóstico de cuotas de todas las publicaciones activas y pausadas.

En ML Argentina cada publicación tiene UN solo tipo de cuotas:
  - Clásica (gold_special): cuotas con bajo interés (tag pcj-co-funded) si el vendedor está adherido.
  - Premium (gold_pro): 3 (3x_campaign), 6 (sin tag), 9 (9x_campaign) o 12 (12x_campaign) cuotas sin interés.
Por eso, para ofrecer más de una opción, un mismo producto tiene que estar en Clásica y en Premium
(ML permite una publicación por tipo; dos del mismo tipo son duplicadas).

Por cada SKU informa: qué publicaciones tiene (Clásica / Premium), qué cuotas tiene cada una,
si su categoría admite 3x y 12x, y la comisión total de cada opción a su precio.
Salida: salida/cuotas_diagnostico.csv
"""
import csv
import os
import statistics as st
from collections import Counter, defaultdict

from ml.cliente import ML, log, sku_de

ml = ML(os.environ.get("ML_CODIGO"))
TAGS = ("pcj-co-funded", "3x_campaign", "9x_campaign", "12x_campaign")

# 1) adhesión del vendedor a cuotas con bajo interés (Clásica)
r = ml.pedir("GET", f"/special_installments/pcj-co-funded/sellers/{ml.user_id}")
log(f"Adhesión a pcj-co-funded (Clásica, cuotas con bajo interés): {r.status_code} {r.text[:200]}")

# 2) publicaciones
ids = []
for e in ("active", "paused"):
    lote = ml.ids_publicaciones(status=e)
    log(f"{e}: {len(lote)}")
    ids += lote
items = list(ml.publicaciones(ids, atributos="id,title,status,sub_status,listing_type_id,tags,category_id,price,"
                                             "seller_custom_field,attributes,domain_id,catalog_listing"))
log(f"Detalle de {len(items)}")


def base(s):
    return (s or "").upper().replace("#FEMA", "").strip()


def cuotas(it):
    t = set(it.get("tags") or [])
    if it.get("listing_type_id") == "gold_special":
        return "bajo interés (3 a 12)" if "pcj-co-funded" in t else "con interés"
    for tag, txt in (("3x_campaign", "3 sin interés"), ("9x_campaign", "9 sin interés"), ("12x_campaign", "12 sin interés")):
        if tag in t:
            return txt
    return "6 sin interés" if it.get("listing_type_id") == "gold_pro" else it.get("listing_type_id")


# 3) campañas disponibles y categorías habilitadas
cats = sorted({it.get("category_id") for it in items if it.get("category_id")})
log(f"{len(cats)} categorías")
habil = {}
for i, c in enumerate(cats):
    if i < 3:
        rc = ml.pedir("GET", "/special_installments/campaigns",
                      params={"category_id": c, "listing_type_id": "gold_pro", "channel": "marketplace"})
        log(f"  campañas {c}: {rc.status_code} {rc.text[:300]}")
    for tag in ("3x_campaign", "12x_campaign", "pcj-co-funded"):
        rr = ml.pedir("POST", f"/special_installments/{tag}/categories/{c}/enabled")
        habil[(c, tag)] = (rr.json().get("enabled") if rr.status_code == 200 else f"? {rr.status_code}")
        if i < 2:
            log(f"  {tag} en {c}: {rr.status_code} {rr.text[:150]}")

# 4) comisiones por categoría (a la mediana de precio de la categoría)
precios = defaultdict(list)
dominio = {}
for it in items:
    precios[it.get("category_id")].append(float(it.get("price") or 0))
    dominio.setdefault(it.get("category_id"), it.get("domain_id"))
comision = {}
for c in cats:
    p = st.median(precios[c])
    for clave, lt, tag in (("clasica", "gold_special", None), ("clasica_bajo_interes", "gold_special", "pcj-co-funded"),
                           ("premium_6", "gold_pro", None), ("premium_3", "gold_pro", "3x_campaign"),
                           ("premium_12", "gold_pro", "12x_campaign")):
        params = {"price": p, "listing_type_id": lt, "category_id": c}
        if tag:
            params["tags"] = tag
        rp = ml.pedir("GET", "/sites/MLA/listing_prices", params=params)
        v = ""
        if rp.status_code == 200:
            j = rp.json()
            j = j[0] if isinstance(j, list) and j else j
            det = (j or {}).get("sale_fee_details") or {}
            v = det.get("percentage_fee") or ""
            if not v and j.get("sale_fee_amount") and p:
                v = round(100 * float(j["sale_fee_amount"]) / p, 1)
        comision[(c, clave)] = v
    if c == cats[0]:
        log(f"  comisiones {c}: " + str({k: comision[(c, k)] for k in ("clasica", "clasica_bajo_interes", "premium_6", "premium_3", "premium_12")}))

# 5) por SKU
por = defaultdict(list)
for it in items:
    por[base(sku_de(it))].append(it)
filas = []
for sku, grupo in por.items():
    cl = [g for g in grupo if g.get("listing_type_id") == "gold_special"]
    pr = [g for g in grupo if g.get("listing_type_id") == "gold_pro"]
    ref = (cl or pr or grupo)[0]
    c = ref.get("category_id")
    tiene = Counter(cuotas(g) for g in grupo)
    falta = []
    if not pr:
        falta.append("no tiene Premium (sin cuotas sin interés)")
    if len(cl) > 1 or len(pr) > 1:
        falta.append("ATENCIÓN: más de una publicación del mismo tipo (riesgo de duplicada)")
    filas.append({
        "sku": sku, "titulo": ref.get("title"), "precio": ref.get("price"), "categoria": c,
        "clasica": ",".join(f"{g['id']} ({g.get('status')})" for g in cl),
        "cuotas_clasica": ",".join(cuotas(g) for g in cl),
        "premium": ",".join(f"{g['id']} ({g.get('status')})" for g in pr),
        "cuotas_premium": ",".join(cuotas(g) for g in pr),
        "cat_admite_3x": habil.get((c, "3x_campaign")), "cat_admite_12x": habil.get((c, "12x_campaign")),
        "cat_admite_bajo_interes": habil.get((c, "pcj-co-funded")),
        "comision_clasica_%": comision.get((c, "clasica")), "comision_clasica_bajo_interes_%": comision.get((c, "clasica_bajo_interes")),
        "comision_premium_6_%": comision.get((c, "premium_6")), "comision_premium_3_%": comision.get((c, "premium_3")),
        "comision_premium_12_%": comision.get((c, "premium_12")),
        "observacion": " | ".join(falta)})
os.makedirs("salida", exist_ok=True)
with open("salida/cuotas_diagnostico.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
log(f"SKUs: {len(filas)} | cuotas actuales por publicación: {Counter(cuotas(i) for i in items).most_common()}")
log("Combinaciones: " + str(Counter((bool(f['clasica']), bool(f['premium'])) for f in filas)))
log("Categoría admite 3x: " + str(Counter(str(f['cat_admite_3x']) for f in filas)) +
    " | 12x: " + str(Counter(str(f['cat_admite_12x']) for f in filas)))
