"""
Busca cada SKU en la web de FEMA (femacba.com.ar), toma las fotos de la ficha del producto y
(si DRY_RUN=0) reemplaza con ellas la foto genérica de la caja en la publicación de Mercado Libre.
Lista: datos/<LISTA>.json  [{sku, item, ...}]
- Solo usa una página de producto si en ella figura el código exacto.
- En ML solo toca publicaciones con SKU coincidente y 1-2 fotos (caja + ficha opcional); la ficha queda al final.
"""
import csv
import html as H
import io
import json
import os
import re
import time
from collections import Counter

import requests
from PIL import Image, ImageFile

from ml.cliente import ML, log, sku_de

ImageFile.LOAD_TRUNCATED_IMAGES = True
WEB = "https://www.femacba.com.ar/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
SUFIJO = "#FEMA"
dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
desde = int(os.environ.get("DESDE") or 0)
limite = int(os.environ.get("LIMITE") or 0)
lista = json.load(open(f"datos/{os.environ.get('LISTA') or 'revisar_estado'}.json", encoding="utf-8"))[desde:]
if limite:
    lista = lista[:limite]
web = requests.Session()
web.headers.update(UA)


def get(url):
    for i in range(3):
        try:
            r = web.get(url, timeout=40)
            if r.status_code == 200:
                return r.text
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    return ""


def texto(h):
    return H.unescape(re.sub(r"<[^>]+>", " ", h))


def buscar(sku):
    """Devuelve (url_pagina, [urls de fotos]) o (None, [])."""
    res = get(f"{WEB}resultado-busqueda.htm?keywords={requests.utils.quote(sku)}")
    cands = []
    for m in re.finditer(r'href="([^"]+\.html)"', res):
        u = m.group(1)
        u = u if u.startswith("http") else WEB + u.lstrip("/")
        if "femacba.com.ar" in u and u not in cands and not re.search(r"/(contacto|nosotros|login|registro|carrito)", u):
            cands.append(u)
    patron = re.compile(rf"(?<![A-Za-z0-9]){re.escape(sku)}(?![A-Za-z0-9])", re.I)
    for u in cands[:6]:
        pag = get(u)
        if not pag or not patron.search(texto(pag)):
            continue
        og = re.search(r'property="og:image" content="([^"]+)"', pag)
        carpeta = re.search(r"Model/Product/(\d+)/", og.group(1)) if og else None
        if not carpeta:
            continue
        nums = []
        for f in re.findall(rf"Model/Product/{carpeta.group(1)}/([^\"'\s)>]+?\.(?:jpe?g|png|webp)(?:\.webp)?)", pag, re.I):
            if f not in nums and not f.startswith("thumb_"):
                nums.append(f)
        return u, [f"{WEB}mods/html/fil/Model/Product/{carpeta.group(1)}/{f}" for f in nums][:8]
    return None, []


def a_jpeg(datos):
    im = Image.open(io.BytesIO(datos)).convert("RGB")
    if max(im.size) < 500:
        esc = 800 / max(im.size)
        im = im.resize((round(im.width * esc), round(im.height * esc)), Image.LANCZOS)
        lienzo = Image.new("RGB", (max(im.width, 600), max(im.height, 600)), "white")
        lienzo.paste(im, ((lienzo.width - im.width) // 2, (lienzo.height - im.height) // 2))
        im = lienzo
    im.thumbnail((1600, 1600))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return out.getvalue()


ml = ML(os.environ.get("ML_CODIGO")) if not dry else None
filas = []
for n, x in enumerate(lista, 1):
    sku, item = x["sku"], x["item"]
    fila = {"n": desde + n, "sku": sku, "publicacion": item, "titulo": x.get("titulo", ""), "pagina": "", "fotos": 0,
            "resultado": ""}
    try:
        pag, fotos = buscar(sku)
        fila["pagina"], fila["fotos"] = pag or "", len(fotos)
        if not fotos:
            fila["resultado"] = "SIN FOTOS EN LA WEB"
        elif dry:
            fila["resultado"] = f"SE REEMPLAZARÍA ({len(fotos)} fotos)"
        else:
            it = ml.get(f"/items/{item}", attributes="id,status,pictures,attributes,seller_custom_field")
            actuales = it.get("pictures") or []
            if sku_de(it).upper() not in (sku.upper(), (sku + SUFIJO).upper()):
                fila["resultado"] = f"OMITIDA: SKU en ML {sku_de(it)!r}"
            elif len(actuales) > 2:
                fila["resultado"] = f"OMITIDA: ya tiene {len(actuales)} fotos"
            else:
                nuevas = []
                for i, u in enumerate(fotos):
                    r = web.get(u, timeout=60)
                    if r.status_code != 200 or len(r.content) < 3000:
                        continue
                    up = ml.pedir("POST", "/pictures/items/upload", files={"file": (f"{sku}_{i + 1}.jpg", a_jpeg(r.content))})
                    if up.status_code in (200, 201):
                        nuevas.append(up.json()["id"])
                if not nuevas:
                    fila["resultado"] = "ERROR: no se pudo subir ninguna foto"
                else:
                    cuerpo = {"pictures": [{"id": p} for p in nuevas] + [{"id": p["id"]} for p in actuales[1:]]}
                    r = ml.put(f"/items/{item}", cuerpo)
                    fila["resultado"] = (f"REEMPLAZADA ({len(nuevas)} fotos)" if r.status_code in (200, 201)
                                         else f"ERROR {r.status_code}: {r.text[:250]}")
    except Exception as e:
        fila["resultado"] = f"ERROR: {e}"[:300]
    filas.append(fila)
    log(f"[{fila['n']}] {sku}: {fila['resultado']}")

os.makedirs("salida", exist_ok=True)
with open("salida/fema_web_fotos.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Resumen: " + str(Counter(re.sub(r" \(.*", "", r["resultado"]).split(":")[0] for r in filas)))
