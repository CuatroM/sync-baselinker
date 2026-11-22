"""Sube las promociones a la planilla de Google "Mayorista y envíos ML" (pestañas "Promos ...").

Columnas que completás vos en "Promos - Decidir": DECISIÓN (SI/NO) y PRECIO A PONER.
Se conservan entre corridas (se cruzan por ID PROMO + MLA); el resto lo reescribe el sistema.
"""
import json
import os
from datetime import datetime, timezone, timedelta

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from promos_excel import CAMPANIAS_PROPIAS, QUE_ES

AR = timezone(timedelta(hours=-3))
T_RES, T_DEC, T_CUP = "Promos - Resumen", "Promos - Decidir", "Promos - Cupones"
COLOR = {"SÍ": (0.78, 0.94, 0.81), "REVISAR": (1, 0.92, 0.61), "NO": (1, 0.78, 0.81), "YA ADHERIDA": (0.74, 0.84, 0.93)}
DEC_ENC = ["ID PROMO", "PROMOCIÓN", "TIPO", "MLA", "SKU", "TÍTULO", "ESTADO PUBLICACIÓN", "STOCK", "PRECIO ACTUAL",
           "COSTO (BASE)", "ACTUAL / COSTO", "PRECIO MÍNIMO", "PRECIO MÁXIMO", "PRECIO QUE PROPONE ML", "APORTE ML ($)",
           "RECIBÍS / COSTO", "% DESCUENTO", "PRECIO RECOMENDADO", "SUGERENCIA", "DECISIÓN (SI/NO)", "PRECIO A PONER",
           "ESTADO EN PROMO"]
I_DEC, I_PRE = DEC_ENC.index("DECISIÓN (SI/NO)"), DEC_ENC.index("PRECIO A PONER")
# Desde la columna W (índice 22): posiciones fijas. Las que tienen nombre acá las escribe el sistema;
# todas las demás (W–Z, AK–AM, AX, BA y desde BC) son tuyas y se conservan en cada actualización.
IMP = {"IIBB": 4.75, "COMERCIO": 1.5, "CHEQUE": 1.2, "GANANCIAS": 5.0}   # % sobre el precio sin IVA
SIS = {
    26: "TIPO DE ENVÍO ML", 27: "COSTO ENVÍO ML ($)", 28: "% COMISIÓN ML", 29: "COMISIÓN ML ($)",
    30: "% FINANCIACIÓN (CUOTAS)", 31: "FINANCIACIÓN ($)", 32: "% IVA", 33: "CARGO FIJO ML ($)",
    34: "TE QUEDA (NETO $)", 35: "NETO / COSTO",
    39: "IVA NETO VENTA EN MERCADO LIBRE", 40: "PRECIO DE VENTA EN MELI SIN IVA",
    41: f"INGRESOS BRUTOS PRECIO SIN IVA {str(IMP['IIBB']).rstrip('0').rstrip('.').replace('.', ',')}%", 42: f"COMERCIO E INDUSTRIA PRECIO SIN IVA {str(IMP['COMERCIO']).rstrip('0').rstrip('.').replace('.', ',')}%",
    43: f"CRÉDITO Y DÉBITO PRECIO SIN IVA {str(IMP['CHEQUE']).rstrip('0').rstrip('.').replace('.', ',')}%", 44: "MARGEN DE IMPUESTO RENTAS PRECIO SIN IVA",
    45: f"IMPUESTO A LAS GANANCIAS {str(IMP['GANANCIAS']).rstrip('0').rstrip('.').replace('.', ',')}%", 46: "COSTO EXTRA EN IMPUESTOS",
    47: "NETO MELI (− COMISIÓN, FINANCIACIÓN Y ENVÍOS MELI)",
    48: "UTILIDAD NETA: VENTA SIN IVA − MELI − COSTO PRODUCTO − IMPUESTOS", 50: "PORCENTAJE UTILIDAD (SOBRE COSTO)",
    51: "ENVÍO A COBRAR (CREDIFIN)", 53: "GANANCIA",
}
I_CREDIFIN_PROM = 52   # BA: costo promedio del envío Credifin (lo completás vos); AX queda libre
ENC_LIBRE_DEF = {I_CREDIFIN_PROM: "COSTO PROMEDIO DEL ENVÍO CREDIFIN (completalo vos)"}
ULTIMA = max(SIS) + 1   # 54 = columnas hasta BB

