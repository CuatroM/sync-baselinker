"""
Crea publicaciones nuevas en Mercado Libre a partir de las fichas FEMA.

Por cada SKU:
  - título (máx. 60), categoría, atributos de la ficha + obligatorios, descripción con el formato acordado
  - fotos desde la carpeta de Drive (SKU.jpg, SKU_02.jpg…) y la imagen de ficha técnica al final
  - precio = precio de BaseLinker (mayorista del Excel + 25 %) + MARGEN % encima; stock fijo STOCK (1)
  - solo crea en Mercado Libre: no escribe nada en BaseLinker (la vinculación se hace a mano en BASE)
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
_sk = os.environ["SKUS"].strip()
if _sk.startswith("@"):                      # @tanda1 -> datos/tanda1.json
    skus = json.load(open(f"datos/{_sk[1:]}.json", encoding="utf-8"))
else:
    skus = [s.strip() for s in _sk.split(",") if s.strip()]
margen = float(os.environ["MARGEN"].replace(",", ".").replace("%", ""))
tipo = os.environ.get("TIPO", "gold_special")            # gold_special = Clásica
envio = os.environ.get("ENVIO", "me2")
stock_fijo = int(os.environ.get("STOCK") or 1)

TITULOS = {   # títulos cuidados (máx. 60 caracteres); si no hay, se arma desde la ficha
    "69351050": "Aspiradora Industrial Polvo Y Liquido 60 L 1200w Fema Inox",
    "69351065": "Aspiradora Industrial Polvo Y Liquido 20 L 1400w Fema Inox",
    "69351110": "Aspiradora Industrial 80l 2000w 2 Motores Polvo Liquido Fema",
    "69503874": "Criquet Carro Hidraulico 2 Toneladas 125-300 Mm Fema",
    "69504110": "Guinche Pluma Plegable Hidraulico 2 Toneladas Taller Fema",
    "69503222": "Elevador De Autos 2 Columnas 4000 Kg 3hp 220v Traba Manual",
    "69503223": "Elevador De Autos 2 Columnas 4000 Kg 3hp 380v Traba Manual",
    "69350850": "Aparejo Electrico Fema 800 Eco 800 Kg 220v Doble Gancho 30m",
}
CATEGORIAS = {   # categoría forzada por familia (si no, la sugiere Mercado Libre)
    "Aspiradora Polvo y Líquido": "MLA371973",
    "Criquet Carro": "MLA433673",
}
# reglas por tipo de producto (sobre el título): (expresión, categoría fija o None, búsqueda para Mercado Libre)
REGLAS_CAT = [
    (r"^(Lija|HOJA Lija|Bandas Tela|Rollos Tela|Tela Esmeril|Espuma Abrasiva|Fibra Abrasiva|Rollos de Fibra"
     r"|Discos Speed Grip|Discos Speed Lok|Rueda Flap Lija)", "MLA384753", None),               # Lijas
    (r"^(Disco|Ruedas? |Rueda Convoluta|Clipper)", "MLA372119", None),                         # Discos y Piedras
    (r"^Lima (Rotativa|Plana|Media|Redonda|Triangular)|^Lima ", "MLA30832", None),             # Limas
    (r"^Cinta Embalar", "MLA405117", None),                                                      # Cintas de Embalaje
    (r"^Cinta (Doble|de Papel|Automotor|DUCT|Silver)|Enmascarar", "MLA105436", None),           # Cintas Adhesivas
    (r"^Silicona|^Sellador de (Roscas|Juntas)", "MLA435294", None),                              # Selladores y Siliconas
    (r"^Traba Roscas", None, "traba roscas anaerobico fijador de tornillos"),
    (r"^(Pan Afilado|Afilador|Afilado de Sierras)", None, "piedra de afilar herramientas"),
    (r"^Boina", None, "boina para pulidora"),
    (r"^(Soporte|Adaptador Soporte)", None, "plato soporte velcro para lijadora amoladora"),
    (r"^Bujes de Reduccion", None, "buje reduccion para piedra de amolar"),
    (r"^Filtro de Pintura", None, "colador filtro de pintura para soplete"),
    (r"^Sellador (Parabrisas|Carrocería)", None, "sellador poliuretano automotor"),
    (r"^Pasta para Pulir", None, "pasta para pulir automotor"),
    (r"^Gel Tekbond|^Kit Retrovisor", None, "adhesivo instantaneo"),
]
RAICES_OK = {"Herramientas", "Construcción", "Accesorios para Vehículos", "Industrias y Oficinas"}
CAT_NO = {"Camillas para Mecánicos", "Cortadoras de Concreto", "Cortadoras de Pelo", "Rollos de Embalaje",
          "Sensitivas", "Frascos con Gotero", "Strass Autoadhesivos", "Porta Cds y Dvds"}
GTIN = json.load(open("datos/gtin.json", encoding="utf-8")) if os.path.exists("datos/gtin.json") else {}
precio_minimo = float((os.environ.get("PRECIO_MINIMO") or "100000").replace(".", "").replace(",", "."))
_cat_cache = {}


def info_cat(cat):
    if cat not in _cat_cache:
        _cat_cache[cat] = ml.get(f"/categories/{cat}")
    return _cat_cache[cat]


def elegir_categoria(f, t):
    """Categoría: forzada por familia o por regla; si no, la primera sugerencia de ML que sea de herramientas."""
    if CATEGORIAS.get(f["familia"]):
        return CATEGORIAS[f["familia"]]
    consultas = [t]
    for exp, fija, q in REGLAS_CAT:
        if re.search(exp, t, re.I):
            if fija:
                return fija
            consultas = [q, t]
            break
    consultas.append(" ".join(f["nombre"].split()[:3]))
    for q in consultas:
        for d in ml.get("/sites/MLA/domain_discovery/search", q=q, limit=6) or []:
            ci = info_cat(d["category_id"])
            raiz = ((ci.get("path_from_root") or [{}])[0]).get("name")
            if raiz in RAICES_OK and ci.get("name") not in CAT_NO:
                return d["category_id"]
    return None

import glob
fichas = {}
for _arch in sorted(glob.glob("datos/fichas_*.json")):
    for _f in json.load(open(_arch, encoding="utf-8")):
        fichas.setdefault(_f["sku"], _f)
cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
b = cfg["baselinker"]

# ---------------- primero la autorización de Mercado Libre (el código vence en minutos)
ml = ML(os.environ.get("ML_CODIGO"))

# ---------------- precio y stock desde BaseLinker
bl = sync.BaseLinker(os.environ["BL_TOKEN"])
cat_bl = bl.catalogo(int(b["inventory_id"]))
por_sku = {sync.normalizar_codigo(p.get("sku")): p for p in cat_bl.values()}

# ---------------- IVA por SKU desde el Excel de FEMA (columna IMPUESTOS)
import io
import pandas as pd
_xls = pd.read_excel(io.BytesIO(sync.descargar_lista(cfg)), dtype=object)
_xls.columns = [str(c).strip().upper() for c in _xls.columns]
IVA = {sync.normalizar_codigo(r["SKU"]): r.get("IMPUESTOS") for _, r in _xls.iterrows()}
MULT = float(cfg.get("reglas", {}).get("multiplicador_precio", 1) or 1)
PRECIO_XLS = {sync.normalizar_codigo(r["SKU"]): sync.a_numero(r.get("PRECIO MAYORISTA")) for _, r in _xls.iterrows()}

# ---------------- datos extra por SKU (por nombre de atributo de Mercado Libre)
EXTRA = {
    "69504110": {"Tipo de grúa": "Plegable", "Requiere ensamblado": "Sí"},
    "69351050": {"Color": "Gris"}, "69351065": {"Color": "Gris"}, "69351110": {"Color": "Gris"},
}
COLORES = ["gris", "negro", "rojo", "azul", "amarillo", "naranja", "verde", "blanco", "plateado", "celeste",
           "marrón", "violeta", "rosa"]


def color_de(f, t):
    """Color más aproximado según título o ficha; si no hay ninguno, Blanco."""
    texto = (t + " " + f["nombre"] + " " + " ".join(f"{k} {v}" for k, v in f["specs"].items())).lower()
    for c in COLORES:
        if re.search(rf"\b{c}\b", texto):
            return "Gris" if c == "plateado" else c.capitalize()
    return "Blanco"
for linea in (os.environ.get("EXTRA") or "").split(";"):
    # formato: SKU|Atributo=Valor|Atributo=Valor ; SKU|...   o  *|Atributo=Valor para todos
    partes = [x.strip() for x in linea.split("|") if x.strip()]
    if len(partes) > 1:
        EXTRA.setdefault(partes[0], {}).update(dict(x.split("=", 1) for x in partes[1:] if "=" in x))

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


def texto_ml(t):
    for a, b2 in (("≤", "hasta "), ("≥", "desde "), ("±", "+/-"), ("’", "'"), ("“", '"'), ("”", '"'),
                  ("–", "-"), ("—", "-"), ("×", "x")):
        t = t.replace(a, b2)
    return t


def titulo(f):
    t = TITULOS.get(f["sku"]) or f.get("titulo") or f["nombre"]
    if len(t) > 60:
        t = t[:60].rsplit(" ", 1)[0]
    return t


res = []
for sku in skus:
    f = fichas.get(sku)
    fila = {"sku": sku}
    try:
        if not f:
            raise RuntimeError("no hay ficha cargada para este SKU")
        # regla: solo se publica lo que existe en la lista de precios de FEMA (de donde salen precio y stock)
        if sync.normalizar_codigo(sku) not in PRECIO_XLS:
            fila["resultado"] = "EXCLUIDO: no figura en la lista de precios de FEMA"
            log(f"{sku}: {fila['resultado']}")
            res.append(fila)
            continue
        # ¿ya publicado?
        ya = []
        for est in ("active", "paused"):
            ya += ml.get(f"/users/{ml.user_id}/items/search", seller_sku=sku + SUFIJO, status=est).get("results") or []
        if ya:
            r = ml.pedir("GET", f"/items/{ya[0]}/description")
            if r.status_code == 404 or not (r.json().get("plain_text") or "").strip():
                if dry:
                    fila["resultado"] = f"YA EXISTE {ya[0]} — se cargaría la descripción que falta"
                else:
                    rd = ml.pedir("POST", f"/items/{ya[0]}/description", json={"plain_text": texto_ml(fm.descripcion(f))})
                    if rd.status_code not in (200, 201):
                        rd = ml.pedir("PUT", f"/items/{ya[0]}/description", params={"api_version": 2},
                                      json={"plain_text": texto_ml(fm.descripcion(f))})
                    fila["resultado"] = (f"YA EXISTE {ya[0]} — descripción cargada" if rd.status_code in (200, 201)
                                         else f"ERROR: descripción de {ya[0]}: {rd.status_code} {rd.text[:300]}")
                fila["publicacion"] = ya[0]
                log(f"{sku}: {fila['resultado']}")
                res.append(fila)
                continue
            raise RuntimeError(f"ya existe publicación con este SKU: {ya}")
        p = por_sku.get(sync.normalizar_codigo(sku + SUFIJO))
        base = float(((p or {}).get("prices") or {}).get(str(b["price_group_id"])) or 0)
        origen = "BaseLinker"
        if base <= 0:   # no está en BaseLinker: mismo cálculo desde el Excel de FEMA (mayorista × 1,25)
            m = PRECIO_XLS.get(sync.normalizar_codigo(sku))
            base, origen = (round(m * MULT, 2), "Excel FEMA") if m else (0, "")
        if base <= 0:
            raise RuntimeError("sin precio: no está en BaseLinker ni en el Excel de FEMA")
        precio = round(base * (1 + margen / 100), 2)
        t = titulo(f)
        cat = elegir_categoria(f, t)
        if not cat:
            raise RuntimeError("Mercado Libre no sugirió una categoría de herramientas/construcción/vehículos")
        cat_info = info_cat(cat)
        cat_attrs = [{"id": a.get("id"), "name": a.get("name"), "value_type": a.get("value_type"),
                      "tags": a.get("tags"), "allowed_units": [u.get("id") for u in a.get("allowed_units") or []],
                      "values": [{"id": v.get("id"), "name": v.get("name")} for v in a.get("values") or []]}
                     for a in ml.get(f"/categories/{cat}/attributes")]
        attrs = fm.atributos_para(f, cat_attrs)
        attrs.update({"BRAND": f.get("marca") or "FEMA", "MODEL": f["specs"].get("Modelo") or sku, "SELLER_SKU": sku + SUFIJO,
                      "PART_NUMBER": sku, "MPN": sku})
        if GTIN.get(sku):
            attrs["GTIN"] = GTIN[sku]["gtin"]
            fila["gtin"] = GTIN[sku]["gtin"]
        else:
            attrs["EMPTY_GTIN_REASON"] = "El producto no tiene código registrado"
        iva = sync.a_numero(IVA.get(sync.normalizar_codigo(sku)))
        if iva is not None:
            attrs["VALUE_ADDED_TAX"] = f"{fm.fmt(iva)} %"
        attrs["IMPORT_DUTY"] = "0 %"
        por_nombre = {a["name"].lower(): a for a in cat_attrs}
        extra = {**EXTRA.get("*", {}), **EXTRA.get(sku, {})}
        if "COLOR" in {a["id"] for a in cat_attrs} and not any(k.lower() == "color" for k in extra):
            extra["Color"] = color_de(f, t)
        for nom, val in extra.items():
            a = por_nombre.get(nom.strip().lower())
            if not a:
                continue
            opciones = [v["name"] for v in a.get("values") or []]
            elegido = next((o for o in opciones if o.lower() == val.lower()), None) or \
                next((o for o in opciones if val.lower() in o.lower()), None) or val
            attrs[a["id"]] = elegido
        ids_cat = {a["id"] for a in cat_attrs}
        atributos = [{"id": k, "value_name": v} for k, v in attrs.items() if k in ids_cat]
        faltan = [a["name"] for a in cat_attrs if (a.get("tags") or {}).get("required")
                  and a["id"] not in attrs and a["id"] not in ("ITEM_CONDITION",)]
        imgs = imagenes(sku)
        if not [x for x in imgs if "_ficha" not in x["nombre"].lower()]:
            # producto sin foto propia: imagen genérica de la caja FEMA (+ ficha si existe)
            caja = [x for x in fotos if os.path.splitext(x["nombre"])[0].lower() == "caja_fema"
                    and os.path.splitext(x["nombre"])[1].lower() in EXT]
            imgs = caja[:1] + imgs
        if not imgs:
            raise RuntimeError("no hay fotos en Drive para este SKU")
        fila["iva"] = attrs.get("VALUE_ADDED_TAX")
        fila["origen_precio"] = origen
        fila["opciones_obligatorios"] = " || ".join(
            f"{a['name']}: {', '.join(v['name'] for v in (a.get('values') or [])[:15])}"
            for a in cat_attrs if a["name"] in faltan)
        fila.update({"titulo": t, "categoria": f"{cat} {cat_info.get('name')}", "precio_base": base,
                     "precio": precio, "stock": stock_fijo, "fotos": " | ".join(x["nombre"] for x in imgs),
                     "atributos": len(atributos), "obligatorios_sin_dato": ", ".join(faltan)})
        item = {"title": t, "category_id": cat, "price": precio, "currency_id": "ARS",
                "available_quantity": stock_fijo, "buying_mode": "buy_it_now", "condition": "new",
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

        def enviar(ruta):
            r = ml.pedir("POST", ruta, json=item)
            if r.status_code not in (200, 201, 204) and "family_name" in r.text:
                item["family_name"] = item.pop("title")
                r = ml.pedir("POST", ruta, json=item)
            if r.status_code not in (200, 201, 204) and "minimum of price" in r.text and item["price"] < precio_minimo:
                # la categoría exige un precio mínimo: se sube al precio de resguardo acordado
                item["price"] = precio_minimo
                fila["precio"] = f"{precio_minimo:.2f} (subido al mínimo; calculado {precio})"
                r = ml.pedir("POST", ruta, json=item)
            return r

        if dry:
            # en simulación se sube solo la primera foto (no queda asociada a ninguna publicación)
            item["pictures"] = [x for x in [subir(imgs[0])] if x]
            r = enviar("/items/validate")
            if r.status_code in (200, 204):
                fila["resultado"] = "VALIDADO OK"
            else:
                causas = (r.json().get("cause") or []) if r.headers.get("content-type", "").startswith("application/json") else []
                errores = [c.get("message") for c in causas if c.get("type") == "error"]
                avisos = [c.get("message") for c in causas if c.get("type") != "error"]
                fila["resultado"] = ("VALIDACIÓN CON ERRORES: " + " / ".join(errores) if errores else "VALIDADO OK") + \
                    (" | AVISOS: " + " / ".join(avisos) if avisos else "")
        else:
            item["pictures"] = [x for x in (subir(i) for i in imgs) if x]
            r = enviar("/items")
            if r.status_code not in (200, 201):
                raise RuntimeError(f"Mercado Libre rechazó la publicación: {r.status_code} {r.text[:600]}")
            nuevo = r.json()
            iid = nuevo["id"]
            ml.put(f"/items/{iid}", {"status": "paused"})
            rd = ml.pedir("POST", f"/items/{iid}/description", json={"plain_text": texto_ml(fm.descripcion(f))})
            fila.update({"publicacion": iid, "link": nuevo.get("permalink"),
                         "resultado": "CREADA (pausada)" + ("" if rd.status_code in (200, 201) else
                                                            f" — descripción falló {rd.status_code}: {rd.text[:200]}")})
    except Exception as e:
        fila["resultado"] = f"ERROR: {e}"
    log(f"{sku}: {fila.get('resultado')}")
    res.append(fila)

os.makedirs("salida", exist_ok=True)
cols = ["sku", "resultado", "publicacion", "link", "titulo", "categoria", "gtin", "origen_precio", "precio_base", "precio", "stock",
        "iva", "fotos", "atributos", "obligatorios_sin_dato", "opciones_obligatorios"]
with open("salida/ml-publicar-resultado.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=cols, delimiter=";", extrasaction="ignore")
    w.writeheader()
    w.writerows(res)
for r in res:
    print(json.dumps(r, ensure_ascii=False))
sys.exit(1 if any(str(r.get("resultado", "")).startswith("ERROR") for r in res) else 0)
