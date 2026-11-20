"""Solo lectura: arma un Excel con las promociones de la Central de Promociones de Mercado Libre
(campañas a las que nos invitan) y, por cada una, las publicaciones candidatas o ya adheridas,
con el costo de BaseLinker y una sugerencia de si conviene entrar o no.

No adhiere ni quita nada: solo lee. La columna "DECISIÓN" queda vacía para completarla a mano.

Sugerencia (configurable con MARGEN_MIN y MARGEN_REVISAR, multiplicadores sobre el costo de BaseLinker):
  precio con promo >= costo × MARGEN_MIN      -> SÍ
  precio con promo >= costo × MARGEN_REVISAR  -> REVISAR
  menor                                       -> NO
  sin costo en BaseLinker                     -> SIN COSTO
"""
import json
import os
import re
from datetime import datetime, timezone, timedelta

import yaml
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

import promos_excel
import sync
from ml.cliente import ML, log, sku_de

AR = timezone(timedelta(hours=-3))
MARGEN_MIN = float(os.environ.get("MARGEN_MIN") or 1.8)
MARGEN_REVISAR = float(os.environ.get("MARGEN_REVISAR") or 1.4)
SOLO = (os.environ.get("SOLO") or "").strip()   # vacío = todas; si no, ids de promoción separados por coma

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
ml = ML(os.environ.get("ML_CODIGO"))

# ---------------- costos de BaseLinker (igual que la planilla)
costo = {}
if os.path.exists("datos/delfabro_fichas.json"):
    for f in json.load(open("datos/delfabro_fichas.json", encoding="utf-8")):
        if float(f.get("precio_lista") or 0) > 0:
            costo[sync.normalizar_codigo(f["sku"] + "#DELFABRO")] = float(f["precio_lista"])
try:
    bl = sync.BaseLinker(os.environ["BL_TOKEN"])
    for sec in ("baselinker", "extrapower", "jya", "delfabro"):
        b = cfg.get(sec)
        if not b or not b.get("inventory_id"):
            continue
        for p in bl.catalogo(int(b["inventory_id"])).values():
            v = float((p.get("prices") or {}).get(str(b["price_group_id"])) or 0)
            if v > 0:
                costo[sync.normalizar_codigo(p.get("sku"))] = v
except Exception as e:  # sin costos igual se arma el Excel
    log(f"AVISO: no se pudieron leer los costos de BaseLinker ({e})")
log(f"Costos cargados: {len(costo)}")


def costo_de(sku):
    if not sku:
        return None
    s = sync.normalizar_codigo(sku)
    base = s.split("#")[0]
    return costo.get(s) or next((costo[base + x] for x in ("#FEMA", "#EXTRAPOWER", "#JYA", "#DELFABRO", "#CALLONE")
                                 if base + x in costo), None) or costo.get(base)


def fecha(v):
    if not v:
        return ""
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(AR).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return v


def num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ---------------- promociones
r = ml.pedir("GET", f"/seller-promotions/users/{ml.user_id}", reintentos=2, params={"app_version": "v2"})
if r.status_code != 200:
    log(f"ERROR: Mercado Libre no deja leer la Central de Promociones ({r.status_code}): {r.text[:400]}")
    raise SystemExit(1)
promos = r.json().get("results") or []
if SOLO:
    quiero = {x.strip() for x in SOLO.split(",") if x.strip()}
    promos = [p for p in promos if p.get("id") in quiero]
log(f"Promociones encontradas: {len(promos)}")

