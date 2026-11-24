"""TORREON: lee todos los productos de la web (Tiendanube), solo lectura.
Fuente: sitemap.xml (o las páginas del listado /productos/) y cada página de producto.
De cada producto: nombre, SKU/código, precio de lista, stock por variante (el número que muestra la tienda),
categoría, fotos, descripción y datos técnicos.
Salida: salida/torreon_web.json y salida/torreon_web.csv."""
import csv
import html as H
import json
import os
import re
import time

import requests
from bs4 import BeautifulSoup

BASE = "https://www.torreon.com.ar"
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (sync-baselinker; lectura de catálogo)"
URLS_EXTRA = [u.strip() for u in (os.environ.get("URLS") or "").split() if u.strip()]


def log(m):
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), m, flush=True)


def get(url):
    for i in range(4):
        try:
            r = S.get(url, timeout=40)
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                return ""
            log(f"  {r.status_code} {url}")
        except Exception as e:
            log(f"  error {e} {url}")
        time.sleep(3 * (i + 1))
    return ""


def es_producto(u):
    return re.match(rf"{re.escape(BASE)}/productos/[^/?#]+/?$", u) is not None


def urls_productos():
    urls = []
    sm = get(f"{BASE}/sitemap.xml")
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sm)
    for m in [x for x in locs if x.endswith(".xml")]:
        locs += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", get(m))
    urls += [u.replace("http://", "https://") for u in locs if es_producto(u.replace("http://", "https://"))]
    log(f"sitemap: {len(urls)} productos")
    # listado paginado (por si el sitemap no está completo)
    for n in range(1, 40):
        h = get(f"{BASE}/productos/page/{n}/" if n > 1 else f"{BASE}/productos/")
        nuevos = [u for u in re.findall(r'href="((?:https?:)?//www\.torreon\.com\.ar/productos/[^"?#]+/?)"', h)]
        nuevos = [("https:" + u if u.startswith("//") else u) for u in nuevos]
        nuevos = [u if u.endswith("/") else u + "/" for u in nuevos if es_producto(u)]
        antes = len(set(urls))
        urls += nuevos
        if not nuevos or len(set(urls)) == antes:
            break
    urls = list(dict.fromkeys(u if u.endswith("/") else u + "/" for u in urls + URLS_EXTRA))
    log(f"total: {len(urls)} productos")
    return urls


def foto_grande(u):
    u = "https:" + u if u.startswith("//") else u
    return re.sub(r"-(\d+)-(\d+)\.(webp|jpg|jpeg|png)", r"-1024-1024.\3", u)


