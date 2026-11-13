"""
Planilla de Google "Mayorista y envíos ML": precio mayorista y envíos de las publicaciones "a acordar".

Hoja "Publicaciones": una fila por publicación activa o pausada.
  Columnas que edita el usuario: CANT. MAYORISTA, % DESC. MAYORISTA, % CREDIFIN, % VÍA CARGO, % CRUZ DEL SUR.
  Columnas que llena el sistema: el resto (costo de BaseLinker, precio ML, precios calculados, estado).
Hoja "Reglas": valores para publicaciones nuevas y mínimos de cada envío.
En cada corrida:
  - precio por cantidad (solo compradores empresa) = precio ML × (1 − % desc.) desde CANT. MAYORISTA unidades
  - envío personalizado SOLO si la publicación tiene envío "a acordar" (not_specified o custom):
      costo de envío = máx(costo BaseLinker × %, mínimo); % vacío o 0 = esa opción no se ofrece
  - con Mercado Envíos: no se tocan los envíos (columnas vacías, "Mercado Envíos")
  - se cambia Mercado Libre solo donde el valor calculado es distinto del último aplicado
Conexión con ML: igual que los demás procesos, con un código TG- de una autorización recién hecha (no se guarda ninguna llave).
DRY_RUN=1: escribe la planilla con lo que haría, sin tocar Mercado Libre. LIMITE: cambia en ML solo las primeras N.
"""
import json
import os
import time
from datetime import datetime, timezone, timedelta

import requests
import yaml
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

import sync
from ml import cliente
from ml.cliente import API, log, sku_de

dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
limite = int(os.environ.get("LIMITE") or 0)
cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
SHEET = cfg["planilla_ml"]["sheet_id"]
AR = timezone(timedelta(hours=-3))
ahora = datetime.now(AR).strftime("%d/%m/%Y %H:%M")

# ---------------- Google Sheets
gs = AuthorizedSession(service_account.Credentials.from_service_account_info(
    json.loads(os.environ["GOOGLE_SA_JSON"]), scopes=["https://www.googleapis.com/auth/spreadsheets"]))
GS = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET}"


def hojas():
    r = gs.get(GS, params={"fields": "sheets.properties"})
    r.raise_for_status()
    return {s["properties"]["title"]: s["properties"] for s in r.json()["sheets"]}


def asegurar(nombre, oculta=False):
    h = hojas()
    if nombre not in h:
        gs.post(f"{GS}:batchUpdate", json={"requests": [{"addSheet": {"properties": {"title": nombre, "hidden": oculta}}}]}).raise_for_status()
        h = hojas()
    return h[nombre]["sheetId"]


def leer(rango):
    r = gs.get(f"{GS}/values/{rango}", params={"valueRenderOption": "UNFORMATTED_VALUE"})
    r.raise_for_status()
    return r.json().get("values") or []


def escribir(rango, filas, crudo=False):
    gs.post(f"{GS}/values/{rango}:clear").raise_for_status()
    gs.put(f"{GS}/values/{rango}", params={"valueInputOption": "RAW" if crudo else "USER_ENTERED"},
           json={"values": filas}).raise_for_status()


# ---------------- conexión con Mercado Libre: a demanda, con un código TG- recién autorizado (no se guarda ninguna llave)
ml = cliente.ML(os.environ.get("ML_CODIGO"))

# ---------------- reglas
asegurar("Reglas")
REGLAS_DEF = [["REGLA", "VALOR", "MÍNIMO ($)", "NOTA", "MÁXIMO ($)"],
              ["CANT. MAYORISTA", 2, "", "desde cuántas unidades (para publicaciones nuevas)"],
              ["% DESC. MAYORISTA", "2%", "", "descuento sobre el precio de ML (para publicaciones nuevas)"],
              ["% CREDIFIN", "7%", 35000, "Correo Credifin a domicilio/sucursal: % del costo en BaseLinker", 279999],
              ["% VÍA CARGO", "20%", 50000, "Vía Cargo a sucursal", 279999],
              ["% CRUZ DEL SUR", "12%", 35000, "Cruz del Sur R. Negro/Patagonia", 279999]]
rg = leer("Reglas!A1:E10")
if len(rg) < 6:
    escribir("Reglas!A1:E10", REGLAS_DEF)
    rg = leer("Reglas!A1:E10")
