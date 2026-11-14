"""
Busca (solo lectura) datos técnicos de productos Delfabro en el catálogo de Mercado Libre (/products/search),
para completar nuestras fichas. La búsqueda pública de publicaciones (/sites/MLA/search) está bloqueada (403)
para nuestra aplicación, así que usamos el catálogo de productos. No modifica nada.
Salida: salida/delfabro_ml.json y salida/delfabro_ml.csv
"""
import csv
import json
import os
import re
import time

from ml.cliente import ML, log

ml = ML(os.environ.get("ML_CODIGO"))
F = json.load(open("datos/delfabro_fichas.json", encoding="utf-8"))
P = json.load(open("datos/delfabro_publicadas.json", encoding="utf-8"))
skus = [s.strip() for s in (os.environ.get("SKUS") or "").split(",") if s.strip()] or list(P)
fichas = {f["sku"]: f for f in F}


def consulta(t):
    t = re.sub(r"\bDelfabro\b", "", t, flags=re.I)
    return "Delfabro " + " ".join(t.split()[:6])


salida = []
for k, s in enumerate(skus, 1):
    f = fichas[s]
    fila = {"sku": s, "titulo": f["titulo"], "consulta": consulta(f["titulo"]), "resultados": []}
    for q in (fila["consulta"], f"Delfabro {s}"):
        r = ml.pedir("GET", "/products/search", params={"site_id": "MLA", "status": "active", "q": q, "limit": 10})
        if r.status_code != 200:
            fila["error"] = f"{r.status_code} {r.text[:150]}"
            continue
        for p in r.json().get("results") or []:
            attrs = {a.get("name"): a.get("value_name") for a in p.get("attributes") or [] if a.get("value_name")}
            if "delfabro" not in (attrs.get("Marca", "") + " " + (p.get("name") or "")).lower():
                continue
            if any(x["id"] == p["id"] for x in fila["resultados"]):
                continue
            fila["resultados"].append({"id": p["id"], "nombre": p.get("name"), "atributos": attrs,
                                       "descripcion": ((p.get("short_description") or {}).get("content") or "")[:3000]})
        time.sleep(0.2)
    salida.append(fila)
    log(f"[{k}/{len(skus)}] {s} {f['titulo'][:45]}: {len(fila['resultados'])} en catálogo {fila.get('error', '')}")
os.makedirs("salida", exist_ok=True)
json.dump(salida, open("salida/delfabro_ml.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
with open("salida/delfabro_ml.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh, delimiter=";")
    w.writerow(["SKU", "TITULO", "CANT RESULTADOS", "PRIMERO", "ATRIBUTOS"])
    for x in salida:
        p = x["resultados"][0] if x["resultados"] else {}
        w.writerow([x["sku"], x["titulo"], len(x["resultados"]), p.get("nombre", ""), len(p.get("atributos") or {})])
log(f"Listo: {sum(1 for x in salida if x['resultados'])} de {len(salida)} con datos en el catálogo de ML")