class Hoja:
    def __init__(self, sheet_id):
        self.gs = AuthorizedSession(service_account.Credentials.from_service_account_info(
            json.loads(os.environ["GOOGLE_SA_JSON"]), scopes=["https://www.googleapis.com/auth/spreadsheets"]))
        self.url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"

    def props(self):
        r = self.gs.get(self.url, params={"fields": "sheets(properties,conditionalFormats)"})
        r.raise_for_status()
        return {s["properties"]["title"]: s for s in r.json()["sheets"]}

    def asegurar(self, nombre, filas, cols):
        h = self.props()
        if nombre not in h:
            self.lote([{"addSheet": {"properties": {"title": nombre, "gridProperties": {"rowCount": max(filas, 100),
                                                                                         "columnCount": cols}}}}])
            h = self.props()
        p = h[nombre]["properties"]
        g = p.get("gridProperties", {})
        req = []
        if g.get("rowCount", 0) < filas:
            req.append({"appendDimension": {"sheetId": p["sheetId"], "dimension": "ROWS", "length": filas - g["rowCount"]}})
        if g.get("columnCount", 0) < cols:
            req.append({"appendDimension": {"sheetId": p["sheetId"], "dimension": "COLUMNS",
                                            "length": cols - g["columnCount"]}})
        if req:
            self.lote(req)
        return p["sheetId"], len(h[nombre].get("conditionalFormats") or [])

    def lote(self, req):
        r = self.gs.post(f"{self.url}:batchUpdate", json={"requests": req})
        if r.status_code != 200:
            raise RuntimeError(f"Google Sheets {r.status_code}: {r.text[:300]}")

    def leer(self, rango):
        r = self.gs.get(f"{self.url}/values/{rango}", params={"valueRenderOption": "UNFORMATTED_VALUE"})
        if r.status_code == 400:   # la pestaña todavía no existe
            return []
        r.raise_for_status()
        return r.json().get("values") or []

    def escribir(self, nombre, filas):
        self.gs.post(f"{self.url}/values/'{nombre}':clear").raise_for_status()
        for i in range(0, len(filas), 5000):
            r = self.gs.put(f"{self.url}/values/'{nombre}'!A{i + 1}", params={"valueInputOption": "RAW"},
                            json={"values": filas[i:i + 5000]})
            if r.status_code != 200:
                raise RuntimeError(f"Google Sheets {r.status_code}: {r.text[:300]}")


def _v(x):
    return "" if x is None else x


def _n(x):
    try:
        return float(str(x).replace("$", "").replace(".", "").replace(",", ".")) if isinstance(x, str) else float(x)
    except (TypeError, ValueError):
        return None


def extras(f, precio_a_poner="", envio_cobrar=None, credifin_prom=None):
    """{posición: valor} de AA–AJ y AN–BC al precio final de la promo."""
    p = _n(precio_a_poner) if str(precio_a_poner).strip() and f["tipo"] == "DEAL" else f.get("precio_final")
    env, com, fin, fijo = f.get("envio_costo", ""), f.get("com_pct", ""), f.get("fin_pct", ""), f.get("cargo_fijo", "")
    if isinstance(env, (int, float)):
        tipo, env_n = "Mercado Envíos", float(env)
    else:
        tipo, env_n = ({"A acordar": "A acordar", "Lo paga el comprador": "Mercado Envíos (lo paga el comprador)"}
                       .get(env, env or ""), 0.0)
    out = {26: tipo, 27: env_n, 28: _v(com), 30: _v(fin), 32: _v(f.get("iva")), 33: _v(fijo)}
    if not p or com == "":
        return out
    aporte, costo = f.get("aporte_ml") or 0, f.get("costo")
    com_n, fin_n = round(p * float(com) / 100, 2), round(p * float(fin or 0) / 100, 2)
    cargos = round(com_n + fin_n + float(fijo or 0) + env_n, 2)
    neto = round(p + aporte - cargos, 2)
    out.update({29: com_n, 31: fin_n, 34: neto, 35: round(neto / costo, 2) if costo else ""})
    iva = float(f.get("iva") or 21)
    sin_iva = round(p / (1 + iva / 100), 2)
    iibb, cei, chq = (round(sin_iva * IMP[k] / 100, 2) for k in ("IIBB", "COMERCIO", "CHEQUE"))
    rentas = round(iibb + cei + chq, 2)
    gan = round(sin_iva * IMP["GANANCIAS"] / 100, 2)
    extra = round(rentas + gan, 2)
    out.update({39: round(p - sin_iva, 2), 40: sin_iva, 41: iibb, 42: cei, 43: chq, 44: rentas, 45: gan, 46: extra,
                47: neto})
    if costo:
        util = round(sin_iva + aporte - cargos - costo - extra, 2)
        cobrar = _n(envio_cobrar) if tipo == "A acordar" and envio_cobrar not in (None, "") else 0.0
        prom = _n(credifin_prom) or 0.0
        out.update({48: util, 50: round(util / costo * 100, 1), 51: cobrar, 53: round(util + (cobrar or 0) - prom, 2)})
    return out

