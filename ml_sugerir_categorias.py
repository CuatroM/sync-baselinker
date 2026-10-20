"""Solo lectura, sin autorización: ruta de categorías y sugerencias de ML para los títulos de datos/republicar.json."""
import json
import os

import requests

API = "https://api.mercadolibre.com"
s = requests.Session()
out = {"rutas": {}, "sugerencias": {}}
for c in ["MLA454954"]:
    r = s.get(f"{API}/categories/{c}", timeout=30)
    out["rutas"][c] = [p["name"] for p in (r.json().get("path_from_root") or [])] if r.ok else r.status_code
for x in json.load(open(os.environ.get("LISTA") or "datos/republicar.json", encoding="utf-8")):
    r = s.get(f"{API}/sites/MLA/domain_discovery/search", params={"q": x["titulo"], "limit": 8}, timeout=30)
    sug = []
    if r.ok:
        for d in r.json():
            rc = s.get(f"{API}/categories/{d['category_id']}", timeout=30)
            ruta = " > ".join(p["name"] for p in (rc.json().get("path_from_root") or [])) if rc.ok else ""
            sug.append(f"{d['category_id']} | {ruta}")
    else:
        sug.append(f"error {r.status_code} {r.text[:120]}")
    out["sugerencias"][f"{x['sku']} | {x['titulo']}"] = sug
os.makedirs("salida", exist_ok=True)
json.dump(out, open("salida/sugerencias.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1)[:3000])
