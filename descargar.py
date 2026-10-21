"""Solo lectura: baja archivos públicos (URLS separadas por espacio) a salida/ para revisarlos."""
import os
import requests
from urllib.parse import unquote
os.makedirs("salida", exist_ok=True)
for u in (os.environ.get("URLS") or "").split():
    r = requests.get(u, timeout=180, headers={"User-Agent": "Mozilla/5.0"})
    n = unquote(u.split("?")[0].rsplit("/", 1)[-1]).replace(" ", "_")
    open(f"salida/{n}", "wb").write(r.content)
    print(r.status_code, len(r.content), n)