def formato(h, sid, n_cf, ncols, col_sug=None, col_dec=None, anchos=None, editables=()):
    req = [{"deleteConditionalFormatRule": {"sheetId": sid, "index": 0}} for _ in range(n_cf)]
    req += [{"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1}},
                                       "fields": "gridProperties.frozenRowCount"}},
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                            "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.008, "green": 0.337, "blue": 0.102},
                                                           "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                                                           "wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE"}},
                            "fields": "userEnteredFormat"}},
            {"setBasicFilter": {"filter": {"range": {"sheetId": sid, "startRowIndex": 0, "startColumnIndex": 0,
                                                     "endColumnIndex": ncols}}}}]
    for c in editables:   # columnas que completás vos: fondo naranja claro en el encabezado
        req.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": c,
                                             "endColumnIndex": c + 1},
                                   "cell": {"userEnteredFormat": {"backgroundColor": {"red": 1, "green": 0.55, "blue": 0}}},
                                   "fields": "userEnteredFormat.backgroundColor"}})
    if col_sug is not None:
        for k, (r, g, b) in COLOR.items():
            req.append({"addConditionalFormatRule": {"rule": {
                "ranges": [{"sheetId": sid, "startRowIndex": 1, "startColumnIndex": col_sug, "endColumnIndex": col_sug + 1}],
                "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": k}]},
                                "format": {"backgroundColor": {"red": r, "green": g, "blue": b}}}}, "index": 0}})
    if col_dec is not None:
        req.append({"setDataValidation": {"range": {"sheetId": sid, "startRowIndex": 1, "startColumnIndex": col_dec,
                                                    "endColumnIndex": col_dec + 1},
                                          "rule": {"condition": {"type": "ONE_OF_LIST",
                                                                 "values": [{"userEnteredValue": "SI"}, {"userEnteredValue": "NO"}]},
                                                   "showCustomUi": True, "strict": False}}})
    for i, a in enumerate(anchos or []):
        req.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i,
                                                            "endIndex": i + 1},
                                                  "properties": {"pixelSize": a}, "fields": "pixelSize"}})
    h.lote(req)


