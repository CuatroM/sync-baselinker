"""Solo lectura: recorre el árbol de categorías de Mercado Libre (raíces Herramientas e Industrias y Oficinas)
y lista las categorías cuyo nombre coincide con BUSCAR (expresión regular). Sirve para elegir categorías fijas."""
import os, re, time, csv
import requests
from ml.cliente import ML

API = "https://api.mercadolibre.com"
RAICES = [x.strip() for x in (os.environ.get("RAICES") or "Herramientas,Industrias y Oficinas").split(",")]
BUSCAR = re.compile(os.environ.get("BUSCAR") or ".", re.I)
ml = ML(os.environ.get("ML_CODIGO"))
SEMILLAS = ["MLA455290", "MLA431110", "MLA413520", "MLA1500"]   # categorías conocidas: de ahí salen las raíces


def get(u):
    r = ml.pedir("GET", u, reintentos=3)
    if r.status_code != 200:
        print(f"  {r.status_code} {u}", flush=True)
        return {}
    return r.json()


raices, vistos = [], set()
for c in get("/sites/MLA/categories") or []:
    if c["name"] in RAICES:
        raices.append(c)
for sid in SEMILLAS:   # por si el listado de raíces no está disponible
    for c in (get(f"/categories/{sid}").get("path_from_root") or [])[:1]:
        if c["id"] not in {x["id"] for x in raices} and (c["name"] in RAICES or not raices):
            raices.append(c)
print("Raíces:", [(c["id"], c["name"]) for c in raices], flush=True)
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
