"""
Solo lectura: busca TODAS las publicaciones "en revisión" y lista las que ML marcó por categoría incorrecta
(u otros motivos), con la categoría que hoy tiene, la que sugiere ML para el título, ventas y stock.
Salida: salida/categoria_incorrecta.csv (todas las en revisión con su motivo) y salida/republicar_candidatas.json.
"""
import csv
import json
import os
from collections import Counter

from ml.cliente import ML, log, sku_de

ml = ML(os.environ.get("ML_CODIGO"))
ids = ml.ids_publicaciones(status="under_review")
log(f"Publicaciones en revisión: {len(ids)}")
cats = {}


def nombre_cat(c):
    if c and c not in cats:
        r = ml.pedir("GET", f"/categories/{c}")
        cats[c] = " > ".join(p["name"] for p in r.json().get("path_from_root", [])) if r.status_code == 200 else ""
    return cats.get(c, "")


filas, cand = [], []
for it in ml.publicaciones(ids, "id,title,status,sub_status,category_id,seller_custom_field,attributes,sold_quantity,available_quantity,price"):
    r = ml.pedir("GET", f"/moderations/infractions/{ml.user_id}", params={"related_item_id": it["id"]})
    mot = []
    if r.status_code == 200:
        d = r.json()
        lst = d.get("infractions") if isinstance(d, dict) else d
        mot = [str(i.get("reason")) for i in lst or [] if isinstance(i, dict) and i.get("reason")]
    motivo = " || ".join(dict.fromkeys(mot))
    es_cat = "categor" in motivo.lower()
    sug, sug_n = "", ""
    if es_cat:
        s = ml.pedir("GET", "/sites/MLA/domain_discovery/search", params={"q": it["title"], "limit": 1})
        if s.status_code == 200 and s.json():
            sug, sug_n = s.json()[0].get("category_id", ""), s.json()[0].get("category_name", "")
    sku = sku_de(it)
    f = {"publicacion": it["id"], "sku": sku, "marca_sku": sku.split("#")[-1] if "#" in sku else "",
         "titulo": it["title"], "estado": f"{it['status']}/{','.join(it.get('sub_status') or [])}",
         "categoria_incorrecta": "SI" if es_cat else "", "motivo": motivo[:300],
         "cat_actual": it.get("category_id"), "cat_actual_nombre": nombre_cat(it.get("category_id")),
         "cat_sugerida": sug, "cat_sugerida_nombre": nombre_cat(sug) if sug else sug_n,
         "ventas": it.get("sold_quantity") or 0, "stock": it.get("available_quantity") or 0, "precio": it.get("price")}
    filas.append(f)
    if es_cat:
        cand.append({"sku": sku, "item": it["id"], "titulo": it["title"], "cat_actual": it.get("category_id"),
                     "categoria": sug or it.get("category_id"), "cat_nombre": f["cat_sugerida_nombre"] or f["cat_actual_nombre"],
                     "vendidos": f["ventas"], "fotos": "viejas", "extras": []})
        log(f"{it['id']} {sku} | {it['title'][:60]} | {f['cat_actual_nombre'][-50:]} -> {f['cat_sugerida_nombre'][-50:]} | {motivo[:80]}")

os.makedirs("salida", exist_ok=True)
with open("salida/categoria_incorrecta.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()) if filas else ["publicacion"], delimiter=";")
    w.writeheader()
    w.writerows(filas)
json.dump(cand, open("salida/republicar_candidatas.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
log(f"Categoría incorrecta: {len(cand)} | por marca: {Counter(x['sku'].split('#')[-1] for x in cand)}")
log("Motivos (todas en revisión): " + str(Counter(f['motivo'].split(' || ')[0][:60] for f in filas).most_common(10)))
