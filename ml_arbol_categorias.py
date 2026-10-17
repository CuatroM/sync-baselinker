"""Solo lectura: árbol de categorías (hijos) a partir de las categorías de CATS."""
import json
import os

import requests

API = "https://api.mercadolibre.com"
s = requests.Session()
out = {}
for c in (os.environ.get("CATS") or "").split(","):
    c = c.strip()
    if not c:
        continue
    r = s.get(f"{API}/categories/{c}", timeout=30).json()
    padre = (r.get("path_from_root") or [{}])[-2]["id"] if len(r.get("path_from_root") or []) > 1 else c
    p = s.get(f"{API}/categories/{padre}", timeout=30).json()
    hijos = []
    for h in p.get("children_categories") or []:
        hh = s.get(f"{API}/categories/{h['id']}", timeout=30).json()
        hijos.append({"id": h["id"], "name": h["name"], "hoja": not hh.get("children_categories"),
                      "sub": [f"{x['id']} {x['name']}" for x in hh.get("children_categories") or []]})
    out[c] = {"padre": " > ".join(x["name"] for x in p.get("path_from_root") or []), "padre_id": padre, "hijos": hijos}
os.makedirs("salida", exist_ok=True)
json.dump(out, open("salida/arbol.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False)[:2000])
