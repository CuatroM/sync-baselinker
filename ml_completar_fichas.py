"""
Completa en Mercado Libre atributos de ficha técnica que se pueden deducir sin inventar datos,
en las publicaciones #FEMA (activas y pausadas). Solo carga atributos VACÍOS; nunca pisa uno cargado.
Reglas:
  - Formato de venta = "Unidad" (se vende por unidad).
  - "Cantidad de …" / "Unidades por …" = 1, salvo que el título indique juego, kit, set, pack, par o "x N".
  - Código OEM = código de la pieza (el SKU sin #FEMA), solo en categorías que piden ese dato.
  - Voltaje, potencia y diámetro cuando están escritos en el título (ej. "220V", "1500W", "Diámetro 230 mm").
DRY_RUN=1 solo informa; 0 escribe. DESDE / LIMITE para hacerlo por partes.
"""
import csv
import os
import re
import unicodedata
from collections import Counter

from ml.cliente import ML, log, sku_de

PROV = (os.environ.get("PROVEEDOR") or "FEMA").strip().upper()
SUFIJO = "#" + PROV
dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
desde = int(os.environ.get("DESDE") or 0)
limite = int(os.environ.get("LIMITE") or 0)
NO_CUENTAN = {"hidden", "read_only", "fixed", "others", "inferred"}
VARIOS = re.compile(r"\b(juego|jgo|kit|set|pack|par|pares|x\s?\d+|\d+\s?(unid|u\.|pzs|piezas))\b", re.I)

ml = ML(os.environ.get("ML_CODIGO"))
FICHAS = {}
if PROV == "DELFABRO":   # solo sus publicaciones, y los datos técnicos de la web guardados en las fichas
    import json
    ids = sorted(json.load(open("datos/delfabro_publicadas.json")).values())
    FICHAS = {f["sku"]: f for f in json.load(open("datos/delfabro_fichas.json", encoding="utf-8"))}
else:
    ids = sorted(ml.ids_publicaciones(status="active") + ml.ids_publicaciones(status="paused"))
log(f"{len(ids)} publicaciones en la cuenta")

_cats = {}


def attrs_cat(cat):
    if cat not in _cats:
        r = ml.pedir("GET", f"/categories/{cat}/attributes")
        _cats[cat] = {}
        for a in (r.json() if r.status_code == 200 else []):
            tags = a.get("tags") or {}
            if any(tags.get(t) for t in NO_CUENTAN):
                continue
            a["_importante"] = bool(tags.get("required") or tags.get("catalog_required")
                                    or tags.get("conditional_required") or a.get("relevance") == 1)
            _cats[cat][a["id"]] = a
        rc = ml.pedir("GET", f"/categories/{cat}")
        _cats[cat]["__nombre"] = {"name": rc.json().get("name", "") if rc.status_code == 200 else ""}
    return _cats[cat]


def _raices(t):
    t = unicodedata.normalize("NFKD", t.lower()).encode("ascii", "ignore").decode()
    return {w[:5] for w in re.findall(r"[a-z]{4,}", t)}


EMPAQUE = {"paque", "envas", "bolsa", "unida", "rollo", "cajas", "blist"}


def es_cantidad_de_venta(nombre, cat_nombre):
    """True si el campo cuenta unidades vendidas (paquetes, envases, o el mismo producto de la categoría),
    no una característica del producto (dientes, aspas, celdas, agujeros...)."""
    m = re.match(r"^(?:Cantidad de|Unidades por) (.+)$", nombre)
    if not m:
        return False
    resto = m.group(1)
    if re.search(r"\bpor (paquete|pack|envase|caja|bolsa|blister)\b", resto, re.I):
        return True
    if " para " in f" {resto} ":          # "compartimentos para billetes" es una característica
        return False
    r = _raices(resto)
    primera = _raices(resto.split()[0]) if resto.split() else set()
    return bool(r & EMPAQUE) or bool(primera & _raices(cat_nombre))


def num_unidad(a, valor, unidad):
    """Arma 'valor unidad' solo si la unidad está permitida por la categoría."""
    if a.get("value_type") == "number":
        return valor
    for u in a.get("allowed_units") or []:
        if (u.get("name") or "").lower().replace(" ", "") == unidad.lower().replace(" ", ""):
            return f"{valor} {u['name']}"
    return None


