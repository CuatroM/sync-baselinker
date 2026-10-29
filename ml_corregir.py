"""
Corrige títulos y atributos puntuales de publicaciones (lista en datos/correcciones_ml.json).
Cada entrada: items, y opcionalmente titulo (nuevo completo), titulo_reemplazar [viejo, nuevo], atributos {"Nombre": "valor"}.
Los atributos se buscan por nombre en la categoría de la publicación.
DRY_RUN=1 (por defecto): solo muestra qué cambiaría.
"""
import json
import os
import re

from ml.cliente import ML, log, sku_de

dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
lista = json.load(open("datos/correcciones_ml.json", encoding="utf-8"))
ml = ML(os.environ.get("ML_CODIGO"))
log("SIMULACIÓN" if dry else "REAL")
cats = {}


def attr_id(cat, nombre):
    if cat not in cats:
        cats[cat] = ml.get(f"/categories/{cat}/attributes") or []
    for a in cats[cat]:
        if a.get("name", "").strip().lower() == nombre.lower():
            return a["id"]
    return None


for e in lista:
    for iid in e["items"]:
        try:
            it = ml.get(f"/items/{iid}", attributes="id,title,category_id,attributes,sold_quantity,seller_custom_field,status")
            sku = sku_de(it).upper()
            if sku and sku not in (e["sku"].upper(), e["sku"].split("#")[0].upper()):
                log(f"{iid} OMITIDA: el SKU es {sku!r}")
                continue
            cuerpo, cambios = {}, []
            tit = it.get("title", "")
            nuevo = e.get("titulo")
            if e.get("titulo_reemplazar"):
                viejo, nv = e["titulo_reemplazar"]
                nuevo = re.sub(re.escape(viejo), nv, tit, flags=re.I)
            if nuevo and nuevo != tit:
                cuerpo["title"] = nuevo
                cambios.append(f"título: '{tit}' -> '{nuevo}'")
            attrs = []
            for nombre, valor in (e.get("atributos") or {}).items():
                aid = attr_id(it["category_id"], nombre)
                if not aid:
                    cambios.append(f"atributo {nombre}: NO EXISTE en la categoría")
                    continue
                actual = next((a.get("value_name") for a in it.get("attributes") or [] if a["id"] == aid), None)
                if actual != valor:
                    attrs.append({"id": aid, "value_name": valor})
                    cambios.append(f"{nombre} ({aid}): {actual!r} -> {valor!r}")
            if attrs:
                cuerpo["attributes"] = attrs
            if not cuerpo:
                log(f"{iid} {e['sku']} | sin cambios | " + " ; ".join(cambios))
                continue
            if dry:
                log(f"{iid} {e['sku']} | ventas {it.get('sold_quantity')} | SE CAMBIARÍA: " + " ; ".join(cambios))
                continue
            r = ml.put(f"/items/{iid}", cuerpo)
            if r.status_code in (200, 201):
                log(f"{iid} {e['sku']} | CAMBIADO: " + " ; ".join(cambios))
            elif "title" in cuerpo and attrs:
                # si ML no deja cambiar el título (p. ej. por ventas), se intenta solo con los atributos
                r2 = ml.put(f"/items/{iid}", {"attributes": attrs})
                log(f"{iid} {e['sku']} | título RECHAZADO ({r.status_code} {r.text[:150]}) | atributos: "
                    + ("CAMBIADOS" if r2.status_code in (200, 201) else f"ERROR {r2.status_code} {r2.text[:150]}"))
            else:
                log(f"{iid} {e['sku']} | ERROR {r.status_code} {r.text[:250]}")
        except Exception as ex:
            log(f"{iid} ERROR: {ex}"[:300])