elif len(rg[0]) < 5 or rg[0][4] != "MÁXIMO ($)":   # planilla anterior: se agrega la columna de máximo (ML anula envíos de $280.000 o más)
    gs.put(f"{GS}/values/Reglas!E1:E6", params={"valueInputOption": "RAW"},
           json={"values": [["MÁXIMO ($)"], [""], [""], [279999], [279999], [279999]]}).raise_for_status()
    rg = leer("Reglas!A1:E10")
R = {fila[0]: (fila[1] if len(fila) > 1 else "", fila[2] if len(fila) > 2 else "") for fila in rg[1:] if fila}
MAXIMO_R = {fila[0]: (fila[4] if len(fila) > 4 else "") for fila in rg[1:] if fila}


def pct(v):
    """'2%' -> 0.02 ; 0.02 (celda con formato %) -> 0.02 ; 2 -> 0.02 ; vacío -> None"""
    if v in (None, ""):
        return None
    t = str(v).strip()
    try:
        x = float(t.replace("%", "").replace(",", "."))
    except ValueError:
        return None
    if "%" in t or x > 1:
        return x / 100
    return x


def num(v, d=0.0):
    try:
        return float(str(v).replace("$", "").replace(".", "").replace(",", ".")) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        return d


ENVIOS = [("% CREDIFIN", "Correo Credifin a domicilio/sucursal"), ("% VÍA CARGO", "Vía Cargo a sucursal"),
          ("% CRUZ DEL SUR", "Cruz del Sur R. Negro/Patagonia")]
MINIMO = {k: num(R.get(k, ("", 0))[1]) for k, _ in ENVIOS}
MAXIMO = {k: min(num(MAXIMO_R.get(k), 279999) or 279999, 279999) for k, _ in ENVIOS}   # si el envío calculado supera esto, esa opción no se ofrece (ML anula envíos de $280.000 o más)
DEF = {"CANT. MAYORISTA": num(R.get("CANT. MAYORISTA", (2, ""))[0], 2), "% DESC. MAYORISTA": pct(R.get("% DESC. MAYORISTA", ("2%", ""))[0])}
DEF.update({k: pct(R.get(k, ("", ""))[0]) for k, _ in ENVIOS})
log(f"Reglas: {DEF} | mínimos: {MINIMO} | máximos: {MAXIMO}")

# ---------------- planilla actual (lo que editó el usuario + último aplicado)
COLS = ["MLA", "SKU", "TÍTULO", "COSTO (BASE)", "PRECIO ML", "CANT. MAYORISTA", "% DESC. MAYORISTA", "PRECIO MAYORISTA",
        "% CREDIFIN", "ENVÍO CREDIFIN", "% VÍA CARGO", "ENVÍO VÍA CARGO", "% CRUZ DEL SUR", "ENVÍO CRUZ DEL SUR",
        "TIPO DE ENVÍO", "ESTADO", "ACTUALIZADO", "MAYORISTA APLICADO", "ENVÍOS APLICADOS"]
asegurar("Publicaciones")
viejo = leer("Publicaciones!A1:S")
previo = {}
if viejo:
    h = viejo[0]
    for fila in viejo[1:]:
        d = dict(zip(h, fila + [""] * (len(h) - len(fila))))
        if d.get("MLA"):
            previo[d["MLA"]] = d
log(f"Planilla actual: {len(previo)} publicaciones")

# ---------------- costos de BaseLinker
bl = sync.BaseLinker(os.environ["BL_TOKEN"])
costo = {}
# DELFABRO: mientras no tenga inventario configurado en BaseLinker, el costo es el precio de la lista PDF guardado en la ficha
if os.path.exists("datos/delfabro_fichas.json"):
    for f in json.load(open("datos/delfabro_fichas.json", encoding="utf-8")):
        if float(f.get("precio_lista") or 0) > 0:
            costo[sync.normalizar_codigo(f["sku"] + "#DELFABRO")] = float(f["precio_lista"])
for sec in ("baselinker", "extrapower", "jya", "delfabro"):
    b = cfg.get(sec)
    if not b or not b.get("inventory_id"):
        continue
    for p in bl.catalogo(int(b["inventory_id"])).values():
        v = float((p.get("prices") or {}).get(str(b["price_group_id"])) or 0)
        if v > 0:
            costo[sync.normalizar_codigo(p.get("sku"))] = v