def valor_permitido(a, texto):
    vals = a.get("values") or []
    if not vals:
        return texto
    return next((v["name"] for v in vals if v["name"].lower() == texto.lower()), None)


def propuestas(it, sku, ca):
    tl = it.get("title") or ""
    cargados = {x["id"] for x in it.get("attributes") or [] if (x.get("value_name") or x.get("value_id"))
                and str(x.get("value_id")) != "-1"}
    out = {}
    cat_nombre = ca.get("__nombre", {}).get("name", "")
    for aid, a in ca.items():
        if aid.startswith("__") or aid in cargados or not a["_importante"]:
            continue
        nombre = (a.get("name") or "").strip()
        v = None
        if aid == "SALE_FORMAT" or nombre == "Formato de venta":
            v = valor_permitido(a, "Unidad")
        elif re.match(r"^(Cantidad de |Unidades por )", nombre) and a.get("value_type") in ("number", "number_unit"):
            if es_cantidad_de_venta(nombre, cat_nombre) and not VARIOS.search(tl):
                v = num_unidad(a, "1", "u") if a.get("value_type") == "number_unit" else "1"
        elif aid == "OEM" or nombre in ("Código OEM", "Número de pieza", "Código de pieza"):
            v = sku
        elif aid == "VOLTAGE" or nombre == "Voltaje":
            m = re.search(r"\b(\d{2,3})\s?v\b", tl, re.I)
            v = num_unidad(a, m.group(1), "V") if m else None
        elif aid == "POWER" or nombre == "Potencia":
            fr = re.search(r"(?<![\d.,/])(?:(\d+)\s+)?(\d+)\s?/\s?(\d+)\s?(hp|cv)\b", tl, re.I)
            m = re.search(r"(?<![\d/.,])(\d{1,3}(?:\.\d{3})+|\d+(?:[.,]\d+)?)\s?(kw|w|hp|cv)\b", tl, re.I)
            if fr:                                   # "3/4 HP" -> 0.75 ; "1 1/2 HP" -> 1.5
                val = int(fr.group(1) or 0) + int(fr.group(2)) / int(fr.group(3))
                v = num_unidad(a, f"{val:g}", "hp")
            elif m:
                u = {"kw": "kW", "w": "W", "hp": "hp", "cv": "hp"}[m.group(2).lower()]
                num = m.group(1)
                num = num.replace(".", "") if re.fullmatch(r"\d{1,3}(\.\d{3})+", num) else num.replace(",", ".")
                v = num_unidad(a, num, u)
        elif aid == "DIAMETER" or nombre == "Diámetro":
            m = re.search(r"di[aá]m(?:etro)?\.?\s*(\d+(?:[.,]\d+)?)\s*(mm|cm|\"|pulg)", tl, re.I)
            if m:
                u = {"\"": "\"", "pulg": "\""}.get(m.group(2).lower(), m.group(2).lower())
                v = num_unidad(a, m.group(1).replace(",", "."), u)
        if v is None and FICHAS.get(sku):
            v = desde_ficha(a, nombre, FICHAS[sku], tl)
        if v:
            out[aid] = (nombre, v)
    return out


# ---------- DELFABRO: datos técnicos de la web (fichas) -> atributos de ML ----------
CONV = {"mm": 1, "cm": 10, "m": 1000, "g": 0.001, "kg": 1, "t": 1000, "tn": 1000, "ton": 1000,
        "psi": 1, "bar": 14.5038, "mpa": 145.038, "v": 1, "w": 1, "kw": 1000, "hp": 745.7, "l": 1, "lts": 1, "lt": 1, "litros": 1}
FAM = {"mm": "L", "cm": "L", "m": "L", "g": "P", "kg": "P", "t": "P", "tn": "P", "ton": "P", "psi": "B", "bar": "B", "mpa": "B",
       "v": "V", "w": "W", "kw": "W", "hp": "W", "l": "C", "lts": "C", "lt": "C", "litros": "C"}


def _spec(f, *rx):
    for k, v in (f.get("specs") or {}).items():
        if any(re.search(r, k, re.I) for r in rx):
            return str(v)
    return None