def subir(sheet_id, promos, filas, mmin, mrev, log=print):
    """filas ya evaluadas por promos_excel.evaluar (tienen sugerencia, recomendado, etc.)."""
    h = Hoja(sheet_id)
    ahora = datetime.now(AR).strftime("%d/%m/%Y %H:%M")

    # lo ya cargado: DECISIÓN, PRECIO A PONER y toda columna que no sea del sistema (por posición)
    prev, libres, enc_viejo = {}, {}, []
    viejo = h.leer(f"'{T_DEC}'")
    if viejo:
        enc_viejo = viejo[0]
        ip, im = enc_viejo.index("ID PROMO"), enc_viejo.index("MLA")
        idd = enc_viejo.index("DECISIÓN (SI/NO)") if "DECISIÓN (SI/NO)" in enc_viejo else None
        ipp = enc_viejo.index("PRECIO A PONER") if "PRECIO A PONER" in enc_viejo else None
        for r in viejo[1:]:
            r = r + [""] * (len(enc_viejo) - len(r))
            d = r[idd] if idd is not None else ""
            pp = r[ipp] if ipp is not None else ""
            if str(d).strip() or str(pp).strip():
                prev[(r[ip], r[im])] = (d, pp)
            lib = {i: v for i, v in enumerate(r) if i >= len(DEC_ENC) and i not in SIS and str(v).strip()}
            if lib:
                libres[(r[ip], r[im])] = lib
    log(f"Planilla: {len(prev)} decisiones ya cargadas se conservan")

    # envío a cobrar (Credifin) de la pestaña Publicaciones
    cobrar = {}
    pub = h.leer("'Publicaciones'")
    if pub and "MLA" in pub[0] and "ENVÍO CREDIFIN" in pub[0]:
        a_, b_ = pub[0].index("MLA"), pub[0].index("ENVÍO CREDIFIN")
        cobrar = {r[a_]: r[b_] for r in pub[1:] if len(r) > b_ and r[b_] not in ("", None)}

    # Decidir
    orden = {"SÍ": 0, "REVISAR": 1, "NO": 2, "YA ADHERIDA": 3, "COSTO DUDOSO": 4, "SIN COSTO": 5, "SIN PRECIO": 6}
    dec = sorted((f for f in filas if f["tipo"] not in CAMPANIAS_PROPIAS),
                 key=lambda f: (f["promo"], orden.get(f["sugerencia"], 9), -(f["x"] or 0)))
    ancho = max([ULTIMA, len(enc_viejo)] + [max(l) + 1 for l in libres.values()])
    enc_total = DEC_ENC + [""] * (ancho - len(DEC_ENC))
    for i in range(len(DEC_ENC), ancho):
        if i in SIS:
            enc_total[i] = SIS[i]
        else:
            v = enc_viejo[i] if i < len(enc_viejo) else ""
            enc_total[i] = v if str(v).strip() and v not in SIS.values() else ENC_LIBRE_DEF.get(i, "")
    filas_dec = [enc_total]
    for f in dec:
        k = (f["promo_id"], f["mla"])
        d, pp = prev.get(k, ("", ""))
        lib = libres.get(k, {})
        fila = [f["promo_id"], f["promo"], f["tipo"], f["mla"], f["sku"], f["titulo"], f["estado_pub"],
                _v(f["stock"]), _v(f["actual"]), _v(f["costo"]),
                round(f["actual"] / f["costo"], 2) if f["actual"] and f["costo"] else "",
                _v(f.get("minimo")), _v(f.get("maximo")), _v(f["precio_eval"]), _v(f.get("aporte_ml")),
                _v(f["x"]),
                round((1 - f["precio_eval"] / f["original"]) * 100, 1) if f["precio_eval"] and f.get("original") else "",
                _v(f["recomendado"]), f["sugerencia"], d, pp, f.get("estado_item") or ""]
        fila += [""] * (ancho - len(fila))
        for i, v in lib.items():
            fila[i] = v
        for i, v in extras(f, pp, cobrar.get(f["mla"]), lib.get(I_CREDIFIN_PROM)).items():
            fila[i] = v
        filas_dec.append(fila)
    sid, ncf = h.asegurar(T_DEC, len(filas_dec) + 50, ancho)
    h.escribir(T_DEC, filas_dec)
    formato(h, sid, ncf, ancho, col_sug=DEC_ENC.index("SUGERENCIA"), col_dec=I_DEC,
            editables=(I_DEC, I_PRE, I_CREDIFIN_PROM),
            anchos=[110, 180, 110, 110, 120, 320, 90, 60, 100, 100, 80, 100, 100, 110, 90, 90, 80, 110, 100, 90, 100, 90]
            + [90] * 4 + [130, 100, 80, 100, 90, 100, 60, 90, 110, 80] + [90] * 3 + [110] * 15)

    # Cupones (campañas propias ya activas): una fila por publicación
    propias = [p for p in promos if p["tipo"] in CAMPANIAS_PROPIAS]
    por = {}
    for f in filas:
        if f["tipo"] in CAMPANIAS_PROPIAS:
            por.setdefault(f["mla"], {})[f["promo_id"]] = f
    enc_c = ["MLA", "SKU", "TÍTULO", "ESTADO PUBLICACIÓN", "STOCK", "PRECIO ACTUAL", "COSTO (BASE)",
             *[f"{p['nombre']} ({p.get('pct') or 0:g}%) · RECIBÍS/COSTO" for p in propias], "PEOR CASO", "SUGERENCIA"]
    cup = []
    for mla, d in por.items():
        b = next(iter(d.values()))
        xs = [d[p["id"]]["x"] if p["id"] in d else None for p in propias]
        peor = min((x for x in xs if x is not None), default=None)
        if not b["costo"]:
            s = "SIN COSTO"
        elif b["actual"] and not (1 <= b["actual"] / b["costo"] <= 6):
            s = "COSTO DUDOSO"
        else:
            s = "SÍ" if peor >= mmin else "REVISAR" if peor >= mrev else "NO"
        cup.append([mla, b["sku"], b["titulo"], b["estado_pub"], _v(b["stock"]), _v(b["actual"]), _v(b["costo"]),
                    *[_v(x) for x in xs], _v(peor), s])
    o = {"NO": 0, "REVISAR": 1, "SÍ": 2, "COSTO DUDOSO": 3, "SIN COSTO": 4}
    cup.sort(key=lambda r: (o.get(r[-1], 9), r[-2] if r[-2] != "" else 0))
    sid, ncf = h.asegurar(T_CUP, len(cup) + 50, len(enc_c))
    h.escribir(T_CUP, [enc_c] + cup)
    formato(h, sid, ncf, len(enc_c), col_sug=len(enc_c) - 1,
            anchos=[110, 120, 320, 90, 60, 100, 100, *[140] * len(propias), 90, 100])

    # Resumen
    res = [[f"Actualizado {ahora} · SÍ si recibís ≥ costo × {mmin:g} · REVISAR si ≥ × {mrev:g} · "
            "Completá DECISIÓN (SI/NO) y, si querés, PRECIO A PONER en «Promos - Decidir»."],
           ["ID PROMO", "PROMOCIÓN", "TIPO", "QUÉ ES", "ESTADO", "INICIO", "FIN", "ADHERIR HASTA", "PUBLICACIONES", "SÍ",
            "REVISAR", "NO", "SIN COSTO", "COSTO DUDOSO", "YA ADHERIDAS", "MARCADAS SI", "MARCADAS NO"]]
    for p in promos:
        fs = [f for f in filas if f["promo_id"] == p["id"]]
        c = lambda k: sum(1 for f in fs if f["sugerencia"] == k)
        si = sum(1 for (pid, _), (d, _) in prev.items() if pid == p["id"] and str(d).strip().upper() == "SI")
        no = sum(1 for (pid, _), (d, _) in prev.items() if pid == p["id"] and str(d).strip().upper() == "NO")
        res.append([p["id"], p.get("nombre"), p["tipo"], QUE_ES.get(p["tipo"], ""), p.get("estado"), p.get("inicio"),
                    p.get("fin"), p.get("adherir_hasta"), len(fs), c("SÍ"), c("REVISAR"), c("NO"), c("SIN COSTO"),
                    c("COSTO DUDOSO"), c("YA ADHERIDA"), si, no])
    sid, ncf = h.asegurar(T_RES, len(res) + 20, len(res[1]))
    h.escribir(T_RES, res)
    h.lote([{"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": 2},
                            "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.008, "green": 0.337, "blue": 0.102},
                                                           "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                                                           "wrapStrategy": "WRAP"}},
                            "fields": "userEnteredFormat"}},
            {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}}, "fields": "userEnteredFormat"}}]
           + [{"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                                             "properties": {"pixelSize": a}, "fields": "pixelSize"}}
              for i, a in enumerate([110, 200, 150, 380, 70, 110, 110, 110, 90, 60, 70, 60, 80, 90, 90, 90, 90])])
    log(f"Planilla actualizada: {len(dec)} filas en «{T_DEC}», {len(cup)} en «{T_CUP}», {len(promos)} promociones")