def costo_de(sku):
    s = sync.normalizar_codigo(sku)
    base = s.split("#")[0]
    return costo.get(s) or next((costo[base + x] for x in ("#FEMA", "#EXTRAPOWER", "#JYA", "#DELFABRO") if base + x in costo), None) or costo.get(base)


# ---------------- publicaciones
ids = []
for est in ("active", "paused"):
    ids += ml.ids_publicaciones(status=est)
ids = list(dict.fromkeys(ids))
log(f"Publicaciones activas y pausadas: {len(ids)} | {'SIMULACIÓN' if dry else 'REAL'}")

filas, cambios, n_cambios = [], {"mayorista": 0, "envios": 0, "errores": 0}, 0
ATR = "id,title,status,price,currency_id,shipping,seller_custom_field,attributes,tags"


def publicaciones(ids):
    """Como ml.publicaciones, pero si ML corta un pedido (403/429 pasajero) espera y reintenta; si sigue, las pide de a una."""
    for i in range(0, len(ids), 20):
        lote = ids[i:i + 20]
        for espera in (0, 20, 60, 120):
            if espera:
                log(f"ML respondió {r.status_code} al pedir publicaciones: espero {espera} s y reintento")
                time.sleep(espera)
            r = ml.pedir("GET", "/items", params={"ids": ",".join(lote), "attributes": ATR})
            if r.status_code == 200:
                break
        if r.status_code == 200:
            for x in r.json():
                if x.get("code") == 200:
                    yield x["body"]
            continue
        for iid in lote:
            r1 = ml.pedir("GET", f"/items/{iid}", params={"attributes": ATR})
            if r1.status_code == 200:
                yield r1.json()
            else:
                log(f"  {iid}: no se pudo leer ({r1.status_code}), se deja como está en la planilla")
                if iid in previo:
                    saltadas.append(iid)


saltadas = []
for it in publicaciones(ids):
    iid, sku = it["id"], sku_de(it)
    p = previo.get(iid, {})
    modo = (it.get("shipping") or {}).get("mode")
    precio = float(it.get("price") or 0)
    c = costo_de(sku)
    val = lambda k: p.get(k) if p.get(k) not in (None, "") else (DEF[k] if k in DEF else "")
    cant = int(num(val("CANT. MAYORISTA"), 0) or 0)
    dsc = pct(val("% DESC. MAYORISTA"))
    pm = round(precio * (1 - dsc), 2) if cant >= 2 and dsc else ""
    fila = {"MLA": iid, "SKU": sku, "TÍTULO": it.get("title"), "COSTO (BASE)": c or "", "PRECIO ML": precio,
            "CANT. MAYORISTA": cant or "", "% DESC. MAYORISTA": f"{dsc * 100:g}%" if dsc else "", "PRECIO MAYORISTA": pm,
            "TIPO DE ENVÍO": {"me2": "Mercado Envíos", "not_specified": "A acordar", "custom": "A acordar (envío personalizado)"}.get(modo, modo),
            "MAYORISTA APLICADO": p.get("MAYORISTA APLICADO", ""), "ENVÍOS APLICADOS": p.get("ENVÍOS APLICADOS", ""), "ACTUALIZADO": p.get("ACTUALIZADO", "")}
    estado = []
    tocar = not limite or n_cambios < limite
    # mayorista
    firma_m = f"{cant}|{pm}" if pm else ""
    if pm and str(p.get("MAYORISTA APLICADO", "")) != firma_m:
        if dry or not tocar:
            estado.append("mayorista: se aplicaría")
        else:
            r = ml.pedir("POST", f"/items/{iid}/prices/standard/quantity", json={"prices": [{
                "amount": pm, "currency_id": it.get("currency_id", "ARS"),
                "conditions": {"context_restrictions": ["channel_marketplace", "user_type_business"], "min_purchase_unit": cant}}]})
            if r.status_code in (200, 201):
                fila["MAYORISTA APLICADO"], fila["ACTUALIZADO"] = firma_m, ahora
                cambios["mayorista"] += 1
                estado.append("mayorista: aplicado")
            else:
                cambios["errores"] += 1
                estado.append(f"mayorista: ERROR {r.status_code} {r.text[:120]}")
            time.sleep(0.25)
    elif pm:
        estado.append("mayorista: ok")
    # envíos
    if modo in ("not_specified", "custom"):
        costos = []
        for k, desc in ENVIOS:
            pk = pct(val(k))
            fila[k] = f"{pk * 100:g}%" if pk else ""
            col = "ENVÍO " + k[2:]
            v = round(max(c * pk, MINIMO[k]), 2) if pk and c else 0
            if v and v <= MAXIMO[k]:
                fila[col] = v
                costos.append({"description": desc, "cost": v})
            else:   # supera el máximo (ML anula envíos de $280.000 o más): esa opción no se ofrece
                fila[col] = f"NO SE OFRECE (daría ${v:,.0f})".replace(",", ".") if v else ""
                if v:
                    estado.append(f"{k[2:].title()} no se ofrece: supera el máximo")
        firma_e = json.dumps(costos, ensure_ascii=False) if costos else ""
        if not c:
            estado.append("envíos: sin costo en BaseLinker")
        elif firma_e != str(p.get("ENVÍOS APLICADOS", "")):
            if dry or not tocar:
                estado.append("envíos: se aplicaría")
            else:
                cuerpo = {"shipping": {"mode": "custom", "local_pick_up": bool((it.get("shipping") or {}).get("local_pick_up")),
                                       "free_shipping": False, "methods": [], "costs": costos}} if costos else \
                         {"shipping": {"mode": "not_specified", "local_pick_up": bool((it.get("shipping") or {}).get("local_pick_up")), "free_shipping": False}}
                r = ml.put(f"/items/{iid}", cuerpo)
                if r.status_code in (200, 201):
                    fila["ENVÍOS APLICADOS"], fila["ACTUALIZADO"] = firma_e, ahora
                    cambios["envios"] += 1
                    estado.append("envíos: aplicado")
                else:
                    cambios["errores"] += 1
                    estado.append(f"envíos: ERROR {r.status_code} {r.text[:120]}")
                time.sleep(0.25)
        else:
            estado.append("envíos: ok")
    else:
        for k, _ in ENVIOS:
            fila[k] = fila["ENVÍO " + k[2:]] = ""
        fila["ENVÍOS APLICADOS"] = ""
    if any("aplicado" in e or "ERROR" in e for e in estado):
        n_cambios += 1
    fila["ESTADO"] = " | ".join(estado)
    filas.append(fila)

