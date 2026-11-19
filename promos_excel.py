"""Arma el Excel de promociones a partir de las filas que lee ml_promociones.py (también se puede usar aparte).

Cada fila (dict): promo_id, tipo, promo, estado_item, mla, sku, titulo, estado_pub, stock, actual, original,
sugerido, minimo, maximo, precio_ml, aporte_ml, costo, pct_campania.
"""
import math
from datetime import datetime, timezone, timedelta

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

AR = timezone(timedelta(hours=-3))
VERDE = "02561A"
COLOR = {"SÍ": "C6EFCE", "REVISAR": "FFEB9C", "NO": "FFC7CE", "SIN COSTO": "E7E6E6", "SIN PRECIO": "E7E6E6",
         "YA ADHERIDA": "BDD7EE", "COSTO DUDOSO": "E7E6E6"}
CAMPANIAS_PROPIAS = ("SELLER_COUPON_CAMPAIGN", "VOLUME")
QUE_ES = {
    "DEAL": "Oferta con precio tachado: elegís el precio dentro del rango mínimo–máximo. El descuento lo pagás vos.",
    "SMART": "Oferta automática de ML: el precio lo propone ML y ML pone una parte del descuento.",
    "PRICE_MATCHING": "Igualar a la competencia: precio propuesto por ML, ML pone una parte.",
    "SELLER_COUPON_CAMPAIGN": "Cupón tuyo, ya activo en toda la tienda (lo creaste vos).",
    "VOLUME": "Descuento por cantidad tuyo, ya activo (solo comprando la cantidad indicada).",
}


def _sug(x, mmin, mrev):
    if x is None:
        return "SIN PRECIO"
    return "SÍ" if x >= mmin else "REVISAR" if x >= mrev else "NO"


def evaluar(f, mmin, mrev):
    """Agrega: recibis, x (recibís / costo), recomendado, sugerencia."""
    c, t = f.get("costo"), f["tipo"]
    precio = f.get("precio_ml")
    if t in CAMPANIAS_PROPIAS:
        precio = round(f["actual"] * (1 - (f.get("pct_campania") or 0) / 100), 2) if f.get("actual") else None
    elif t == "DEAL" and not precio:
        precio = f.get("sugerido") or f.get("maximo")
    recibis = round(precio + (f.get("aporte_ml") or 0), 2) if precio else None
    f["precio_eval"], f["recibis"] = precio, recibis
    f["x"] = round(recibis / c, 2) if c and recibis else None
    f["recomendado"] = None
    if not c:
        f["sugerencia"] = "SIN COSTO"
    elif f.get("actual") and not (1 <= f["actual"] / c <= 6):   # precio actual > 6 veces el costo (o menor): el SKU cruzó mal
        f["sugerencia"] = "COSTO DUDOSO"
    elif f.get("estado_item") in ("started", "pending"):
        f["sugerencia"] = "YA ADHERIDA" if (f["x"] or 0) >= mrev else "NO"
    elif t == "DEAL" and f.get("maximo"):
        # el precio más bajo que todavía deja costo × MARGEN_MIN, dentro del rango que permite ML
        obj = math.ceil(c * mmin / 10) * 10
        sug = f.get("sugerido")
        if sug and sug >= obj:   # el precio que sugiere ML ya deja el margen: se usa ese
            f["recomendado"] = math.ceil(sug)
            f["sugerencia"] = "SÍ"
        elif obj <= f["maximo"]:   # si no, el precio más bajo que deja costo × MARGEN_MIN
            f["recomendado"] = max(obj, math.ceil((f.get("minimo") or 0)))
            f["sugerencia"] = "SÍ"
        elif c * mrev <= f["maximo"]:
            f["recomendado"] = f["maximo"]
            f["sugerencia"] = "REVISAR"
        else:
            f["sugerencia"] = "NO"
    else:
        f["sugerencia"] = _sug(f["x"], mmin, mrev)
    return f