filas_p, filas_i, promos_x = [], [], []
for p in promos:
    pid, tipo = p.get("id"), p.get("type")
    ben = p.get("benefits") or {}
    items, search_after = [], None
    while True:
        params = {"promotion_type": tipo, "app_version": "v2", "limit": 50}
        if search_after:
            params["search_after"] = search_after
        ri = ml.pedir("GET", f"/seller-promotions/promotions/{pid}/items", reintentos=3, params=params)
        if ri.status_code != 200:
            log(f"  {pid} {tipo}: no se pudieron leer los ítems ({ri.status_code}) {ri.text[:200]}")
            break
        d = ri.json()
        lote = d.get("results") or []
        items += lote
        search_after = (d.get("paging") or {}).get("searchAfter")
        if not lote or not search_after:
            break
    log(f"{pid} | {tipo} | {p.get('name')} | {p.get('status')} | {len(items)} ítems")
    log(f"   datos promo: {json.dumps({k: v for k, v in p.items() if k not in ('name',)}, ensure_ascii=False)[:400]}")
    if items:
        log(f"   ejemplo ítem: {json.dumps(items[0], ensure_ascii=False)[:400]}")
    filas_p.append([pid, tipo, p.get("name") or "", p.get("status") or "", fecha(p.get("start_date")),
                    fecha(p.get("finish_date")), fecha(p.get("deadline_date")),
                    num(ben.get("meli_percent")), num(ben.get("seller_percent")), len(items),
                    sum(1 for i in items if i.get("status") == "candidate"),
                    sum(1 for i in items if i.get("status") in ("started", "pending"))])
    pct_c = num(p.get("fixed_percentage")) or num(p.get("discount_percentage"))
    if pct_c is None:
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", p.get("name") or "")
        pct_c = float(m.group(1).replace(",", ".")) if m else 0
    promos_x.append({"id": pid, "tipo": tipo, "nombre": p.get("name") or "", "estado": p.get("status") or "",
                     "inicio": fecha(p.get("start_date")), "fin": fecha(p.get("finish_date")),
                     "adherir_hasta": fecha(p.get("deadline_date")), "pct": pct_c})
    if not items:
        continue
    det = {x["id"]: x for x in ml.publicaciones([i["id"] for i in items if i.get("id")],
                                                atributos="id,title,status,price,available_quantity,"
                                                          "seller_custom_field,attributes,variations,category_id,"
                                                          "listing_type_id,tags,shipping")}
    for i in items:
        it = det.get(i.get("id")) or {}
        sku = sku_de(it) if it else ""
        actual = num(it.get("price")) or num(i.get("original_price"))
        original = num(i.get("original_price")) or actual
        aporte = num(i.get("suggestion_meli_amount"))
        if aporte is None and num(i.get("meli_percentage")) and original:
            aporte = round(original * num(i.get("meli_percentage")) / 100, 2)
        filas_i.append({"promo_id": pid, "tipo": tipo, "promo": p.get("name") or "", "estado_item": i.get("status") or "",
                        "mla": i.get("id"), "sku": sku, "titulo": it.get("title") or "", "estado_pub": it.get("status") or "",
                        "stock": it.get("available_quantity"), "actual": actual, "original": original,
                        "sugerido": num(i.get("suggested_discounted_price")), "minimo": num(i.get("min_discounted_price")),
                        "maximo": num(i.get("max_discounted_price")), "precio_ml": num(i.get("price")),
                        "aporte_ml": aporte, "costo": costo_de(sku), "pct_campania": pct_c,
                        "cat": it.get("category_id"), "lt": it.get("listing_type_id"), "tags": it.get("tags") or [],
                        "envio_modo": (it.get("shipping") or {}).get("mode"),
                        "envio_gratis": bool((it.get("shipping") or {}).get("free_shipping"))})

# ---------------- costos de vender (solo para las ofertas a decidir): envío, comisión, financiación, cargo fijo
CUOTAS_TAGS = ("3x_campaign", "9x_campaign", "12x_campaign", "pcj-co-funded")
_fee = {}