# las que ML no dejó leer esta vez quedan en la planilla como estaban
for iid in saltadas:
    f = {k: previo[iid].get(k, "") for k in COLS}
    f["ESTADO"] = f"no se pudo leer en esta corrida ({ahora}); queda como estaba"
    filas.append(f)
filas.sort(key=lambda f: (f["TIPO DE ENVÍO"] != "A acordar (envío personalizado)" and f["TIPO DE ENVÍO"] != "A acordar", str(f["SKU"])))
escribir("Publicaciones!A1:S", [COLS] + [[f.get(k, "") for k in COLS] for f in filas], crudo=True)
# formato: encabezado en negrita y congelado, columnas de estado interno ocultas
sid = hojas()["Publicaciones"]["sheetId"]
gs.post(f"{GS}:batchUpdate", json={"requests": [
    {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1}}, "fields": "gridProperties.frozenRowCount"}},
    {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                                                   "backgroundColor": {"red": 0.008, "green": 0.337, "blue": 0.102}}}, "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
    {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 17, "endIndex": 19},
                                   "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}}]}).raise_for_status()
# envíos que no se ofrecen por superar el máximo: celda en naranja (se rehace la regla en cada corrida)
cf = gs.get(GS, params={"fields": "sheets(properties.sheetId,conditionalFormats)"}).json()
n_cf = next((len(x.get("conditionalFormats") or []) for x in cf["sheets"] if x["properties"]["sheetId"] == sid), 0)
gs.post(f"{GS}:batchUpdate", json={"requests": [{"deleteConditionalFormatRule": {"sheetId": sid, "index": 0}} for _ in range(n_cf)] + [
    {"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [{"sheetId": sid, "startColumnIndex": i, "endColumnIndex": i + 1, "startRowIndex": 1} for i in (9, 11, 13)],
        "booleanRule": {"condition": {"type": "TEXT_STARTS_WITH", "values": [{"userEnteredValue": "NO SE OFRECE"}]},
                        "format": {"backgroundColor": {"red": 1, "green": 0.55, "blue": 0}, "textFormat": {"bold": True}}}}}}]}).raise_for_status()
log(f"Planilla escrita: {len(filas)} filas | cambios en ML: {cambios}")
