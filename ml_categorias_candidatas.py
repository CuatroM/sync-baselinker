"""
Solo lectura: para cada publicación de LISTA (por defecto datos/republicar_pendientes.json) busca
categorías candidatas en ML (domain_discovery con el título completo y con una versión corta sin marcas ni códigos)
y anota el estado actual de la publicación vieja. Salida: salida/categorias_candidatas.json y .csv
"""
import csv
import json
import os
import re
import time

from ml.cliente import ML, log

ml = ML(os.environ.get("ML_CODIGO"))
lista = json.load(open((os.environ.get("LISTA") or "datos/republicar_pendientes.json").strip(), encoding="utf-8"))
MARCAS = r"\b(fema|power|black jack|extra power|briggs|stratton|oleo ?mac|husqvarna|stihl|jonsered|poulan|homelite|mtd|castor|omega|" \
         r"trendy|oreiro|ona saez|amayra|wanderlust|skora|la chapelle|simillino|vertice|toy syory|clandy|dsm|wero|pepi|dealer|richi|" \
         r"volcan|stan|mg|qba|honda|repuesto|original|accesorio)\b"
nombres = {}


def ruta(c):
    if c not in nombres:
        r = ml.pedir("GET", f"/categories/{c}")
        nombres[c] = " > ".join(x["name"] for x in (r.json().get("path_from_root") or [])) if r.status_code == 200 else c
    return nombres[c]


def corto(t):
    t = re.sub(MARCAS, " ", t, flags=re.I)
    t = re.sub(r"\b[A-Z]*\d[\w\-./]*\b", " ", t)       # códigos y medidas
    return " ".join(t.split()[:5])


def candidatas(q):
    r = ml.pedir("GET", "/sites/MLA/domain_discovery/search", params={"q": q, "limit": 5})
    if r.status_code != 200:
        return []
    return [(x["category_id"], x.get("category_name")) for x in r.json()]


salida = []
for k, x in enumerate(lista, 1):
    estado = ""
    r = ml.pedir("GET", f"/items/{x['item']}", params={"attributes": "status,sub_status,category_id"})
    if r.status_code == 200:
        j = r.json()
        estado = f"{j.get('status')}/{','.join(j.get('sub_status') or [])}"
    vistos, cands = set(), []
    for q in (corto(x["titulo"]), x["titulo"]):
        for c, n in candidatas(q):
            if c not in vistos:
                vistos.add(c)
                cands.append({"id": c, "ruta": ruta(c), "consulta": q})
    salida.append({**x, "estado_vieja": estado, "consulta_corta": corto(x["titulo"]), "candidatas": cands})
    log(f"[{k}/{len(lista)}] {x['sku']} {estado} | {x['titulo'][:50]} -> {[c['id'] for c in cands]}")
    time.sleep(0.2)
os.makedirs("salida", exist_ok=True)
json.dump(salida, open("salida/categorias_candidatas.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
with open("salida/categorias_candidatas.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["SKU", "PUBLICACION", "ESTADO", "TITULO", "CATEGORIA ACTUAL", "SUGERIDA ANTES", "CANDIDATAS"])
    for s in salida:
        w.writerow([s["sku"], s["item"], s["estado_vieja"], s["titulo"], ruta(s["cat_actual"]), s.get("cat_nombre"),
                    " || ".join(f"{c['id']} {c['ruta']}" for c in s["candidatas"])])
log("Listo")