def leer(url):
    h = get(url)
    if not h:
        return {"url": url, "error": "no se pudo leer"}
    s = BeautifulSoup(h, "html.parser")
    meta = lambda p: (s.find("meta", attrs={"property": p}) or s.find("meta", attrs={"name": p}) or {}).get("content", "")
    h1 = s.find("h1")
    nombre = h1.get_text(" ", strip=True) if h1 else meta("og:title")
    # variantes de Tiendanube (precio, stock, sku)
    variantes = []
    for tag in s.select("[data-variants]"):
        try:
            variantes = json.loads(H.unescape(tag["data-variants"]))
            break
        except Exception:
            continue
    ld = {}
    for sc in s.find_all("script", type="application/ld+json"):
        try:
            j = json.loads(sc.string or "")
        except Exception:
            continue
        for x in (j if isinstance(j, list) else [j]):
            if isinstance(x, dict) and x.get("@type") == "Product":
                ld = x
    of = ld.get("offers") or {}
    of = of[0] if isinstance(of, list) and of else of
    def nro(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    var = []
    for v in variantes:
        var.append({"sku": v.get("sku") or "", "nombre": " / ".join(str(x) for x in (v.get("option0"), v.get("option1"),
                                                                                  v.get("option2")) if x),
                    "precio": nro(v.get("price_number")), "precio_tachado": nro(v.get("compare_at_price_number")),
                    "stock": v.get("stock"), "disponible": bool(v.get("available")),
                    "foto": foto_grande(v["image_url"]) if v.get("image_url") else ""})
    precio = next((v["precio"] for v in var if v["precio"]), None) or nro(of.get("price")) or nro(meta("product:price:amount"))
    stock_total = None
    if var and all(v["stock"] is not None for v in var):
        stock_total = sum(int(v["stock"] or 0) for v in var)
    texto = s.get_text(" ", strip=True)
    m = re.search(r"Solo quedan\s+(\d+)\s+en stock", texto, re.I)
    if stock_total is None and m:
        stock_total = int(m.group(1))
    disponible = any(v["disponible"] for v in var) if var else ("InStock" in str(of.get("availability", "")))
    if stock_total is None:
        stock_total = 0 if not disponible else None   # disponible sin número: stock ilimitado en la tienda
    # fotos
    fotos = []
    for u in re.findall(r'(?:https?:)?//[a-z0-9.-]*mitiendanube\.com/stores/[^"\'\s,)]+?/products/[^"\'\s,)]+?\.(?:webp|jpg|jpeg|png)', h):
        f = foto_grande(u)
        if f not in fotos:
            fotos.append(f)
    # descripción
    d = s.select_one("[data-store='product-description'], .product-description, .js-product-description, "
                     "#product-description, .description")
    desc = d.get_text("\n", strip=True) if d else ""
    tablas = []
    for t in (d.find_all("table") if d else []):
        for tr in t.find_all("tr"):
            c = [x.get_text(" ", strip=True) for x in tr.find_all(["td", "th"])]
            if len(c) >= 2 and c[0]:
                tablas.append([c[0], " ".join(c[1:])])
    migas = [a.get_text(" ", strip=True) for a in s.select(".breadcrumb a, .breadcrumbs a, nav[aria-label*='readcrumb'] a")]
    sku = ld.get("sku") or (var[0]["sku"] if var else "")
    return {"url": url, "nombre": nombre, "sku": sku, "marca": (ld.get("brand") or {}).get("name", "") if isinstance(ld.get("brand"), dict) else (ld.get("brand") or ""),
            "precio_web": precio, "stock_web": stock_total, "disponible": disponible,
            "categorias": [x for x in migas if x.lower() not in ("inicio", "productos", "home")],
            "variantes": var, "fotos": fotos[:12], "descripcion": desc[:4000], "tabla": tablas,
            "meta_descripcion": meta("og:description") or meta("description")}


os.makedirs("salida", exist_ok=True)
res = []
for i, u in enumerate(urls_productos(), 1):
    p = leer(u)
    res.append(p)
    log(f"{i:3} {p.get('sku') or '-':16} | {str(p.get('nombre'))[:55]:55} | ${p.get('precio_web')} | stock {p.get('stock_web')} | "
        f"{len(p.get('fotos') or [])} fotos | {len(p.get('variantes') or [])} var" + (f" | {p['error']}" if p.get("error") else ""))
    time.sleep(0.6)
json.dump(res, open("salida/torreon_web.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
with open("salida/torreon_web.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["SKU", "NOMBRE", "PRECIO WEB", "PRECIO −30%", "STOCK WEB", "DISPONIBLE", "CATEGORÍA", "VARIANTES", "FOTOS", "URL"])
    for p in res:
        pw = p.get("precio_web")
        w.writerow([p.get("sku"), p.get("nombre"), pw, round(pw * 0.7, 2) if pw else "", p.get("stock_web"),
                    "SI" if p.get("disponible") else "NO", " > ".join(p.get("categorias") or []),
                    len(p.get("variantes") or []), len(p.get("fotos") or []), p.get("url")])
ok = [p for p in res if not p.get("error")]
log(f"Listo: {len(ok)} productos leídos | disponibles {sum(1 for p in ok if p.get('disponible'))} | "
    f"sin stock {sum(1 for p in ok if not p.get('disponible'))} | sin precio {sum(1 for p in ok if not p.get('precio_web'))} | "
    f"sin SKU {sum(1 for p in ok if not p.get('sku'))} | con variantes {sum(1 for p in ok if len(p.get('variantes') or []) > 1)}")
