"""
Republica publicaciones que ML marcó como prohibidas (categoría incorrecta / fotos que no coinciden).
Para cada una de datos/republicar.json [{sku, item, titulo}]:
  1. Lee la publicación vieja: la categoría que hoy le asignó ML, título, precio, atributos, envío y descripción.
  2. Busca las fotos reales del código en la web de FEMA (si no hay, no hace nada).
  3. Crea la publicación nueva en esa categoría con esas fotos y la deja pausada.
  4. Solo si la nueva se creó bien: cierra y elimina la vieja (pedido y confirmado por el usuario).
DRY_RUN=1 valida la nueva sin crear nada ni tocar la vieja.
"""
import csv
import json
import os
import re
import time
from collections import Counter

from femaweb import a_jpeg, buscar, web
from ml.cliente import ML, log, sku_de

dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
lista = json.load(open("datos/republicar.json", encoding="utf-8"))
ml = ML(os.environ.get("ML_CODIGO"))
log(f"{len(lista)} publicaciones | {'SIMULACIÓN' if dry else 'REAL'}")
NO_COPIAR = {"ITEM_CONDITION"}


def subir(sku, url, i):
    r = web.get(url, timeout=60)
    if r.status_code != 200 or len(r.content) < 3000:
        return None
    up = ml.pedir("POST", "/pictures/items/upload", files={"file": (f"{sku}_{i}.jpg", a_jpeg(r.content))})
    return {"id": up.json()["id"]} if up.status_code in (200, 201) else None


filas = []
for x in lista:
    sku, viejo = x["sku"], x["item"]
    fila = {"sku": sku, "vieja": viejo, "titulo": x["titulo"], "categoria": "", "nueva": "", "resultado": ""}
    try:
        it = ml.get(f"/items/{viejo}")
        if sku_de(it).upper() not in (sku.upper(), (sku + "#FEMA").upper()):
            raise RuntimeError(f"el SKU en ML es {sku_de(it)!r}")
        cat = it.get("category_id")
        rc = ml.pedir("GET", f"/categories/{cat}")
        fila["categoria"] = f"{cat} {rc.json().get('name', '') if rc.status_code == 200 else ''}".strip()
        _, fotos = buscar(sku)
        if not fotos:
            raise RuntimeError("no hay fotos en la web de FEMA")
        rd = ml.pedir("GET", f"/items/{viejo}/description")
        texto = rd.json().get("plain_text", "") if rd.status_code == 200 else ""
        attrs = [{"id": a["id"], **({"value_id": a["value_id"]} if a.get("value_id") else {"value_name": a.get("value_name")})}
                 for a in it.get("attributes") or []
                 if a.get("id") not in NO_COPIAR and (a.get("value_id") or a.get("value_name"))]
        envio = it.get("shipping") or {}
        item = {"title": it["title"], "category_id": cat, "price": it["price"], "currency_id": it.get("currency_id", "ARS"),
                "available_quantity": max(1, it.get("available_quantity") or 1),
                "buying_mode": it.get("buying_mode", "buy_it_now"), "listing_type_id": it.get("listing_type_id"),
                "condition": it.get("condition", "new"), "attributes": attrs,
                "sale_terms": [{"id": s["id"], "value_name": s.get("value_name")} for s in it.get("sale_terms") or []
                               if s.get("value_name")],
                "shipping": {"mode": envio.get("mode", "me2"), "local_pick_up": False,
                             "free_shipping": bool(envio.get("free_shipping"))}}
        if dry:
            item["pictures"] = [p for p in [subir(sku, fotos[0], 1)] if p]
            v = ml.pedir("POST", "/items/validate", json=item)
            if v.status_code in (200, 204):
                fila["resultado"] = f"SE REPUBLICARÍA ({len(fotos)} fotos)"
            else:
                causas = v.json().get("cause") or []
                errores = [c.get("message") for c in causas if c.get("type") == "error"]
                fila["resultado"] = ("VALIDACIÓN CON ERRORES: " + " / ".join(errores))[:400] if errores else \
                    f"SE REPUBLICARÍA ({len(fotos)} fotos)"
        else:
            item["pictures"] = [p for p in (subir(sku, u, i + 1) for i, u in enumerate(fotos)) if p]
            if not item["pictures"]:
                raise RuntimeError("no se pudo subir ninguna foto")
            r = ml.pedir("POST", "/items", json=item)
            if r.status_code not in (200, 201):
                raise RuntimeError(f"ML rechazó la nueva: {r.status_code} {r.text[:300]}")
            nueva = r.json()["id"]
            fila["nueva"] = nueva
            ml.put(f"/items/{nueva}", {"status": "paused"})
            if texto:
                ml.pedir("POST", f"/items/{nueva}/description", json={"plain_text": texto})
            c1 = ml.put(f"/items/{viejo}", {"status": "closed"})
            time.sleep(1)
            c2 = ml.put(f"/items/{viejo}", {"deleted": "true"})
            if c2.status_code in (200, 201):
                fila["resultado"] = "REPUBLICADA (pausada) y la vieja eliminada"
            else:
                fila["resultado"] = (f"REPUBLICADA (pausada); la vieja no se pudo eliminar: "
                                     f"cerrar {c1.status_code}, eliminar {c2.status_code} {c2.text[:150]}")
    except Exception as e:
        fila["resultado"] = f"ERROR: {e}"[:400]
    log(f"{sku} {viejo} [{fila['categoria']}]: {fila['resultado']}")
    filas.append(fila)

os.makedirs("salida", exist_ok=True)
with open("salida/republicar_resultado.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Resumen: " + str(Counter(re.split(r"[;:(]", r["resultado"])[0].strip() for r in filas)))