def _hoja(wb, nombre, enc, filas, anchos, moneda=(), pct=(), col_sug=None, primera=False):
    ws = wb.active if primera else wb.create_sheet(nombre)
    ws.title = nombre
    ws.append(enc)
    for f in filas:
        ws.append(f)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=VERDE)
        c.alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[1].height = 34
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
            elif isinstance(c.value, float):
                c.number_format = "0.00"
        if col_sug and fila[col_sug - 1].value in COLOR:
            fila[col_sug - 1].fill = PatternFill("solid", fgColor=COLOR[fila[col_sug - 1].value])
    return ws


def armar(promos, filas, ruta, mmin=1.8, mrev=1.4):
    for f in filas:
        evaluar(f, mmin, mrev)
    orden = {"SÍ": 0, "REVISAR": 1, "NO": 2, "YA ADHERIDA": 3, "COSTO DUDOSO": 4, "SIN COSTO": 5, "SIN PRECIO": 6}
    wb = Workbook()

    # 1) Resumen
    res = []
    for p in promos:
        fs = [f for f in filas if f["promo_id"] == p["id"]]
        cnt = {k: sum(1 for f in fs if f["sugerencia"] == k) for k in ("SÍ", "REVISAR", "NO", "SIN COSTO", "COSTO DUDOSO", "YA ADHERIDA")}
        res.append([p["id"], p.get("nombre"), p["tipo"], QUE_ES.get(p["tipo"], ""), p.get("estado"), p.get("inicio"),
                    p.get("fin"), p.get("adherir_hasta"), len(fs), cnt["SÍ"], cnt["REVISAR"], cnt["NO"], cnt["SIN COSTO"],
                    cnt["COSTO DUDOSO"], cnt["YA ADHERIDA"]])
    _hoja(wb, "Resumen", ["ID PROMO", "PROMOCIÓN", "TIPO", "QUÉ ES", "ESTADO", "INICIO", "FIN", "ADHERIR HASTA",
                          "PUBLICACIONES", "SÍ", "REVISAR", "NO", "SIN COSTO", "COSTO DUDOSO", "YA ADHERIDAS"],
          res, [17, 30, 22, 60, 10, 16, 16, 16, 12, 8, 9, 8, 10, 11, 11], primera=True)

    # 2) Para decidir: ofertas de ML (no las campañas propias)
    dec = sorted((f for f in filas if f["tipo"] not in CAMPANIAS_PROPIAS),
                 key=lambda f: (f["promo"], orden.get(f["sugerencia"], 9), -(f["x"] or 0)))
    _hoja(wb, "Para decidir",
          ["PROMOCIÓN", "TIPO", "MLA", "SKU", "TÍTULO", "ESTADO PUBLICACIÓN", "STOCK", "PRECIO ACTUAL", "COSTO (BASE)", "ACTUAL / COSTO",
           "PRECIO MÍNIMO", "PRECIO MÁXIMO", "PRECIO QUE PROPONE ML", "APORTE ML ($)", "RECIBÍS", "RECIBÍS / COSTO",
           "% DESCUENTO", "PRECIO RECOMENDADO", "SUGERENCIA", "DECISIÓN (SI/NO)"],
          [[f["promo"], f["tipo"], f["mla"], f["sku"], f["titulo"], f["estado_pub"], f["stock"], f["actual"], f["costo"],
            round(f["actual"] / f["costo"], 2) if f["actual"] and f["costo"] else None, f.get("minimo"), f.get("maximo"), f["precio_eval"], f.get("aporte_ml"), f["recibis"], f["x"],
            round((1 - f["precio_eval"] / f["original"]) * 100, 1) if f["precio_eval"] and f.get("original") else None,
            f["recomendado"], f["sugerencia"], ""] for f in dec],
          [26, 15, 15, 18, 48, 11, 7, 13, 12, 10, 12, 12, 13, 11, 12, 10, 10, 13, 12, 11],
          moneda=(8, 9, 11, 12, 13, 14, 15, 18), pct=(17,), col_sug=19)

    # 3) Cupones y descuento por cantidad (campañas propias ya activas): una fila por publicación
    propias = [p for p in promos if p["tipo"] in CAMPANIAS_PROPIAS]
    por_mla = {}
    for f in filas:
        if f["tipo"] in CAMPANIAS_PROPIAS:
            por_mla.setdefault(f["mla"], {})[f["promo_id"]] = f
    cup = []
    for mla, d in por_mla.items():
        b = next(iter(d.values()))
        xs = [d[p["id"]]["x"] if p["id"] in d else None for p in propias]
        peor = min((x for x in xs if x is not None), default=None)
        cup.append([mla, b["sku"], b["titulo"], b["estado_pub"], b["stock"], b["actual"], b["costo"], *xs, peor,
                    "SIN COSTO" if not b["costo"] else "COSTO DUDOSO"
                    if b["actual"] and not (1 <= b["actual"] / b["costo"] <= 6) else _sug(peor, mmin, mrev)])
    cup.sort(key=lambda r: (r[-2] is None, r[-2] or 0))
    n = len(propias)
    _hoja(wb, "Cupones y cantidad",
          ["MLA", "SKU", "TÍTULO", "ESTADO PUBLICACIÓN", "STOCK", "PRECIO ACTUAL", "COSTO (BASE)",
           *[f"{p.get('nombre')} ({p.get('pct') or 0:g}%) · PRECIO/COSTO" for p in propias], "PEOR CASO", "SUGERENCIA"],
          cup, [15, 18, 48, 11, 7, 13, 12, *[16] * n, 10, 12], moneda=(6, 7), col_sug=7 + n + 2)

    # 4) Sin costo en BaseLinker
    sc = {}
    for f in filas:
        if not f["costo"]:
            sc.setdefault(f["mla"], [f["mla"], f["sku"], f["titulo"], f["estado_pub"], f["actual"], set()])[5].add(f["promo"])
    _hoja(wb, "Sin costo en Base", ["MLA", "SKU", "TÍTULO", "ESTADO PUBLICACIÓN", "PRECIO ACTUAL", "PROMOCIONES"],
          [[*r[:5], ", ".join(sorted(r[5]))] for r in sc.values()], [15, 20, 50, 11, 13, 60], moneda=(5,))

    ws = wb.create_sheet("Cómo leerlo")
    for linea in [
        "Generado " + datetime.now(AR).strftime("%d/%m/%Y %H:%M") + ". Solo lectura: no se adhirió ni se quitó nada.",
        "",
        "RECIBÍS = precio con la promo + lo que aporta Mercado Libre (en ofertas automáticas ML pone una parte).",
        "RECIBÍS / COSTO = cuántas veces el costo de BaseLinker cobrás. Hoy publicamos a costo × 2,5.",
        f"SUGERENCIA: SÍ si es ≥ {mmin:g} · REVISAR si es ≥ {mrev:g} · NO si es menor. La comisión y el envío de ML van aparte.",
        f"PRECIO RECOMENDADO (precio tachado / Black Friday): el que sugiere ML si deja costo × {mmin:g}; si no, el más bajo que lo deja.",
        "   Si costo × " + f"{mmin:g}" + " supera el máximo permitido pero costo × " + f"{mrev:g}" + " no, recomienda el máximo (REVISAR).",
        "Cupones y cantidad: tus campañas ya activas en toda la tienda. Muestra cuánto te queda si el cliente usa el cupón.",
        "   El descuento por cantidad solo se aplica comprando esa cantidad; el cupón tiene tope en pesos (el % real suele ser menor).",
        "COSTO DUDOSO: el precio actual es más de 6 veces el costo de BaseLinker (o menor al costo): el SKU seguramente cruzó con otro producto.",
        "Sin costo en Base: publicaciones cuyo SKU no está en BaseLinker (o no tiene precio): no se pueden evaluar.",
        "DECISIÓN: completala con SI / NO.",
    ]:
        ws.append([linea])
    ws.column_dimensions["A"].width = 120
    wb.save(ruta)
    cuenta = {}
    for f in filas:
        cuenta[f["sugerencia"]] = cuenta.get(f["sugerencia"], 0) + 1
    return cuenta