def _medida(texto, cual=0):
    """'146/420 mm' -> (146, 'mm') o (420, 'mm') según cual; '2.3 kg' -> (2.3, 'kg')."""
    if not texto:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)(?:\s*(?:/|a|-)\s*(\d+(?:[.,]\d+)?))?\s*(mm|cm|m|kgs?|g|tn|ton|t|psi|bar|mpa|kw|w|hp|v|lts?|litros|l)\b",
                  texto.replace("Kgs", "kg"), re.I)
    if not m:
        return None
    n = m.group(2) if (cual and m.group(2)) else m.group(1)
    u = m.group(3).lower().rstrip("s") if m.group(3).lower() not in ("lts", "psi") else m.group(3).lower()
    return float(n.replace(",", ".")), u


def _a_unidad(a, valor, u):
    """Convierte a una unidad permitida por la categoría (misma familia)."""
    if a.get("value_type") == "number":
        return f"{valor:g}"
    perm = [(x.get("name") or "") for x in a.get("allowed_units") or []]
    fmt = lambda x: (f"{round(x, 2):.2f}").rstrip("0").rstrip(".")
    for pu in perm:
        if pu.lower() == u:
            return f"{fmt(valor)} {pu}"
    cands = []
    for pu in perm:
        k = pu.lower()
        if k in CONV and FAM.get(k) == FAM.get(u):
            x = valor * CONV[u] / CONV[k]
            cands.append((not (1 <= x < 100000), x, pu))
    if cands:
        _, x, pu = sorted(cands)[0]
        return f"{fmt(x)} {pu}"
    return None


def _num(a, f, tl, rx, cual=0, capacidad=False):
    if capacidad:
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(tn|toneladas|t|kg)\b", tl, re.I)
        if m:
            u = "kg" if m.group(2).lower() == "kg" else "t"
            return _a_unidad(a, float(m.group(1).replace(",", ".")), u)
    m = _medida(_spec(f, *rx), cual) if rx else None
    return _a_unidad(a, *m) if m else None


def _lista(a, *opciones):
    for o in opciones:
        if o:
            v = valor_permitido(a, o)
            if v:
                return v
    return None


def desde_ficha(a, nombre, f, tl):
    n = nombre.lower()
    if re.match(r"rueda|equipo|cadena", tl, re.I) and n in ("peso máximo soportado", "con ruedas"):
        return None                        # repuestos: la capacidad del título es la del guinche, no la de la pieza
    if re.match(r"prensa", tl, re.I) and n in ("largo total", "largo"):
        return None                        # en la web de prensas el "Largo" no es el largo total del equipo
    if re.search(r"arenadora", tl, re.I) and n in ("altura", "altura total"):
        return None
    if a.get("value_type") in ("number_unit", "number"):
        if n in ("peso máximo soportado", "capacidad máxima", "fuerza de presión", "capacidad de carga", "capacidad"):
            return _num(a, f, tl, [r"capacidad", r"carga"], capacidad=True)
        if n in ("máxima altura levantada", "elevación máxima", "altura máxima"):
            return _num(a, f, tl, [r"altura m[aá]x"]) or _num(a, f, tl, [r"altura m[ií]n.*m[aá]x|m[ií]n ?/ ?m[aá]x"], cual=1)
        if n in ("mínima altura levantada", "altura mínima"):
            return _num(a, f, tl, [r"altura m[ií]n(?!.*m[aá]x)"]) or _num(a, f, tl, [r"altura m[ií]n.*m[aá]x|m[ií]n ?/ ?m[aá]x"])
        if n in ("altura total", "altura"):
            return _num(a, f, tl, [r"altura total|^alto"])
        if n in ("ancho total", "ancho"):
            return _num(a, f, tl, [r"ancho total|^ancho$"])
        if n in ("largo total", "largo"):
            return _num(a, f, tl, [r"largo total|^largo$"])
        if n == "peso":
            return _num(a, f, tl, [r"^peso"])
        if n == "apertura de mordaza":
            return _num(a, f, tl, [r"apertura"])
        if n == "ancho de la mandíbula":
            return _num(a, f, tl, [r"ancho de mordaza"])
        if n in ("presión de trabajo", "presión máxima soportada", "presión máxima"):
            return _num(a, f, tl, [r"presi[oó]n"])
        if n == "voltaje":
            return _num(a, f, tl, [r"voltaje"])
        if n == "cantidad de ruedas" and re.match(r"rueda", tl, re.I):
            return _a_unidad(a, 1, "u") or "1"
        return None
    if n == "formato del gato":
        return _lista(a, "Botella" if re.search(r"botella|hidroneum", tl, re.I) else None,
                      "Carrito" if re.search(r"carrito", tl, re.I) else None)
    if n == "con yunque":
        return _lista(a, "Sí") if re.search(r"yunque", tl, re.I) else None
    if n == "con ruedas":
        return _lista(a, "Sí") if re.search(r"guinche|gr[uú]a|carrito|zorra", tl, re.I) else None
    if n == "es plegable":
        return _lista(a, "Sí") if re.search(r"desarmable|plegable", tl, re.I) else None
    if n == "tipo de conector":
        m = re.search(r"\b(macho|hembra)\b", tl, re.I)
        return _lista(a, m.group(1).capitalize()) if m else None
    if n in ("tipo de morsa de banco", "tipo de base"):
        b = (_spec(f, r"^base") or "") + " " + tl
        return _lista(a, "Giratoria" if re.search(r"girator", b, re.I) else "Fija" if re.search(r"\bfija\b", b, re.I) else None)
    if n == "material":
        mat = _spec(f, r"^material")
        if not mat:
            return None
        if a.get("values"):
            return next((v["name"] for v in a["values"] if v["name"].lower() in mat.lower()), None)
        return mat[:60]
    if n == "tipo de producto":
        if re.match(r"tap[oó]n", tl, re.I):
            return _lista(a, "Tapón") or (None if a.get("values") else "Tapón hidráulico")
        if re.match(r"acople", tl, re.I):
            return _lista(a, "Acople rápido") or (None if a.get("values") else "Acople rápido hidráulico")
        if re.match(r"soporte", tl, re.I):
            return None if a.get("values") else "Soporte con acople"
        if re.match(r"llave", tl, re.I):
            return _lista(a, "Llave de impacto") or (None if a.get("values") else "Llave de impacto")
    return None


