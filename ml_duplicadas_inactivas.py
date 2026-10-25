"""
Publicaciones que ML dejó inactivas por "Era igual a otra de tus publicaciones" (clásicas y de catálogo).
Pedido por el usuario. Busca en todas las marcas.

1) Recorre las publicaciones de todos los estados y toma las inactivas, cerradas, en revisión
   y las pausadas por ML (con sub-estado).
2) A cada una le pide a ML el motivo (moderaciones). Es duplicada si el motivo dice
   "igual a otra" / "duplicad" / "duplicate".
3) Busca otra publicación VIVA del mismo SKU (activa, pausada o en revisión, no duplicada).
DRY_RUN=1 (por defecto): solo arma la lista, no toca nada.
DRY_RUN=0: elimina (cierra y borra) solo las que, comprobadas otra vez en el momento,
           siguen sin estar activas, no tienen ventas y tienen otra publicación viva del mismo SKU.
Salida: salida/duplicadas_inactivas.csv y salida/motivos.json
"""
import csv
import json
import os
import re
import time
from collections import Counter, defaultdict

from ml.cliente import ML, log, sku_de

dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
ml = ML(os.environ.get("ML_CODIGO"))
log("SIMULACIÓN (no se borra nada)" if dry else "REAL: se eliminan las que pasen la comprobación")

VIVOS = ("active", "paused", "under_review")
ids = {}
for e in ("active", "paused", "under_review", "inactive", "closed"):
    try:
        lote = ml.ids_publicaciones(status=e)
    except Exception as ex:
        log(f"{e}: {ex}")
        lote = []
    log(f"Estado {e}: {len(lote)}")
    for i in lote:
        ids.setdefault(i, e)

items = {}
for it in ml.publicaciones(list(ids), atributos="id,title,status,sub_status,sold_quantity,attributes,"
                                                "seller_custom_field,listing_type_id,catalog_listing,tags,deleted"):
    items[it["id"]] = it
log(f"Detalle de {len(items)} publicaciones")
log("Estados: " + str(Counter(f"{i.get('status')}/{','.join(i.get('sub_status') or [])}" for i in items.values()).most_common(15)))


def base(s):
    return (s or "").upper().strip()


def motivo(item_id):
    textos = []
    for ruta, params in ((f"/moderations/infractions/{ml.user_id}", {"related_item_id": item_id}),
                         (f"/items/{item_id}/moderations", {})):
        r = ml.pedir("GET", ruta, params=params)
        if r.status_code != 200:
            continue
        d = r.json()
        lst = d.get("infractions") if isinstance(d, dict) else d
        for i in lst or []:
            if isinstance(i, dict):
                for k in ("reason", "remedy", "element_name", "subject", "description"):
                    if i.get(k):
                        textos.append(str(i[k]))
    return " || ".join(dict.fromkeys(textos))[:400]


DUP = re.compile(r"igual a otra|duplicad|duplicate", re.I)
por_sku = defaultdict(list)
for i in items.values():
    por_sku[base(sku_de(i))].append(i)

candidatas, motivos = [], Counter()
for i in items.values():
    if i.get("status") == "active" or i.get("deleted"):
        continue
    if i.get("status") == "paused" and not i.get("sub_status"):
        continue  # pausadas comunes (por stock, a mano): no son bajas de ML
    m = motivo(i["id"])
    motivos[m[:120]] += 1
    if not DUP.search(m) and "duplicated" not in ",".join(i.get("sub_status") or []):
        continue
    candidatas.append((i, m))
log(f"No activas revisadas: {sum(motivos.values())} | duplicadas: {len(candidatas)}")
log("Motivos más comunes: " + str(motivos.most_common(12)))

filas = []
dups = {c[0]["id"] for c in candidatas}
for n, (it, m) in enumerate(candidatas, 1):
    sku = sku_de(it)
    vivas = [o["id"] for o in por_sku.get(base(sku), []) if o["id"] != it["id"] and o.get("status") in VIVOS
             and o["id"] not in dups and not o.get("deleted")] if sku else []
    f = {"sku": sku, "publicacion": it["id"], "titulo": it.get("title"),
         "tipo": "catálogo" if it.get("catalog_listing") else it.get("listing_type_id"),
         "estado": f"{it.get('status')}/{','.join(it.get('sub_status') or [])}", "vendidos": it.get("sold_quantity") or 0,
         "motivo": m, "viva": ",".join(vivas[:3]), "resultado": ""}
    try:
        if not sku:
            raise RuntimeError("no tiene SKU")
        if not vivas:
            raise RuntimeError("no hay otra publicación viva del mismo SKU")
        if f["vendidos"] > 0:
            raise RuntimeError("tiene ventas")
        if dry:
            f["resultado"] = "SE ELIMINARÍA"
        else:
            ahora = ml.get(f"/items/{it['id']}")
            if ahora.get("status") == "active":
                raise RuntimeError("ahora está activa")
            if base(sku_de(ahora)) != base(sku):
                raise RuntimeError(f"el SKU cambió a {sku_de(ahora)!r}")
            if (ahora.get("sold_quantity") or 0) > 0:
                raise RuntimeError("tiene ventas")
            ok_viva = False
            for v in vivas:
                iv = ml.get(f"/items/{v}")
                if base(sku_de(iv)) == base(sku) and iv.get("status") in VIVOS and not iv.get("deleted"):
                    ok_viva = True
                    break
            if not ok_viva:
                raise RuntimeError("la otra publicación ya no está viva")
            ok, r = False, None
            for intento in range(3):
                if ahora.get("status") != "closed":
                    ml.put(f"/items/{it['id']}", {"status": "closed"})
                    time.sleep(1)
                r = ml.put(f"/items/{it['id']}", {"deleted": "true"})
                if r.status_code in (200, 201):
                    ok = True
                    break
                time.sleep(5 * (intento + 1))
            f["resultado"] = "ELIMINADA" if ok else f"NO SE PUDO ELIMINAR: {r.status_code} {r.text[:150]}"
    except Exception as e:
        f["resultado"] = f"NO SE TOCA: {e}"[:300]
    if n % 50 == 0 or not f["resultado"].startswith(("SE ELIMINARÍA", "ELIMINADA")):
        log(f"{n}/{len(candidatas)} {sku} {it['id']}: {f['resultado']}")
    filas.append(f)

os.makedirs("salida", exist_ok=True)
json.dump(motivos.most_common(), open("salida/motivos.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
campos = ["sku", "publicacion", "titulo", "tipo", "estado", "vendidos", "motivo", "viva", "resultado"]
with open("salida/duplicadas_inactivas.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=campos, delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Por tipo: " + str(Counter(f["tipo"] for f in filas)))
log("Resumen: " + str(Counter(re.split(r"[:(]", f["resultado"])[0].strip() for f in filas)))
