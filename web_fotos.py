"""Solo lectura: baja una página web y guarda las imágenes de producto que encuentra (para revisarlas)."""
import os
import re
from urllib.parse import urljoin

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
url = os.environ["URL"]
os.makedirs("salida", exist_ok=True)
r = requests.get(url, headers=UA, timeout=60)
print(r.status_code, url)
html = r.text
open("salida/pagina.html", "w", encoding="utf-8").write(html)
cands = set()
for m in re.finditer(r'(?:src|data-src|data-zoom-image|data-large|href|content)=["\']([^"\']+\.(?:jpe?g|png|webp)(?:\?[^"\']*)?)["\']', html, re.I):
    cands.add(urljoin(url, m.group(1)))
for m in re.finditer(r'srcset=["\']([^"\']+)["\']', html, re.I):
    for p in m.group(1).split(","):
        cands.add(urljoin(url, p.strip().split(" ")[0]))
from urllib.parse import urlsplit
raiz = "{0.scheme}://{0.netloc}/".format(urlsplit(url))
cands |= {raiz + c.split("/mods/", 1)[1].join(["mods/", ""]) for c in list(cands) if "/mods/" in c}
excl = re.compile(r"logo|banner|icon|refresh|sprite|flag|payment|tarjeta|whatsapp", re.I)
fotos = sorted(c for c in cands if not excl.search(c))
with open("salida/imagenes.txt", "w") as f:
    f.write("\n".join(fotos))
for i, c in enumerate(fotos[:30]):
    try:
        d = requests.get(c, headers=UA, timeout=60)
        ext = os.path.splitext(c.split("?")[0])[1] or ".jpg"
        if d.status_code == 200 and len(d.content) > 3000:
            open(f"salida/img_{i:02d}{ext}", "wb").write(d.content)
            print("OK", len(d.content), c)
    except Exception as e:
        print("ERR", c, e)