filas, n_fema = [], 0
cola = []
for it in ml.publicaciones(ids, atributos="id,title,status,category_id,attributes,seller_custom_field"):
    sku = sku_de(it)
    if not sku.upper().endswith(SUFIJO):
        continue
    n_fema += 1
    cola.append((it, sku[: -len(SUFIJO)]))
log(f"{SUFIJO}: {n_fema}")
cola = cola[desde:]
if limite:
    cola = cola[:limite]

for n, (it, sku) in enumerate(cola, 1):
    ca = attrs_cat(it["category_id"])
    pr = propuestas(it, sku, ca)
    if not pr:
        continue
    fila = {"n": desde + n, "publicacion": it["id"], "sku": sku, "titulo": it.get("title"),
            "carga": " | ".join(f"{nm} = {v}" for nm, v in pr.values()), "resultado": ""}
    if dry:
        fila["resultado"] = "SE CARGARÍA"
    else:
        cuerpo = {"attributes": [{"id": k, "value_name": v} for k, (_, v) in pr.items()]}
        r = ml.put(f"/items/{it['id']}", cuerpo)
        if r.status_code in (200, 201):
            fila["resultado"] = "CARGADO"
        else:   # se prueba de a uno para salvar los que ML acepta
            ok, mal = [], []
            for k, (nm, v) in pr.items():
                r1 = ml.put(f"/items/{it['id']}", {"attributes": [{"id": k, "value_name": v}]})
                (ok if r1.status_code in (200, 201) else mal).append(f"{nm}" + ("" if r1.status_code in (200, 201)
                                                                             else f" ({r1.text[:120]})"))
            fila["resultado"] = ("CARGADO PARCIAL" if ok else "ERROR") + (": no aceptó " + "; ".join(mal) if mal else "")
    filas.append(fila)
    if len(filas) % 200 == 0:
        log(f"  {len(filas)} con datos para cargar (vamos en {desde + n})")

os.makedirs("salida", exist_ok=True)
with open("salida/completar_fichas_resultado.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["n", "publicacion", "sku", "titulo", "carga", "resultado"], delimiter=";")
    w.writeheader()
    w.writerows(filas)
cnt = Counter()
for x in filas:
    for p in x["carga"].split(" | "):
        cnt[p.split(" = ")[0]] += 1
log(f"Publicaciones revisadas: {len(cola)} | con datos para cargar: {len(filas)} | {'SIMULACIÓN' if dry else 'REAL'}")
log("Atributos: " + ", ".join(f"{k}: {v}" for k, v in cnt.most_common()))
log("Resultado: " + str(Counter(x["resultado"].split(":")[0] for x in filas)))
