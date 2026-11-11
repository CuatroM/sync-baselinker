"""
Solo lectura: productos de la lista de FEMA que NO están en BaseLinker.
Descarga la lista del proveedor (igual que la sincronización), la compara con el catálogo FEMA de BaseLinker y
arma salida/fema_nuevos.csv + salida/FEMA_nuevos_para_BaseLinker.xlsx con SKU, descripción, precio, stock, unidad,
y si el código es NUEVO (no estaba sin vincular el 04/10/2026) o es de los que ya faltaban antes.
"""
import io
import json
import os

import html as H
import re
import time

import pandas as pd
import requests
import yaml

import sync

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



cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
cont = sync.descargar_lista(cfg)
lista = sync.leer_lista(cont, cfg)
df = pd.read_excel(io.BytesIO(cont), dtype=object) if cont[:2] in (b"PK", b"\xd0\xcf") else \
    pd.read_csv(io.StringIO(cont.decode("utf-8-sig", errors="replace")), sep=None, engine="python", dtype=object)
df.columns = [str(c).strip() for c in df.columns]
sync.log(f"Columnas de la lista: {list(df.columns)}")
colde = lambda *k: next((c for c in df.columns if any(x in c.upper() for x in k)), None)
C_DESC, C_SUG, C_UNI = colde("DESCRIP", "NOMBRE", "TITULO", "DETALLE"), colde("SUGERIDO"), colde("UNIDAD")
bl = sync.BaseLinker(os.environ["BL_TOKEN"])
b = cfg["baselinker"]
skus = set()
for p in bl.catalogo(int(b["inventory_id"])).values():
    s = sync.normalizar_codigo(p.get("sku"))
    skus.add(s)
    skus.add(s.split("#")[0])
antes = set(json.load(open("datos/fema_sin_vincular_20261004.json")))
filas = []
for _, f in df.iterrows():
    c = sync.normalizar_codigo(f.get("SKU"))
    if not c or c in ("-", "N/A", "NAN") or c in skus or c + "#FEMA" in skus:
        continue
    d = lista.get(c, {})
    filas.append({"SKU": c + "#FEMA", "CODIGO FEMA": c, "DESCRIPCION": ("" if pd.isna(f.get(C_DESC)) else str(f.get(C_DESC)).strip()) if C_DESC else "",
                  "PRECIO MAYORISTA": d.get("precio", ""), "STOCK": d.get("stock", 0),
                  "PRECIO SUGERIDO ML": f.get(C_SUG, "") if C_SUG else "", "UNIDAD DE VENTA": f.get(C_UNI, "") if C_UNI else "",
                  "NUEVO": "SI (apareció después del 04/10)" if c not in antes else "no (ya faltaba)"})
# los que tienen stock y precio: se buscan en la web de FEMA (nombre y fotos), porque la lista no trae descripción
for f in filas:
    if float(f["STOCK"] or 0) > 0 and f["PRECIO MAYORISTA"] not in ("", None):
        try:
            url, fotos = buscar(f["CODIGO FEMA"])
        except Exception as e:
            url, fotos = None, []
        f["WEB FEMA"], f["FOTOS WEB"] = url or "", len(fotos)
        if url:
            pag = get(url)
            m = re.search(r'property="og:title" content="([^"]+)"', pag or "") or re.search(r"<h1[^>]*>(.*?)</h1>", pag or "", re.S)
            if m and not f["DESCRIPCION"]:
                f["DESCRIPCION"] = re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip()
        time.sleep(0.5)
os.makedirs("salida", exist_ok=True)
# códigos puntuales pedidos (CODIGOS="a,b,c"): estado en la lista, en BaseLinker y en la web de FEMA
pedidos = [sync.normalizar_codigo(x) for x in (os.environ.get("CODIGOS") or "").replace(";", ",").split(",") if x.strip()]
if pedidos:
    filas_df = {sync.normalizar_codigo(r.get("SKU")): r for _, r in df.iterrows()}
    res = []
    for c in pedidos:
        r = filas_df.get(c)
        d = lista.get(c, {})
        url, fotos = buscar(c)
        nombre = ""
        if url:
            m = re.search(r'property="og:title" content="([^"]+)"', get(url) or "")
            nombre = H.unescape(m.group(1)).strip() if m else ""
        res.append({"CODIGO": c, "EN LISTA FEMA": "SI" if r is not None else "NO",
                    "DESCRIPCION LISTA": "" if r is None or pd.isna(r.get(C_DESC)) else str(r.get(C_DESC)).strip(),
                    "PRECIO MAYORISTA": d.get("precio", ""), "STOCK": d.get("stock", ""),
                    "EN BASELINKER": "SI" if (c in skus or c + "#FEMA" in skus) else "NO",
                    "WEB FEMA": url or "", "NOMBRE WEB": nombre, "FOTOS WEB": len(fotos), "URLS FOTOS": " ".join(fotos)})
        sync.log(f"{c}: lista={res[-1]['EN LISTA FEMA']} precio={res[-1]['PRECIO MAYORISTA']} stock={res[-1]['STOCK']} "
                 f"BL={res[-1]['EN BASELINKER']} web={'SI' if url else 'NO'} fotos={len(fotos)} | {nombre or res[-1]['DESCRIPCION LISTA']}")
        time.sleep(0.5)
    pd.DataFrame(res).to_csv("salida/fema_codigos.csv", sep=";", index=False, encoding="utf-8-sig")
out = pd.DataFrame(filas).sort_values(["NUEVO", "STOCK"], ascending=[False, False])
out.to_csv("salida/fema_nuevos.csv", sep=";", index=False, encoding="utf-8-sig")
out.to_excel("salida/FEMA_sin_BaseLinker.xlsx", index=False)
con = out[out["STOCK"].astype(float) > 0]
sync.log(f"Sin BaseLinker: {len(out)} | con stock: {len(con)} | nuevos desde 04/10: {(out['NUEVO'].str.startswith('SI')).sum()} "
         f"(con stock: {(con['NUEVO'].str.startswith('SI')).sum()})")
