"""
Planilla de Google "Proveedores y egresos MP": identifica a quién fue cada egreso de Mercado Pago.

La API de Mercado Pago no informa el destinatario de las transferencias que hace la cuenta,
así que el usuario lo indica una vez y el sistema lo recuerda.

Hoja "Proveedores" (la completa el usuario):
  PROVEEDOR | ALIAS | CBU / CVU | TITULAR DE LA CUENTA | CUIT | NOTAS
Hoja "Egresos" (la llena el sistema en cada corrida, conservando lo que escribió el usuario):
  FECHA | TIPO | MONTO | ID OPERACIÓN | DESCRIPCIÓN | CUENTA DESTINO (MP) |
  PROVEEDOR ✎ | ALIAS / CBU ✎ | TITULAR (auto) | NOTAS ✎
  - Se elige el PROVEEDOR de la lista, o se escribe el ALIAS / CBU: el sistema completa el otro.
  - Si un egreso va a una cuenta de Mercado Pago que ya tiene proveedor asignado en otra fila,
    el sistema le asigna el mismo proveedor solo.
Config: config.yaml → planilla_egresos.sheet_id · Secret: GOOGLE_SA_JSON
"""
import json
import os

import pandas as pd
import yaml
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

ENCAB_PROV = ["PROVEEDOR", "ALIAS", "CBU / CVU", "TITULAR DE LA CUENTA", "CUIT", "NOTAS"]
ENCAB_EGR = ["FECHA", "TIPO", "MONTO", "ID OPERACIÓN", "DESCRIPCIÓN", "CUENTA DESTINO (MP)",
             "PROVEEDOR ✎", "ALIAS / CBU ✎", "TITULAR (auto)", "NOTAS ✎"]


def _txt(x):
    """Texto limpio para Google Sheets (sin NaN ni None)."""
    if x is None or (isinstance(x, float) and x != x):
        return ""
    return str(x).strip()


def _norm(x):
    return str(x or "").strip().lower().replace(" ", "")


