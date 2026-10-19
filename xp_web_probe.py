"""Solo lectura: prueba de la web de EXTRAPOWER. Para cada código de CODIGOS busca en el sitio y guarda
la página de búsqueda y la del primer producto (salida/*.html) para estudiar la estructura."""
import os
import re
from urllib.parse import quote

import requests

WEB = "https://www.extrapowersa.com.ar"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
s = requests.Session()
s.headers.update(UA)
os.makedirs("salida", exist_ok=True)
for c in (os.environ.get("CODIGOS") or "B420").split(","):
    c = c.strip()
    u = f"{WEB}/catalogsearch/result/?q={quote(c)}"
    r = s.get(u, timeout=60)
    open(f"salida/busq_{c}.html", "w", encoding="utf-8").write(r.text)
    links = []
    for m in re.finditer(r'<h3 class="product-name">\s*<a href="([^"]+)"', r.text):
        links.append(m.group(1))
    if not links:
        links = sorted(set(re.findall(rf'href="({re.escape(WEB)}/[a-z0-9\-]+\.html)"', r.text)))[:5]
    imgs = re.findall(r'(https?://[^"\']+/media/catalog/product/[^"\']+\.(?:jpe?g|png))', r.text)
    print(c, r.status_code, "links:", links[:5], "| imgs:", [i.rsplit("/", 1)[-1] for i in imgs[:6]])
    if links:
        p = s.get(links[0], timeout=60)
        open(f"salida/prod_{c}.html", "w", encoding="utf-8").write(p.text)
        sku = re.findall(r'(?i)(?:sku|c[oó]digo)[^<]{0,40}<[^>]+>\s*([A-Z0-9\-/]+)', p.text)[:3]
        gal = sorted(set(re.findall(r'(https?://[^"\']+/media/catalog/product/[^"\']+\.(?:jpe?g|png))', p.text)))
        print("   prod", p.status_code, links[0], "| sku?", sku, "| fotos:", [g.split("/media/catalog/product/")[1] for g in gal][:8])
    for ruta in (f"/media/catalog/product/{c[0]}/{c[1]}/{c}.jpg",):
        h = s.head(WEB + ruta, timeout=30)
        print("   directo", ruta, h.status_code, h.headers.get("content-length"))
