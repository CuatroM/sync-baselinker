"""DELFABRO: lee todos los productos de la web (solo lectura).
Fuente: los sitemaps de productos (permitidos en robots.txt) y cada página de producto.
Salida: salida/delfabro_web.json y salida/delfabro_web.csv con código, nombre, precio, stock (disponible / SIN EXISTENCIA),
categorías, fotos y descripción."""
import csv, json, os, re, time
import requests
from bs4 import BeautifulSoup

BASE = "https://delfabrohidraulicos.com"
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (sync-baselinker; lectura de catálogo)"


def log(m):
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), m, flush=True)


def get(url):
    for i in range(4):
        try:
            r = S.get(url, timeout=40)
            if r.status_code == 200:
                return r.text
            log(f"  {r.status_code} {url}")
        except Exception as e:
            log(f"  error {e} {url}")
        time.sleep(3 * (i + 1))
    return ""


def urls_productos():
    idx = get(f"{BASE}/sitemap_index.xml")
    mapas = [m for m in re.findall(r"<loc>([^<]+)</loc>", idx) if "product-sitemap" in m]
    urls = []
    for m in mapas:
        urls += [u for u in re.findall(r"<loc>([^<]+)</loc>", get(m)) if "/producto/" in u]
    return list(dict.fromkeys(urls))


def precio(t):
    t = re.sub(r"[^\d,]", "", t or "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def leer(url):
    h = get(url)
    if not h:
        return {"url": url, "error": "no se pudo leer"}
    s = BeautifulSoup(h, "html.parser")
    meta = lambda p: (s.find("meta", attrs={"property": p}) or s.find("meta", attrs={"name": p}) or {}).get("content", "")
    h1 = s.find("h1")
    nombre = h1.get_text(" ", strip=True) if h1 else meta("og:title")
    texto = s.get_text(" ", strip=True)
    cod = re.search(r"C[ÓO]DIGO\s*:?\s*([A-Z0-9][A-Z0-9\-\./]{1,30})", texto, re.I)
    sku = (s.select_one(".sku") or {})
    codigo = (sku.get_text(strip=True) if hasattr(sku, "get_text") else "") or (cod.group(1) if cod else "")
    p = meta("product:price:amount")
    if not p:
        e = s.select_one(".summary .price .amount, .elementor-widget-woocommerce-product-price .amount, .price .amount")
        p = precio(e.get_text()) if e else None
    else:
        p = float(p)
    stock_el = s.select_one(".stock")
    stock_txt = stock_el.get_text(" ", strip=True) if stock_el else ""
    prod = s.select_one("div.product, .type-product")
    clases = " ".join(prod.get("class", [])) if prod else ""
    sin = bool(re.search(r"sin existencia|agotado|out of stock",
                         " ".join([stock_txt, meta("twitter:data2"), meta("product:availability")]), re.I)) or "outofstock" in clases
    fotos = []
    for a in s.select(".woocommerce-product-gallery__image"):
        u = a.get("data-large_image") or (a.find("a") or {}).get("href") or ((a.find("img") or {}).get("data-large_image"))
        if u:
            fotos.append(u)
    if not fotos and meta("og:image"):
        fotos = [meta("og:image")]
    cats = [x.get_text(strip=True) for x in s.select(".posted_in a")]
    desc = s.select_one("#tab-description, .woocommerce-Tabs-panel--description, .elementor-widget-woocommerce-product-content")
    corta = s.select_one(".woocommerce-product-details__short-description")
    pid = re.search(r"postid-(\d+)", " ".join(s.body.get("class", []))) if s.body else None
    return {"url": url, "id_web": pid.group(1) if pid else "", "codigo": codigo.strip().upper(), "nombre": nombre,
            "precio_web": p, "stock_web": "SIN EXISTENCIA" if sin else "DISPONIBLE", "texto_stock": stock_txt,
            "categorias": cats, "fotos": list(dict.fromkeys(fotos)),
            "descripcion_corta": corta.get_text("\n", strip=True) if corta else "",
            "descripcion": desc.get_text("\n", strip=True)[:4000] if desc else ""}


urls = urls_productos()
log(f"{len(urls)} productos en los sitemaps")
res = []
for k, u in enumerate(urls, 1):
    r = leer(u)
    res.append(r)
    log(f"[{k}/{len(urls)}] {r.get('codigo') or '¿sin código?'} | {r.get('stock_web')} | ${r.get('precio_web')} | {len(r.get('fotos') or [])} fotos | {r.get('nombre')}")
    time.sleep(0.7)
os.makedirs("salida", exist_ok=True)
json.dump(res, open("salida/delfabro_web.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
with open("salida/delfabro_web.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["CODIGO", "NOMBRE", "PRECIO WEB", "STOCK WEB", "CATEGORIAS", "CANT FOTOS", "URL", "FOTOS"])
    for r in res:
        w.writerow([r.get("codigo"), r.get("nombre"), r.get("precio_web"), r.get("stock_web"), " / ".join(r.get("categorias") or []),
                    len(r.get("fotos") or []), r["url"], " | ".join(r.get("fotos") or [])])
from collections import Counter
log(f"Resumen: {Counter(r.get('stock_web') for r in res)} | sin código: {sum(1 for r in res if not r.get('codigo'))}")
