"""
Solo lectura: busca en la web de FEMA (femacba) los códigos de datos/fema_sin_nombre.json para sacar
nombre, descripción y fotos (la lista de FEMA trae la descripción vacía). Solo acepta la página que
menciona el código exacto. Salida: salida/fema_nombres.json
"""
import json
import os
import re
import time

from femaweb import WEB, get, texto
import requests

lista = json.load(open("datos/fema_sin_nombre.json", encoding="utf-8"))
out, ok = {}, 0
for i, sku in enumerate(lista, 1):
    res = get(f"{WEB}resultado-busqueda.htm?keywords={requests.utils.quote(sku)}")
    cands = []
    for m in re.finditer(r'href="([^"]+\.html)"', res):
        u = m.group(1)
        u = u if u.startswith("http") else WEB + u.lstrip("/")
        if "femacba.com.ar" in u and u not in cands and not re.search(r"/(contacto|nosotros|login|registro|carrito)", u):
            cands.append(u)
    patron = re.compile(rf"(?<![A-Za-z0-9]){re.escape(sku)}(?![A-Za-z0-9])", re.I)
    dato = None
    for u in cands[:6]:
        pag = get(u)
        if not pag or not patron.search(texto(pag)):
            continue
        tit = re.search(r'property="og:title" content="([^"]+)"', pag) or re.search(r"<h1[^>]*>(.*?)</h1>", pag, re.S)
        desc = re.search(r'property="og:description" content="([^"]*)"', pag)
        og = re.search(r'property="og:image" content="([^"]+)"', pag)
        carpeta = re.search(r"Model/Product/(\d+)/", og.group(1)) if og else None
        fotos = []
        if carpeta:
            for f in re.findall(rf"Model/Product/{carpeta.group(1)}/([^\"'\s)>]+?\.(?:jpe?g|png|webp)(?:\.webp)?)", pag, re.I):
                if f not in fotos and not f.startswith("thumb_"):
                    fotos.append(f)
        dato = {"url": u, "nombre": " ".join(texto(tit.group(1)).split()) if tit else "",
                "descripcion": " ".join(texto(desc.group(1)).split())[:500] if desc else "",
                "fotos": [f"{WEB}mods/html/fil/Model/Product/{carpeta.group(1)}/{f}" for f in fotos][:8] if carpeta else []}
        break
    out[sku] = dato or {"no_encontrado": True}
    ok += bool(dato)
    if i % 100 == 0:
        print(f"{i}/{len(lista)} encontrados {ok}", flush=True)
        os.makedirs("salida", exist_ok=True)
        json.dump(out, open("salida/fema_nombres.json", "w", encoding="utf-8"), ensure_ascii=False)
    time.sleep(0.3)
os.makedirs("salida", exist_ok=True)
json.dump(out, open("salida/fema_nombres.json", "w", encoding="utf-8"), ensure_ascii=False)
print(f"Total {len(lista)} | encontrados {ok} | con fotos {sum(1 for v in out.values() if v.get('fotos'))}")
