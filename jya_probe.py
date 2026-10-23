"""Solo lectura: prueba el listado JSON de la tienda J&A (/v4/product)."""
import json, re, requests
s = requests.Session(); s.headers["User-Agent"] = "Mozilla/5.0"
h = s.get("https://www.jyadistribuidora.ar/productos", timeout=40).text
tok = re.search(r'csrf_token\s*=\s*["\']([^"\']+)', h) or re.search(r'name="csrf-token" content="([^"]+)"', h)
print("token:", bool(tok))
r = s.get("https://www.jyadistribuidora.ar/v4/product", params={"filter_page": 1, "filter_order": 0},
          headers={"X-CSRF-TOKEN": tok.group(1) if tok else "", "X-Requested-With": "XMLHttpRequest"}, timeout=40)
print(r.status_code, r.text[:3000])
