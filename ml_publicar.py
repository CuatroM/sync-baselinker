"""
Crea publicaciones nuevas en Mercado Libre a partir de las fichas FEMA.

Por cada SKU:
  - título (máx. 60), categoría, atributos de la ficha + obligatorios, descripción con el formato acordado
  - fotos desde la carpeta de Drive (SKU.jpg, SKU_02.jpg…) y la imagen de ficha técnica al final
  - precio = precio de BaseLinker (inventario COMPLETO FEMA) + MARGEN %, stock de BaseLinker
  - SKU = CÓDIGO#FEMA, publicación Clásica, Mercado Envíos, queda PAUSADA para revisar
DRY_RUN=1: arma todo y lo valida contra Mercado Libre (/items/validate) sin crear nada.
"""
import csv
import json
import os
import re
import sys

import yaml

import sync
from drive.carpeta import descargar, listar
from ml.cliente import ML, log
from ml import fichas_map as fm

SUFIJO = "#FEMA"
dry = os.environ.get("DRY_RUN", "1") == "1"
skus = [s.strip() for s in os.environ["SKUS"].split(",") if s.strip()]
margen = float(os.environ["MARGEN"].replace(",", ".").replace("%", ""))
tipo = os.environ.get("TIPO", "gold_special")            # gold_special = Clásica
envio = os.environ.get("ENVIO", "me2")

TITULOS = {   # títulos cuidados (máx. 60 caracteres); si no hay, se arma desde la ficha
    "69351050": "Aspiradora Industrial Polvo Y Liquido 60 L 1200w Fema Inox",
    "69351065": "Aspiradora Industrial Polvo Y Liquido 20 L 1400w Fema Inox",
    "69351110": "Aspiradora Industrial 80l 2000w 2 Motores Polvo Liquido Fema",
    "69503874": "Criquet Carro Hidraulico 2 Toneladas 125-300 Mm Fema",
    "69504110": "Guinche Pluma Plegable Hidraulico 2 Toneladas Taller Fema",
}
CATEGORIAS = {   # categoría forzada por familia (si no, la sugiere Mercado Libre)
    "Aspiradora Polvo y Líquido": "MLA371973",
    "Criquet Carro": "MLA433673",
}

fichas = {f["sku"]: f for f in json.load(open("datos/fichas_fema.json", encoding="utf-8"))}
cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
b = cfg["baselinker"]

# ---------------- precio y stock desde BaseLinker
bl = sync.BaseLinker(os.environ["BL_TOKEN"])
cat_bl = bl.catalogo(int(b["inventory_id"]))
por_sku = {sync.normalizar_codigo(p.get("sku")): p for p in cat_bl.values()}

# ---------------- fotos y fichas en Drive
fotos = [x for x in listar(os.environ["DRIVE_FOTOS"], profundidad=0) if not x["carpeta"]]
fichas_img = [x for x in listar(os.environ["DRIVE_FICHAS"], profundidad=0) if not x["carpeta"]]
EXT = (".jpg", ".jpeg", ".png", ".webp")


def imagenes(sku):
    def ok(n, extra=""):
        base, ext = os.path.splitext(n)
        return ext.lower() in EXT and re.fullmatch(rf"{re.escape(sku)}{extra}", base, re.I)
    prod = [x for x in fotos if ok(x["nombre"]) or ok(x["nombre"], r"[_\- ]\(?[0-9a-z]{1,3}\)?")]
    vistos, unicas = set(), []
    for x in sorted(prod, key=lambda x: (os.path.splitext(x["nombre"])[0].lower() != sku.lower(), x["nombre"])):
        base = os.path.splitext(x["nombre"])[0].lower()
        if base not in vistos:                     # si hay .jpg y .png con el mismo nombre, uno solo
            vistos.add(base)
            unicas.append(x)
    ficha = [x for x in fichas_img if os.path.splitext(x["nombre"])[1].lower() in EXT
             and re.fullmatch(rf"{re.escape(sku)}_ficha(nueva)?", os.path.splitext(x["nombre"])[0], re.I)]
    return unicas[:9] + ficha[:1]


def titulo(f):
    t = TITULOS.get(f["sku"]) or f["nombre"]
    if len(t) > 60:
        t = t[:60].rsplit(" ", 1)[0]
    return t


