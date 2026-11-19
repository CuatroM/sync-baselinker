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
from datetime import datetime, timezone, timedelta

import yaml
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

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

filas_p, filas_i = [], []
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
    if not items:
        continue
    det = {x["id"]: x for x in ml.publicaciones([i["id"] for i in items if i.get("id")],
                                                atributos="id,title,status,price,available_quantity,"
                                                          "seller_custom_field,attributes,variations")}
    for i in items:
        it = det.get(i.get("id")) or {}
        sku = sku_de(it) if it else ""
        actual = num(it.get("price")) or num(i.get("original_price"))
        original = num(i.get("original_price")) or actual
        sug = num(i.get("suggested_discounted_price"))
        promo_precio = num(i.get("price")) if i.get("status") in ("started", "pending") else None
        precio_eval = promo_precio or sug or num(i.get("max_discounted_price")) or num(i.get("price"))
        if not precio_eval and actual:   # cupones y otras sin precio propuesto: precio actual menos el % de la campaña
            pc = num(ben.get("seller_percent")) or num((p.get("discount") or {}).get("percent") if isinstance(p.get("discount"), dict) else None)
            precio_eval = round(actual * (1 - (pc or 0) / 100), 2)
        desc = round((1 - precio_eval / original) * 100, 1) if precio_eval and original else None
        c = costo_de(sku)
        x = round(precio_eval / c, 2) if c and precio_eval else None
        if not c:
            rec = "SIN COSTO"
        elif not x:
            rec = "SIN PRECIO"
        elif x >= MARGEN_MIN:
            rec = "SÍ"
        elif x >= MARGEN_REVISAR:
            rec = "REVISAR"
        else:
            rec = "NO"
        filas_i.append([pid, tipo, p.get("name") or "", i.get("id"), sku, it.get("title") or "",
                        it.get("status") or "", it.get("available_quantity"), i.get("status") or "",
                        original, sug, num(i.get("min_discounted_price")), num(i.get("max_discounted_price")),
                        promo_precio, desc, num(i.get("meli_percentage")), num(i.get("seller_percentage")),
                        c, x, rec, ""])

# ---------------- Excel
wb = Workbook()
VERDE, NAR = "02561A", "FF8C00"


def hoja(ws, enc, filas, anchos, moneda=(), pct=()):
    ws.append(enc)
    for f in filas:
        ws.append(f)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=VERDE)
        c.alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for k, a in enumerate(anchos, 1):
        ws.column_dimensions[get_column_letter(k)].width = a
    for fila in ws.iter_rows(min_row=2):
        for c in fila:
            if c.column in moneda:
                c.number_format = '"$"#,##0'
            elif c.column in pct:
                c.number_format = '0.0"%"'


ws = wb.active
ws.title = "Promociones"
hoja(ws, ["ID PROMO", "TIPO", "NOMBRE", "ESTADO", "INICIO", "FIN", "ADHERIR HASTA", "% APORTA ML", "% APORTÁS VOS",
          "ÍTEMS", "CANDIDATOS", "YA ADHERIDOS"], filas_p, [22, 22, 40, 12, 17, 17, 17, 11, 11, 9, 11, 11], pct=(8, 9))

ws2 = wb.create_sheet("Publicaciones")
hoja(ws2, ["ID PROMO", "TIPO", "PROMOCIÓN", "MLA", "SKU", "TÍTULO", "ESTADO PUBLICACIÓN", "STOCK", "ESTADO EN PROMO",
           "PRECIO ACTUAL", "PRECIO SUGERIDO ML", "PRECIO MÍNIMO", "PRECIO MÁXIMO", "PRECIO EN PROMO (ADHERIDO)",
           "% DESCUENTO", "% APORTA ML", "% APORTÁS VOS", "COSTO (BASE)", "PRECIO / COSTO", "SUGERENCIA", "DECISIÓN (SI/NO)"],
     filas_i, [20, 18, 30, 15, 18, 50, 12, 8, 12, 13, 13, 13, 13, 14, 10, 9, 9, 13, 10, 12, 12],
     moneda=(10, 11, 12, 13, 14, 18), pct=(15, 16, 17))
COLOR = {"SIN PRECIO": "E7E6E6", "SÍ": "C6EFCE", "REVISAR": "FFEB9C", "NO": "FFC7CE", "SIN COSTO": "E7E6E6"}
for fila in ws2.iter_rows(min_row=2):
    c = fila[19]
    if c.value in COLOR:
        c.fill = PatternFill("solid", fgColor=COLOR[c.value])

ws3 = wb.create_sheet("Cómo leerlo")
for linea in [
    "Excel generado " + datetime.now(AR).strftime("%d/%m/%Y %H:%M") + " (solo lectura: no se adhirió ni quitó nada).",
    "",
    "PRECIO / COSTO = precio con la promo ÷ costo en BaseLinker. Hoy publicamos a costo × 2,5.",
    f"SUGERENCIA: SÍ si precio/costo ≥ {MARGEN_MIN} · REVISAR si ≥ {MARGEN_REVISAR} · NO si es menor.",
    "Para evaluar se usa el precio en promo si ya está adherido; si no, el precio sugerido por ML (o el máximo permitido).",
    "Ojo: la comisión de ML y el envío se descuentan aparte; el multiplicador ya lo contempla de forma aproximada.",
    "% APORTA ML: parte del descuento que pone Mercado Libre (no la pagás vos).",
    "DECISIÓN: completala con SI / NO para indicar en qué entrar.",
]:
    ws3.append([linea])
ws3.column_dimensions["A"].width = 110

os.makedirs("salida", exist_ok=True)
wb.save("salida/promociones.xlsx")
cuenta = {}
for f in filas_i:
    cuenta[f[19]] = cuenta.get(f[19], 0) + 1
log(f"Listo: {len(filas_p)} promociones, {len(filas_i)} filas de publicaciones | sugerencias {cuenta}")
