"""Funciones para buscar un código en la web de FEMA y obtener las fotos de su ficha."""
import html as H
import io
import re
import time

import requests
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True
WEB = "https://www.femacba.com.ar/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
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