def tarifa(cat, lt, tags, precio):
    """(% comisión, % financiación, cargo fijo $) de ML para esa categoría, tipo de publicación y precio."""
    tag = next((t for t in CUOTAS_TAGS if t in tags), None)
    clave = (cat, lt, tag, round(precio, -1) if precio < 60000 else 0)   # el cargo fijo solo cambia en precios bajos
    if clave in _fee:
        return _fee[clave]

    def pedir(lt_, tag_):
        params = {"price": precio, "listing_type_id": lt_, "category_id": cat}
        if tag_:
            params["tags"] = tag_
        r = ml.pedir("GET", "/sites/MLA/listing_prices", reintentos=3, params=params)
        if r.status_code != 200:
            return None
        j = r.json()
        j = j[0] if isinstance(j, list) and j else j
        return j or None

    j = pedir(lt, tag)
    res = ("", "", "")
    if j:
        d = j.get("sale_fee_details") or {}
        total = num(d.get("percentage_fee"))
        if total is None and num(j.get("sale_fee_amount")) and precio:
            total = round(100 * num(j["sale_fee_amount"]) / precio, 2)
        fin = num(d.get("financing_add_on_fee"))
        if fin is None and total is not None:
            if lt == "gold_special" and not tag:
                fin = 0.0
            else:   # diferencia contra la publicación clásica sin cuotas
                b = pedir("gold_special", None)
                bt = num(((b or {}).get("sale_fee_details") or {}).get("percentage_fee")) if b else None
                fin = round(total - bt, 2) if bt is not None else None
        com = round(total - (fin or 0), 2) if total is not None else ""
        res = (com, "" if fin is None else fin, num(d.get("fixed_fee")) or 0.0)
        if len(_fee) < 2:
            log(f"   ejemplo tarifa {clave}: {json.dumps(j, ensure_ascii=False)[:400]}")
    _fee[clave] = res
    return res


def costo_envio(mla):
    r = ml.pedir("GET", f"/users/{ml.user_id}/shipping_options/free", reintentos=3, params={"item_id": mla})
    if r.status_code != 200:
        return None
    return num(((r.json().get("coverage") or {}).get("all_country") or {}).get("list_cost"))


decidir = [f for f in filas_i if f["tipo"] not in promos_excel.CAMPANIAS_PROPIAS]
envio = {}
from concurrent.futures import ThreadPoolExecutor
pend = sorted({f["mla"] for f in decidir if f["envio_modo"] == "me2" and f["envio_gratis"]})
with ThreadPoolExecutor(6) as ex:
    for mla, v in zip(pend, ex.map(costo_envio, pend)):
        envio[mla] = v
log(f"Costos de envío leídos: {sum(1 for v in envio.values() if v is not None)} de {len(pend)}")
for f in decidir:
    promos_excel.evaluar(f, MARGEN_MIN, MARGEN_REVISAR)
    if f["envio_modo"] == "me2":
        f["envio_costo"] = envio.get(f["mla"]) if f["envio_gratis"] else "Lo paga el comprador"
    else:
        f["envio_costo"] = "A acordar"
    f["precio_final"] = f.get("recomendado") or f.get("precio_eval")
con_tarifa = [f for f in decidir if f["precio_final"] and f.get("cat") and f.get("lt")]
with ThreadPoolExecutor(6) as ex:
    res = list(ex.map(lambda f: tarifa(f["cat"], f["lt"], f["tags"], f["precio_final"]), con_tarifa))
for f in decidir:
    f["com_pct"] = f["fin_pct"] = f["cargo_fijo"] = ""
for f, (c_, fi, fx) in zip(con_tarifa, res):
    f["com_pct"], f["fin_pct"], f["cargo_fijo"] = c_, fi, fx
log(f"Tarifas de ML consultadas: {len(_fee)}")

os.makedirs("salida", exist_ok=True)
with open("salida/promociones_filas.json", "w", encoding="utf-8") as fh:
    json.dump({"promos": promos_x, "filas": filas_i}, fh, ensure_ascii=False)
cuenta = promos_excel.armar(promos_x, filas_i, "salida/promociones.xlsx", MARGEN_MIN, MARGEN_REVISAR)
if os.environ.get("GOOGLE_SA_JSON"):
    import promos_sheet
    promos_sheet.subir(cfg["planilla_ml"]["sheet_id"], promos_x, filas_i, MARGEN_MIN, MARGEN_REVISAR, log)
else:
    log("AVISO: sin GOOGLE_SA_JSON, no se actualizó la planilla de Google")
log(f"Listo: {len(promos_x)} promociones, {len(filas_i)} filas de publicaciones | sugerencias {cuenta}")
