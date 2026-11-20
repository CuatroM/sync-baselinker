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
LIBRES = 4   # columnas W a Z: libres para tus notas, se conservan
EXTRA_ENC = ["COSTO ENVÍO ML ($)", "% COMISIÓN ML", "% FINANCIACIÓN (CUOTAS)", "CARGO FIJO ML ($)",
             "TE QUEDA (NETO $)", "NETO / COSTO"]   # columnas AA a AF
I_EXTRA = len(DEC_ENC) + LIBRES          # 26 = columna AA
FIN_SIS = I_EXTRA + len(EXTRA_ENC)       # desde AG en adelante: también libres, se conservan


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


def extras(f, precio_a_poner=""):
    """AA–AF: envío, % comisión, % financiación, cargo fijo, neto y neto/costo al precio final de la promo."""
    p = _n(precio_a_poner) if str(precio_a_poner).strip() and f["tipo"] == "DEAL" else f.get("precio_final")
    env, com, fin, fijo = f.get("envio_costo", ""), f.get("com_pct", ""), f.get("fin_pct", ""), f.get("cargo_fijo", "")
    neto = x = ""
    if p and com != "":
        e = env if isinstance(env, (int, float)) else 0
        neto = round(p + (f.get("aporte_ml") or 0) - p * (float(com) + float(fin or 0)) / 100 - float(fijo or 0) - e, 2)
        x = round(neto / f["costo"], 2) if f.get("costo") else ""
    return [_v(env), _v(com), _v(fin), _v(fijo), neto, x]


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

    # decisiones ya cargadas
    prev, libres, enc_libres = {}, {}, None
    viejo = h.leer(f"'{T_DEC}'")
    if viejo:
        enc = viejo[0]
        enc_libres = (enc + [""] * FIN_SIS)[len(DEC_ENC):I_EXTRA] + enc[FIN_SIS:]
        ip, im = enc.index("ID PROMO"), enc.index("MLA")
        idd = enc.index("DECISIÓN (SI/NO)") if "DECISIÓN (SI/NO)" in enc else None
        ipp = enc.index("PRECIO A PONER") if "PRECIO A PONER" in enc else None
        for r in viejo[1:]:
            r = r + [""] * (len(enc) - len(r))
            d = r[idd] if idd is not None else ""
            pp = r[ipp] if ipp is not None else ""
            if str(d).strip() or str(pp).strip():
                prev[(r[ip], r[im])] = (d, pp)
            lib = (r + [""] * FIN_SIS)[len(DEC_ENC):I_EXTRA] + r[FIN_SIS:]
            if any(str(x).strip() for x in lib):
                libres[(r[ip], r[im])] = lib
    log(f"Planilla: {len(prev)} decisiones ya cargadas se conservan")

    # Decidir
    orden = {"SÍ": 0, "REVISAR": 1, "NO": 2, "YA ADHERIDA": 3, "COSTO DUDOSO": 4, "SIN COSTO": 5, "SIN PRECIO": 6}
    dec = sorted((f for f in filas if f["tipo"] not in CAMPANIAS_PROPIAS),
                 key=lambda f: (f["promo"], orden.get(f["sugerencia"], 9), -(f["x"] or 0)))
    if not enc_libres or not any(str(x).strip() for x in enc_libres):
        enc_libres = [""] * LIBRES
    n_lib = max(len(enc_libres), LIBRES)
    enc_libres = enc_libres + [""] * (n_lib - len(enc_libres))
    enc_total = DEC_ENC + enc_libres[:LIBRES] + EXTRA_ENC + enc_libres[LIBRES:]
    filas_dec = [enc_total]
    for f in dec:
        d, pp = prev.get((f["promo_id"], f["mla"]), ("", ""))
        lib = libres.get((f["promo_id"], f["mla"]), [])
        lib = lib + [""] * (n_lib - len(lib))
        filas_dec.append([f["promo_id"], f["promo"], f["tipo"], f["mla"], f["sku"], f["titulo"], f["estado_pub"],
                          _v(f["stock"]), _v(f["actual"]), _v(f["costo"]),
                          round(f["actual"] / f["costo"], 2) if f["actual"] and f["costo"] else "",
                          _v(f.get("minimo")), _v(f.get("maximo")), _v(f["precio_eval"]), _v(f.get("aporte_ml")),
                          _v(f["x"]),
                          round((1 - f["precio_eval"] / f["original"]) * 100, 1) if f["precio_eval"] and f.get("original") else "",
                          _v(f["recomendado"]), f["sugerencia"], d, pp, f.get("estado_item") or "",
                          *lib[:LIBRES], *extras(f, pp), *lib[LIBRES:]])
    sid, ncf = h.asegurar(T_DEC, len(filas_dec) + 50, len(enc_total))
    h.escribir(T_DEC, filas_dec)
    formato(h, sid, ncf, len(enc_total), col_sug=DEC_ENC.index("SUGERENCIA"), col_dec=I_DEC, editables=(I_DEC, I_PRE),
            anchos=[110, 180, 110, 110, 120, 320, 90, 60, 100, 100, 80, 100, 100, 110, 90, 90, 80, 110, 100, 90, 100, 90,
                    90, 90, 90, 90, 110, 90, 110, 100, 110, 90])

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
