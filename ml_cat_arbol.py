"""Solo lectura: recorre el árbol de categorías de Mercado Libre (raíces Herramientas e Industrias y Oficinas)
y lista las categorías cuyo nombre coincide con BUSCAR (expresión regular). Sirve para elegir categorías fijas."""
import os, re, time, csv
import requests

API = "https://api.mercadolibre.com"
RAICES = [x.strip() for x in (os.environ.get("RAICES") or "Herramientas,Industrias y Oficinas").split(",")]
BUSCAR = re.compile(os.environ.get("BUSCAR") or ".", re.I)
S = requests.Session()


def get(u):
    for i in range(4):
        r = S.get(API + u, timeout=30)
        if r.status_code == 200:
            return r.json()
        time.sleep(2 * (i + 1))
    return {}


raices = [c for c in get("/sites/MLA/categories") if c["name"] in RAICES]
filas, pend = [], [(c["id"], c["name"]) for c in raices]
while pend:
    cid, ruta = pend.pop(0)
    d = get(f"/categories/{cid}")
    hijos = d.get("children_categories") or []
    if BUSCAR.search(d.get("name", "")):
        filas.append([cid, ruta, "hoja" if not hijos else f"{len(hijos)} hijos"])
    pend += [(h["id"], ruta + " > " + h["name"]) for h in hijos]
for f in filas:
    print(" | ".join(f), flush=True)
os.makedirs("salida", exist_ok=True)
with open("salida/categorias.csv", "w", newline="", encoding="utf-8-sig") as fh:
    csv.writer(fh, delimiter=";").writerows([["ID", "RUTA", "TIPO"]] + filas)
print(f"Listo: {len(filas)} categorías")
