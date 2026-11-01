"""
Solo lectura: lee la página de cada producto NUEVO de la tienda J&A (datos/jya_nuevos.json) y guarda todo lo que trae:
nombre, precio, stock y variantes (var stock), datos del producto (var s_producto), descripción, fotos y categoría.
Salida: salida/jya_nuevos_web.json (+ 2 páginas de ejemplo en salida/ para revisar).
"""
import json
import os
import re
import time
from html import unescape

import requests

lista = json.load(open("datos/jya_nuevos.json", encoding="utf-8"))
web = requests.Session()
web.headers["User-Agent"] = "Mozilla/5.0 (lectura de productos)"
RE_STOCK = re.compile(r"var\s+stock\s*=\s*(\[.*?\]);\s*\n", re.S)
RE_PROD = re.compile(r"var\s+s_producto\s*=\s*(\{.*?\});", re.S)
os.makedirs("salida", exist_ok=True)


def pedir(url):
    for espera in (0, 90, 180):
        if espera:
            print(f"403: espero {espera} s", flush=True)
            time.sleep(espera)
        time.sleep(1.3)
        r = web.get(url, timeout=40)
        if r.status_code != 403:
            return r
    return r


def texto(h):
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<br\s*/?>|</p>|</li>|</div>", "\n", h, flags=re.I)
    return re.sub(r"[ \t]+", " ", unescape(re.sub(r"<[^>]+>", " ", h))).strip()


out = []
for n, x in enumerate(lista, 1):
    d = {"url": x["url"], "nombre_listado": x["nombre"]}
    try:
        r = pedir(x["url"])
        d["http"] = r.status_code
        if r.status_code == 200:
            h = r.text
            if n <= 2:
                open(f"salida/ejemplo_{n}.html", "w", encoding="utf-8").write(h)
            m = RE_STOCK.search(h)
            d["stock_var"] = json.loads(m.group(1)) if m else None
            m = RE_PROD.search(h)
            d["producto"] = json.loads(m.group(1)) if m else None
            m = re.search(r'<meta[^>]+(?:name|property)=["\'](?:og:)?description["\'][^>]+content=["\']([^"\']*)', h, re.I)
            d["meta_descripcion"] = unescape(m.group(1)) if m else ""
            m = re.search(r'class=["\'][^"\']*(?:product-description|product__description|descripcion)[^"\']*["\'][^>]*>(.*?)</(?:div|section)>', h, re.S | re.I)
            d["descripcion"] = texto(m.group(1)) if m else ""
            d["fotos"] = list(dict.fromkeys(re.findall(r"https://d22fxaf9t8d39k\.cloudfront\.net/[\w]+\.(?:jpe?g|png|webp)", h)))
            m = re.search(r'<h1[^>]*>(.*?)</h1>', h, re.S)
            d["h1"] = texto(m.group(1)) if m else ""
    except Exception as e:
        d["error"] = str(e)[:200]
    out.append(d)
    print(f"{n}/{len(lista)} {d.get('http')} {x['nombre'][:60]} | fotos {len(d.get('fotos') or [])}", flush=True)

json.dump(out, open("salida/jya_nuevos_web.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print("Listo:", sum(1 for d in out if d.get("http") == 200), "de", len(out))