def sincronizar(egresos, log):
    """egresos: DataFrame con Fecha, Categoría, Egreso, ID operación, Descripción, Cuenta destino.
    Devuelve DataFrame (ID operación → Proveedor, Alias / CBU, Titular) o None si no hay planilla."""
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8")) or {}
    sheet = ((cfg.get("planilla_egresos") or {}).get("sheet_id") or "").strip()
    if not sheet or not os.environ.get("GOOGLE_SA_JSON"):
        log("Planilla de proveedores no configurada (planilla_egresos.sheet_id): se omite.")
        return None
    info = json.loads(os.environ["GOOGLE_SA_JSON"])
    gs = AuthorizedSession(service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]))
    GS = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet}"
    r = gs.get(GS, params={"fields": "sheets.properties"})
    if r.status_code in (403, 404):
        log(f"No tengo acceso a la planilla de proveedores ({r.status_code}). Compartila como "
            f"Editor con: {info.get('client_email')}")
        return None
    r.raise_for_status()
    hojas = {s["properties"]["title"]: s["properties"]["sheetId"] for s in r.json()["sheets"]}

    def asegurar(nombre):
        if nombre not in hojas:
            x = gs.post(f"{GS}:batchUpdate", json={"requests": [{"addSheet": {"properties": {
                "title": nombre, "gridProperties": {"frozenRowCount": 1}}}}]})
            x.raise_for_status()
            hojas[nombre] = x.json()["replies"][0]["addSheet"]["properties"]["sheetId"]
        return hojas[nombre]

    def leer(rango):
        x = gs.get(f"{GS}/values/{rango}", params={"valueRenderOption": "UNFORMATTED_VALUE"})
        return (x.json().get("values") or []) if x.status_code == 200 else []

    # ---------------- Proveedores
    id_prov = asegurar("Proveedores")
    prov = leer("Proveedores!A1:F")
    if not prov:
        gs.put(f"{GS}/values/Proveedores!A1", params={"valueInputOption": "RAW"},
               json={"values": [ENCAB_PROV]}).raise_for_status()
        prov = [ENCAB_PROV]
    proveedores = []
    for f in prov[1:]:
        f = (list(f) + [""] * 6)[:6]
        if str(f[0]).strip():
            proveedores.append({"prov": str(f[0]).strip(), "alias": str(f[1]).strip(),
                                "cbu": str(f[2]).strip(), "titular": str(f[3]).strip()})
    por_nombre = {_norm(p["prov"]): p for p in proveedores}
    por_cuenta = {}
    for p in proveedores:
        for k in (p["alias"], p["cbu"]):
            if k:
                por_cuenta[_norm(k)] = p

    # ---------------- Egresos: conservar lo que cargó el usuario
    id_egr = asegurar("Egresos")
    previos = {}
    for f in leer("Egresos!A2:J")[0:]:
        f = (list(f) + [""] * 10)[:10]
        if str(f[3]).strip():
            previos[str(f[3]).strip()] = f
    # aprender: cuenta MP destino → proveedor (de filas ya asignadas)
    por_destino = {}
    for f in previos.values():
        if str(f[5]).strip() and str(f[6]).strip():
            por_destino[str(f[5]).strip()] = str(f[6]).strip()

    filas, resultado = {}, []
    for _, e in egresos.iterrows():
        idop = str(e["ID operación"])
        ant = previos.get(idop, [""] * 10)
        monto = pd.to_numeric(e.get("Egreso"), errors="coerce")
        filas[idop] = [_txt(e.get("Fecha"))[:16], _txt(e.get("Categoría")).replace("Egreso · ", ""),
                       round(float(monto), 2) if pd.notna(monto) else 0, idop,
                       _txt(e.get("Descripción")), _txt(e.get("Cuenta destino")),
                       _txt(ant[6]), _txt(ant[7]), "", _txt(ant[9])]
    for idop, f in previos.items():          # egresos de corridas anteriores fuera del período
        filas.setdefault(idop, [x if isinstance(x, (int, float)) and x == x else _txt(x) for x in f])

    asignados = 0
    for idop, f in filas.items():
        prov_, cta = str(f[6]).strip(), str(f[7]).strip()
        if not prov_ and cta and _norm(cta) in por_cuenta:
            prov_ = por_cuenta[_norm(cta)]["prov"]
        if not prov_ and str(f[5]).strip() in por_destino:
            prov_ = por_destino[str(f[5]).strip()]
        p = por_nombre.get(_norm(prov_)) or (por_cuenta.get(_norm(cta)) if cta else None)
        if p and not cta:
            cta = p["alias"] or p["cbu"]
        f[6], f[7], f[8] = prov_, cta, (p or {}).get("titular", "")
        asignados += bool(prov_)
        resultado.append({"ID operación": idop, "Proveedor": prov_, "Alias / CBU": cta,
                          "Titular": f[8]})

    orden = sorted(filas.values(), key=lambda f: str(f[0]), reverse=True)
    # TITULAR como fórmula para que se actualice apenas el usuario elige el proveedor
    for i, f in enumerate(orden, start=2):
        if not f[8]:
            f[8] = (f'=IFERROR(VLOOKUP(G{i},Proveedores!A:D,4,FALSE),'
                    f'IFERROR(INDEX(Proveedores!D:D,MATCH(H{i},Proveedores!B:B,0)),""))')
    gs.post(f"{GS}/values/Egresos!A1:J:clear").raise_for_status()
    gs.put(f"{GS}/values/Egresos!A1", params={"valueInputOption": "USER_ENTERED"},
           json={"values": [ENCAB_EGR] + orden}).raise_for_status()

    # formato: encabezados, lista desplegable de proveedores, columnas editables resaltadas
    n = len(orden) + 1
    req = [
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                 "backgroundColor": {"red": .9, "green": .9, "blue": .9}}},
                        "fields": "userEnteredFormat(textFormat,backgroundColor)"}}
        for sid in (id_prov, id_egr)]
    req += [
        {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1}},
                                   "fields": "gridProperties.frozenRowCount"}} for sid in (id_prov, id_egr)]
    req += [
        {"setDataValidation": {"range": {"sheetId": id_egr, "startRowIndex": 1, "endRowIndex": max(n, 2),
                                         "startColumnIndex": 6, "endColumnIndex": 7},
                               "rule": {"condition": {"type": "ONE_OF_RANGE", "values": [
                                   {"userEnteredValue": "=Proveedores!$A$2:$A"}]},
                                   "showCustomUi": True, "strict": False}}},
        {"repeatCell": {"range": {"sheetId": id_egr, "startRowIndex": 1, "endRowIndex": max(n, 2),
                                  "startColumnIndex": 6, "endColumnIndex": 8},
                        "cell": {"userEnteredFormat": {"backgroundColor": {"red": 1, "green": .97, "blue": .8}}},
                        "fields": "userEnteredFormat.backgroundColor"}},
        {"repeatCell": {"range": {"sheetId": id_egr, "startRowIndex": 1, "endRowIndex": max(n, 2),
                                  "startColumnIndex": 2, "endColumnIndex": 3},
                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY",
                                                                         "pattern": "\"$\" #,##0.00"}}},
                        "fields": "userEnteredFormat.numberFormat"}},
        {"autoResizeDimensions": {"dimensions": {"sheetId": id_egr, "dimension": "COLUMNS",
                                                 "startIndex": 0, "endIndex": 10}}},
        {"autoResizeDimensions": {"dimensions": {"sheetId": id_prov, "dimension": "COLUMNS",
                                                 "startIndex": 0, "endIndex": 6}}},
    ]
    gs.post(f"{GS}:batchUpdate", json={"requests": req})
    log(f"Planilla de proveedores: {len(orden)} egresos, {asignados} con proveedor identificado, "
        f"{len(proveedores)} proveedores cargados")
    return pd.DataFrame(resultado)
