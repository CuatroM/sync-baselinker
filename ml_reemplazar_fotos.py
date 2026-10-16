"""
Reemplaza la foto genérica de la caja FEMA por las fotos reales encontradas en Drive.
Lista de trabajo: datos/reemplazo_fotos.json  [{sku, item, titulo, fotos: [[drive_id, ruta], ...]}]
- Solo toca publicaciones cuyo SKU coincide y que tienen 1 o 2 fotos (caja + ficha opcional).
- Nuevas fotos = fotos de Drive + las que tenía después de la primera (la ficha queda al final).
- No cambia precio, stock, estado ni ningún otro dato.
"""
import csv
import io
import json
import os
import time

from PIL import Image, ImageFile

from drive.carpeta import descargar
from ml.cliente import ML, log, sku_de

ImageFile.LOAD_TRUNCATED_IMAGES = True
SUFIJO = "#FEMA"
dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
desde = int(os.environ.get("DESDE") or 0)
limite = int(os.environ.get("LIMITE") or 0)

trabajo = json.load(open("datos/reemplazo_fotos.json", encoding="utf-8"))[desde:]
if limite:
    trabajo = trabajo[:limite]
ml = ML(os.environ.get("ML_CODIGO"))
log(f"{len(trabajo)} publicaciones a revisar (desde {desde}) | {'SIMULACIÓN' if dry else 'REAL'}")


def a_jpeg(datos, minimo=False):
    im = Image.open(io.BytesIO(datos)).convert("RGB")
    if minimo or max(im.size) < 500:
        esc = 800 / max(im.size)
        im = im.resize((max(1, round(im.width * esc)), max(1, round(im.height * esc))), Image.LANCZOS)
        lienzo = Image.new("RGB", (max(im.width, 600), max(im.height, 600)), "white")
        lienzo.paste(im, ((lienzo.width - im.width) // 2, (lienzo.height - im.height) // 2))
        im = lienzo
    im.thumbnail((1600, 1600))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return out.getvalue()


def subir(drive_id, ruta):
    datos = a_jpeg(descargar(drive_id))
    nombre = os.path.splitext(os.path.basename(ruta))[0] + ".jpg"
    for intento in range(3):
        up = ml.pedir("POST", "/pictures/items/upload", files={"file": (nombre, datos)})
        if up.status_code in (200, 201):
            return up.json()["id"]
        if "500 p" in up.text:
            datos = a_jpeg(datos, minimo=True)
            continue
        time.sleep(3 * (intento + 1))
    log(f"  no pude subir {ruta}: {up.status_code} {up.text[:150]}")
    return None


res = []
for n, t in enumerate(trabajo, 1):
    fila = {"sku": t["sku"], "publicacion": t["item"], "titulo": t["titulo"],
            "fotos_drive": " | ".join(r for _, r in t["fotos"]), "resultado": ""}
    try:
        it = ml.get(f"/items/{t['item']}", attributes="id,status,pictures,attributes,seller_custom_field")
        sku_ml = sku_de(it)
        fotos_ml = it.get("pictures") or []
        if sku_ml.upper() not in (t["sku"].upper(), (t["sku"] + SUFIJO).upper()):
            fila["resultado"] = f"OMITIDA: el SKU de la publicación es {sku_ml!r}"
        elif len(fotos_ml) > 2:
            fila["resultado"] = f"OMITIDA: ya tiene {len(fotos_ml)} fotos (no es solo la caja)"
        elif dry:
            fila["resultado"] = f"SE REEMPLAZARÍA: {len(t['fotos'])} foto(s) de Drive + {len(fotos_ml) - 1} actual(es) al final"
        else:
            nuevas = [x for x in (subir(i, r) for i, r in t["fotos"]) if x]
            if not nuevas:
                fila["resultado"] = "ERROR: no se pudo subir ninguna foto de Drive"
            else:
                cuerpo = {"pictures": [{"id": x} for x in nuevas] + [{"id": p["id"]} for p in fotos_ml[1:]]}
                r = ml.put(f"/items/{t['item']}", cuerpo)
                fila["resultado"] = ("REEMPLAZADA" if r.status_code in (200, 201)
                                     else f"ERROR {r.status_code}: {r.text[:250]}")
    except Exception as e:
        fila["resultado"] = f"ERROR: {e}"[:300]
    log(f"[{desde + n}] {t['sku']} {t['item']}: {fila['resultado']}")
    res.append(fila)

os.makedirs("salida", exist_ok=True)
with open("salida/reemplazo_fotos_resultado.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(res[0].keys()) if res else ["sku"], delimiter=";")
    w.writeheader()
    w.writerows(res)
from collections import Counter
log("Resumen: " + str(Counter(r["resultado"].split(":")[0] for r in res)))
