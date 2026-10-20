"""
Solo lectura: junta de la web de EXTRAPOWER (extrapowersa.com.ar) los datos de cada código de
datos/extrapower_lista.json: nombre, marca, descripción, ficha técnica, información adicional y fotos.
Solo acepta la página cuyo "Código:" es exactamente el buscado (la búsqueda a veces trae variantes).
Salida: salida/xp_web.json  {codigo: {...}}   DESDE/LIMITE para partir el trabajo.
"""
import html
import json
import os
import re
import time
from urllib.parse import quote

import requests

WEB = "https://www.extrapowersa.com.ar"
s = requests.Session()
s.headers["User-Agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
lista = json.load(open("datos/extrapower_lista.json", encoding="utf-8"))
desde, limite = int(os.environ.get("DESDE") or 0), int(os.environ.get("LIMITE") or 0)
lista = lista[desde:desde + limite] if limite else lista[desde:]
if os.environ.get("CODIGOS"):
    _c = {c.strip().upper() for c in os.environ["CODIGOS"].split(",")}
    lista = [x for x in json.load(open("datos/extrapower_lista.json", encoding="utf-8")) if x["cod"].upper() in _c]


def get(u):
    for i in range(3):
        try:
            r = s.get(u, timeout=60)
            if r.status_code == 200:
                return r.text
        except requests.RequestException:
            pass
        time.sleep(3 * (i + 1))
    return ""


def limpio(x):
    x = re.sub(r"(?is)<br\s*/?>|</p>|</li>|</tr>|</h\d>", "\n", x)
    x = html.unescape(re.sub(r"<[^>]+>", " ", x))
    return "\n".join(re.sub(r"[ \t\xa0]+", " ", l).strip() for l in x.splitlines() if l.strip())


def bloque(t, clase):
    m = re.search(rf'(?is)<div[^>]+class="[^"]*{clase}[^"]*"[^>]*>(.*?)</div>\s*</div>', t)
    return limpio(m.group(1)) if m else ""


def producto(u, cod):
    t = get(u)
    m = re.search(r"(?is)C[oó]digo:\s*(?:<[^>]+>\s*)*([A-Za-z0-9\-/\.]+)", t)
    if not m or m.group(1).strip().upper() != cod.upper():
        return None
    nombre = limpio((re.search(r'(?is)<div class="product-name">\s*<h1[^>]*>(.*?)</h1>', t) or
                     re.search(r"(?is)<h1[^>]*>(.*?)</h1>", t)).group(1)) if "<h1" in t else ""
    corta = bloque(t, "short-description")
    det = ""
    md = re.search(r'(?is)<div class="box-collateral box-description">(.*?)<div class="box-collateral', t) or \
        re.search(r'(?is)id="product_tabs_description_contents"[^>]*>(.*?)</div>\s*</div>', t)
    if md:
        det = limpio(md.group(1))
    adic = {}
    for k, v in re.findall(r'(?is)<th class="label">(.*?)</th>\s*<td class="data[^"]*">(.*?)</td>', t):
        adic[limpio(k)] = limpio(v)
    fotos = []
    # todas las imágenes del producto (galería incluida), como original: /media/catalog/product/x/y/archivo
    for d1, d2, f in re.findall(r'/media/catalog/product/(?:cache/\d+/(?:image|thumbnail)/(?:\d+x\d+/)?[0-9a-f]{32}/)?'
                                r'([^/"\'\s])/([^/"\'\s])/([^"\'\s/]+\.(?:jpe?g|png))', t, re.I):
        if "placeholder" in f.lower():
            continue
        k = f"{d1}/{d2}/{f}"
        if k not in fotos:
            fotos.append(k)
    return {"url": u, "nombre": nombre, "corta": corta, "detalle": det, "adicional": adic,
            "fotos": [f"{WEB}/media/catalog/product/{f}" for f in fotos]}


out, n_ok = {}, 0
for i, x in enumerate(lista, 1):
    cod = x["cod"]
    t = get(f"{WEB}/catalogsearch/result/?q={quote(cod)}")
    links = []
    for u in re.findall(r'<h3 class="product-name">\s*<a href="([^"]+)"', t):
        if u not in links:
            links.append(u)
    res = None
    for u in links[:6]:
        res = producto(u, cod)
        if res:
            break
    out[cod] = res or {"no_encontrado": True, "resultados": len(links)}
    n_ok += bool(res)
    if i % 100 == 0:
        print(f"{i}/{len(lista)} encontrados {n_ok}", flush=True)
        os.makedirs("salida", exist_ok=True)
        json.dump(out, open("salida/xp_web.json", "w", encoding="utf-8"), ensure_ascii=False)
    time.sleep(0.2)
os.makedirs("salida", exist_ok=True)
json.dump(out, open("salida/xp_web.json", "w", encoding="utf-8"), ensure_ascii=False)
print(f"Total {len(lista)} | encontrados {n_ok} | con fotos {sum(1 for v in out.values() if v.get('fotos'))}")
