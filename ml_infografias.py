"""
Sube las 2 imágenes informativas (ficha técnica + partes/usos) a las publicaciones de Mercado Libre.
- Lista: LISTA (por defecto datos/infografias_t1.json) [{id, sku, items, fotos: [ruta1, ruta2]}]
  Las imágenes están en la rama "infografias" (el workflow las baja a la carpeta infografias/).
- La portada NO se toca: las nuevas quedan como fotos 2 y 3 y las demás siguen detrás.
  Si la categoría no admite tantas fotos, se dejan afuera las últimas (queda anotado).
- Publicaciones con variantes: las 2 imágenes se agregan también a cada variante (después de su primera foto).
- Se saltean las de catálogo y las que ya tienen las imágenes (registro en datos/infografias_subidas.json).
- DRY_RUN=1 (por defecto): solo informa qué haría. DESDE / LIMITE para hacer por partes (cuentan publicaciones).
"""
import csv
import glob
import json
import os
import time
from collections import Counter

from ml.cliente import ML, log, sku_de

dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
lista_f = (os.environ.get("LISTA") or "todas").strip()
desde = int(os.environ.get("DESDE") or 0)
limite = int(os.environ.get("LIMITE") or 0)
CARPETA = "infografias"
REG = "datos/infografias_subidas.json"

if lista_f.lower() in ("", "todas"):
    lista = [p for f in sorted(glob.glob("datos/infografias_t*.json")) for p in json.load(open(f, encoding="utf-8"))]
else:
    lista = json.load(open(lista_f, encoding="utf-8"))
tareas = [(p, iid) for p in lista for iid in p["items"]][desde:]
if limite:
    tareas = tareas[:limite]
registro = json.load(open(REG, encoding="utf-8")) if os.path.exists(REG) else {}
ml = ML(os.environ.get("ML_CODIGO"))
log(f"{len(tareas)} publicaciones (desde {desde}) | {'SIMULACIÓN' if dry else 'REAL'}")

max_fotos, subidas = {}, {}


def maximo(cat):
    if cat not in max_fotos:
        try:
            max_fotos[cat] = int(ml.get(f"/categories/{cat}").get("settings", {}).get("max_pictures_per_item") or 10)
        except Exception:
            max_fotos[cat] = 10
    return max_fotos[cat]


def subir(ruta):
    if ruta in subidas:
        return subidas[ruta]
    datos = open(os.path.join(CARPETA, ruta), "rb").read()
    for intento in range(3):
        up = ml.pedir("POST", "/pictures/items/upload", files={"file": (os.path.basename(ruta), datos, "image/jpeg")})
        if up.status_code in (200, 201):
            subidas[ruta] = up.json()["id"]
            return subidas[ruta]
        time.sleep(3 * (intento + 1))
    raise RuntimeError(f"no pude subir {ruta}: {up.status_code} {up.text[:150]}")


filas = []
for k, (p, iid) in enumerate(tareas, desde + 1):
    f = {"publicacion": iid, "sku": p["sku"], "titulo": "", "estado": "", "fotos_antes": "", "fotos_despues": "", "resultado": ""}
    try:
        it = ml.get(f"/items/{iid}", attributes="id,title,status,sub_status,pictures,variations,category_id,"
                                              "catalog_listing,attributes,seller_custom_field")
        pics = [x["id"] for x in it.get("pictures") or []]
        f.update(titulo=it.get("title"), estado=f"{it.get('status')}/{','.join(it.get('sub_status') or [])}",
                 fotos_antes=len(pics))
        ya = registro.get(iid, {}).get("fotos") or []
        sku = sku_de(it).upper()
        if sku and sku not in (p["sku"].upper(), p["sku"].split("#")[0].upper()):
            f["resultado"] = f"OMITIDA: el SKU de la publicación es {sku!r}"
        elif it.get("catalog_listing"):
            f["resultado"] = "OMITIDA: es de catálogo"
        elif it.get("status") == "closed":
            f["resultado"] = "OMITIDA: finalizada"
        elif not pics:
            f["resultado"] = "OMITIDA: no tiene portada"
        elif ya and any(x in pics for x in ya):
            f["resultado"] = "OMITIDA: ya tiene las imágenes informativas"
        else:
            tope = maximo(it["category_id"])
            libres = tope - 1
            if libres < 2:
                raise RuntimeError(f"la categoría admite solo {tope} fotos")
            quedan = pics[1:][:libres - 2]
            afuera = len(pics) - 1 - len(quedan)
            nota = f" (quedan afuera las últimas {afuera} fotos: la categoría admite {tope})" if afuera else ""
            if afuera:
                f["resultado"] = f"OMITIDA: no hay lugar para las 2 imágenes sin sacar {afuera} fotos tuyas (la categoría admite {tope})"
            elif dry:
                f["fotos_despues"] = 1 + 2 + len(quedan)
                f["resultado"] = "SE AGREGARÍAN como fotos 2 y 3" + nota
            else:
                nuevas = [subir(r) for r in p["fotos"]]
                todas = [pics[0]] + nuevas + quedan
                cuerpo = {"pictures": [{"id": x} for x in todas]}
                vars_ = it.get("variations") or []
                if vars_:
                    cuerpo["variations"] = []
                    for v in vars_:
                        vp = [x for x in (v.get("picture_ids") or []) if x in todas] or [pics[0]]
                        cuerpo["variations"].append({"id": v["id"], "picture_ids": vp[:1] + nuevas + [x for x in vp[1:] if x not in nuevas]})
                r = ml.put(f"/items/{iid}", cuerpo)
                if r.status_code == 409:
                    # ML a veces responde 409 pero igual aplica el cambio: se verifica antes de reintentar
                    time.sleep(8)
                    ahora = [x["id"] for x in ml.get(f"/items/{iid}", attributes="pictures").get("pictures") or []]
                    if all(x in ahora for x in nuevas):
                        r.status_code = 200
                    else:
                        r = ml.put(f"/items/{iid}", cuerpo)
                if r.status_code not in (200, 201):
                    raise RuntimeError(f"ML rechazó el cambio: {r.status_code} {r.text[:250]}")
                f["fotos_despues"] = len(todas)
                f["resultado"] = "AGREGADAS como fotos 2 y 3" + nota
                registro[iid] = {"sku": p["sku"], "fotos": nuevas, "archivos": p["fotos"]}
                json.dump(registro, open(REG, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    except Exception as e:
        f["resultado"] = f"ERROR: {e}"[:300]
    log(f"[{k}] {iid} {p['sku']} | {f['estado']} | fotos {f['fotos_antes']} -> {f['fotos_despues']} | {f['resultado']}")
    filas.append(f)

os.makedirs("salida", exist_ok=True)
with open("salida/infografias_resultado.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()) if filas else ["publicacion"], delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Resumen: " + str(Counter(x["resultado"].split(" (")[0].split(":")[0] for x in filas)))