ml = ML(os.environ.get("ML_CODIGO"))
res = []
for sku in skus:
    f = fichas.get(sku)
    fila = {"sku": sku}
    try:
        if not f:
            raise RuntimeError("no hay ficha cargada para este SKU")
        # ¿ya publicado?
        ya = []
        for est in ("active", "paused"):
            ya += ml.get(f"/users/{ml.user_id}/items/search", seller_sku=sku + SUFIJO, status=est).get("results") or []
        if ya:
            raise RuntimeError(f"ya existe publicación con este SKU: {ya}")
        p = por_sku.get(sync.normalizar_codigo(sku + SUFIJO))
        if not p:
            raise RuntimeError("el SKU no está en el inventario de BaseLinker")
        base = float((p.get("prices") or {}).get(str(b["price_group_id"])) or 0)
        stock = int((p.get("stock") or {}).get(str(b["warehouse_id"])) or 0)
        if base <= 0:
            raise RuntimeError("sin precio en BaseLinker")
        precio = round(base * (1 + margen / 100), 2)
        t = titulo(f)
        cat = CATEGORIAS.get(f["familia"])
        if not cat:
            d = ml.get("/sites/MLA/domain_discovery/search", q=t, limit=1)
            cat = d[0]["category_id"] if d else None
        if not cat:
            raise RuntimeError("Mercado Libre no sugirió categoría")
        cat_info = ml.get(f"/categories/{cat}")
        cat_attrs = [{"id": a.get("id"), "name": a.get("name"), "value_type": a.get("value_type"),
                      "tags": a.get("tags"), "allowed_units": [u.get("id") for u in a.get("allowed_units") or []],
                      "values": [{"id": v.get("id"), "name": v.get("name")} for v in a.get("values") or []]}
                     for a in ml.get(f"/categories/{cat}/attributes")]
        attrs = fm.atributos_para(f, cat_attrs)
        attrs.update({"BRAND": "FEMA", "MODEL": f["specs"].get("Modelo") or sku, "SELLER_SKU": sku + SUFIJO,
                      "EMPTY_GTIN_REASON": "El producto no tiene código registrado"})
        ids_cat = {a["id"] for a in cat_attrs}
        atributos = [{"id": k, "value_name": v} for k, v in attrs.items() if k in ids_cat]
        faltan = [a["name"] for a in cat_attrs if (a.get("tags") or {}).get("required")
                  and a["id"] not in attrs and a["id"] not in ("ITEM_CONDITION",)]
        imgs = imagenes(sku)
        if not imgs:
            raise RuntimeError("no hay fotos en Drive para este SKU")
        fila.update({"titulo": t, "categoria": f"{cat} {cat_info.get('name')}", "precio_base": base,
                     "precio": precio, "stock": stock, "fotos": " | ".join(x["nombre"] for x in imgs),
                     "atributos": len(atributos), "obligatorios_sin_dato": ", ".join(faltan)})
        item = {"title": t, "category_id": cat, "price": precio, "currency_id": "ARS",
                "available_quantity": max(stock, 1), "buying_mode": "buy_it_now", "condition": "new",
                "listing_type_id": tipo, "attributes": atributos,
                "sale_terms": [{"id": "WARRANTY_TYPE", "value_name": "Garantía de fábrica"},
                               {"id": "WARRANTY_TIME", "value_name": "6 meses"}],
                "shipping": {"mode": envio, "local_pick_up": False, "free_shipping": False}}
        def subir(x):
            up = ml.pedir("POST", "/pictures/items/upload", files={"file": (x["nombre"], descargar(x["id"]))})
            if up.status_code in (200, 201):
                return {"id": up.json()["id"]}
            log(f"{sku}: no pude subir {x['nombre']}: {up.status_code} {up.text[:200]}")
            return None

        if dry:
            # en simulación se sube solo la primera foto (no queda asociada a ninguna publicación)
            item["pictures"] = [x for x in [subir(imgs[0])] if x]
            r = ml.pedir("POST", "/items/validate", json=item)
            if r.status_code not in (200, 204) and "family_name" in r.text:
                item["family_name"] = item.pop("title")
                r = ml.pedir("POST", "/items/validate", json=item)
            fila["resultado"] = "VALIDADO OK" if r.status_code in (200, 204) else f"VALIDACIÓN: {r.status_code} {r.text[:600]}"
        else:
            item["pictures"] = [x for x in (subir(i) for i in imgs) if x]
            r = ml.pedir("POST", "/items", json=item)
            if r.status_code not in (200, 201) and "family_name" in r.text:
                item["family_name"] = item.pop("title")
                r = ml.pedir("POST", "/items", json=item)
            if r.status_code not in (200, 201):
                raise RuntimeError(f"Mercado Libre rechazó la publicación: {r.status_code} {r.text[:600]}")
            nuevo = r.json()
            iid = nuevo["id"]
            ml.put(f"/items/{iid}", {"status": "paused"})
            rd = ml.pedir("POST", f"/items/{iid}/description", json={"plain_text": fm.descripcion(f)})
            fila.update({"publicacion": iid, "link": nuevo.get("permalink"),
                         "resultado": "CREADA (pausada)" + ("" if rd.status_code in (200, 201) else
                                                            f" — descripción falló {rd.status_code}")})
    except Exception as e:
        fila["resultado"] = f"ERROR: {e}"
    log(f"{sku}: {fila.get('resultado')}")
    res.append(fila)

os.makedirs("salida", exist_ok=True)
cols = ["sku", "resultado", "publicacion", "link", "titulo", "categoria", "precio_base", "precio", "stock",
        "fotos", "atributos", "obligatorios_sin_dato"]
with open("salida/ml-publicar-resultado.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=cols, delimiter=";", extrasaction="ignore")
    w.writeheader()
    w.writerows(res)
for r in res:
    print(json.dumps(r, ensure_ascii=False))
sys.exit(1 if any(str(r.get("resultado", "")).startswith("ERROR") for r in res) else 0)
