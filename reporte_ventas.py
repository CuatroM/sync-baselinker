"""
Reportes VENTAS — ventas de Mercado Libre y movimiento de dinero de Mercado Pago.

Solo lectura: no modifica nada en ML ni en MP.

Entradas (variables de entorno):
  PERIODO     vacío = mes en curso hasta hoy · "2026-10" = un mes · "2026-10-01:2026-10-15" = rango
              "ayer" · "hoy" · "completo" = últimos 12 meses (lo máximo que da ML por API)
  ML_CODIGO   código TG-... de Mercado Libre (opcional: sin código se arma solo la parte de MP)
  MP_ACCESS_TOKEN, ML_CLIENT_ID, ML_CLIENT_SECRET (secrets)

Salida: salida/reporte_ventas_<periodo>.xlsx con las hojas
  Resumen mensual · Ventas por día · Detalle de ventas · Por producto · Cargos ML
  Dinero liberado MP · Cobros MP

El repo es público: en el log solo se imprimen cantidades, nunca importes ni compradores.
"""
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

AR = timezone(timedelta(hours=-3))
os.makedirs("salida", exist_ok=True)


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ------------------------------------------------------------------ período
def periodo(texto):
    t = (texto or "").strip().lower()
    hoy = datetime.now(AR).replace(hour=0, minute=0, second=0, microsecond=0)
    if t in ("", "mes"):
        return hoy.replace(day=1), hoy + timedelta(days=1), f"{hoy:%Y-%m}"
    if t in ("completo", "todo", "12 meses"):
        # Mercado Libre solo devuelve por API las ventas de los últimos 12 meses
        d = hoy.replace(day=1)
        for _ in range(12):
            d = (d - timedelta(days=1)).replace(day=1)
        return d, hoy + timedelta(days=1), f"completo_{d:%Y-%m}_a_{hoy:%Y-%m-%d}"
    if t == "hoy":
        return hoy, hoy + timedelta(days=1), f"{hoy:%Y-%m-%d}"
    if t == "ayer":
        return hoy - timedelta(days=1), hoy, f"{hoy - timedelta(days=1):%Y-%m-%d}"
    if ":" in t:
        a, b = [x.strip() for x in t.split(":", 1)]
        d = datetime.strptime(a, "%Y-%m-%d").replace(tzinfo=AR)
        h = datetime.strptime(b, "%Y-%m-%d").replace(tzinfo=AR) + timedelta(days=1)
        return d, h, f"{a}_a_{b}"
    d = datetime.strptime(t, "%Y-%m").replace(tzinfo=AR)
    h = (d + timedelta(days=32)).replace(day=1)
    return d, min(h, hoy + timedelta(days=1)), t


DESDE, HASTA, ETIQUETA = periodo(os.environ.get("PERIODO"))
log(f"Período: {DESDE:%Y-%m-%d} a {HASTA - timedelta(days=1):%Y-%m-%d}")


def num(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------------ Mercado Libre
def datos_ml(codigo):
    from ml.cliente import ML
    ml = ML(codigo)

    # Órdenes, día por día (evita el tope de paginado de la búsqueda)
    ordenes, dia = [], DESDE
    while dia < HASTA:
        fin = min(dia + timedelta(days=1), HASTA)
        offset = 0
        while True:
            r = ml.pedir("GET", "/orders/search", params={
                "seller": ml.user_id, "sort": "date_asc", "limit": 50, "offset": offset,
                "order.date_created.from": dia.isoformat(timespec="milliseconds"),
                "order.date_created.to": (fin - timedelta(milliseconds=1)).isoformat(
                    timespec="milliseconds")})
            if r.status_code == 403:
                raise SystemExit("Mercado Libre no deja leer las ventas (403): habilitá el permiso "
                                 "de Órdenes/Ventas en la app del DevCenter.")
            if r.status_code != 200:
                log(f"Órdenes {dia:%Y-%m-%d}: error {r.status_code}")
                break
            lote = r.json().get("results") or []
            ordenes += lote
            if len(lote) < 50:
                break
            offset += 50
        dia = fin
    log(f"Órdenes de ML: {len(ordenes)}")

    # Costo de envío a cargo del vendedor
    costo_envio = {}
    for o in ordenes:
        sid = (o.get("shipping") or {}).get("id")
        if sid and sid not in costo_envio:
            r = ml.pedir("GET", f"/shipments/{sid}/costs", headers={"x-format-new": "true"})
            c = 0.0
            if r.status_code == 200:
                c = sum(num(s.get("cost")) for s in (r.json().get("senders") or [])
                        if str(s.get("user_id")) == str(ml.user_id))
            costo_envio[sid] = c
    log(f"Envíos consultados: {len(costo_envio)}")

    filas = []
    for o in ordenes:
        items = o.get("order_items") or []
        pagos = o.get("payments") or []
        aprobados = [p for p in pagos if p.get("status") == "approved"]
        sid = (o.get("shipping") or {}).get("id")
        envio = costo_envio.get(sid, 0.0)
        impuestos = sum(num(p.get("taxes_amount")) for p in aprobados)
        bruto_orden = sum(num(i.get("unit_price")) * num(i.get("quantity")) for i in items) or 1
        fecha = pd.Timestamp(o.get("date_created")).tz_convert("America/Argentina/Buenos_Aires")
        for i in items:
            it = i.get("item") or {}
            bruto = num(i.get("unit_price")) * num(i.get("quantity"))
            peso = bruto / bruto_orden      # reparte envío e impuestos entre los ítems
            comision = num(i.get("sale_fee")) * num(i.get("quantity"))
            filas.append({
                "Fecha": fecha.tz_localize(None), "Día": fecha.strftime("%Y-%m-%d"),
                "Mes": fecha.strftime("%Y-%m"), "Orden": str(o.get("id")),
                "Pack": str(o.get("pack_id") or ""), "Estado": o.get("status"),
                "Publicación": it.get("id"), "SKU": it.get("seller_sku") or "",
                "Producto": it.get("title"), "Cantidad": num(i.get("quantity")),
                "Precio unitario": num(i.get("unit_price")), "Bruto": bruto,
                "Comisión ML": comision, "Envío a cargo": round(envio * peso, 2),
                "Impuestos": round(impuestos * peso, 2),
                "Neto estimado": round(bruto - comision - envio * peso - impuestos * peso, 2),
                "Medio de pago": ", ".join(sorted({p.get("payment_type") or "" for p in aprobados})),
                "Cuotas": max([int(num(p.get("installments"))) for p in aprobados] or [0]),
                "Pago MP": ", ".join(str(p.get("id")) for p in aprobados),
                "Fecha liberación": ", ".join(sorted({str(p.get("money_release_date") or "")[:10]
                                                     for p in aprobados})),
                "Envío ID": str(sid or ""),
                "Comprador": (o.get("buyer") or {}).get("nickname"),
            })
    ventas = pd.DataFrame(filas)

    # Facturación de ML (lo que ML le cobra a la cuenta)
    cargos = []
    r = ml.pedir("GET", "/billing/integration/monthly/periods",
                 params={"group": "ML", "document_type": "BILL", "offset": 0, "limit": 12})
    if r.status_code != 200:
        log(f"Facturación ML no disponible ({r.status_code}): {r.text[:300]}")
    else:
        for p in r.json().get("results") or []:
            per = p.get("period") or {}
            ini, fin = str(per.get("date_from", ""))[:10], str(per.get("date_to", ""))[:10]
            if not ini or fin < f"{DESDE:%Y-%m-%d}" or ini > f"{HASTA:%Y-%m-%d}":
                continue
            offset = 0
            while True:
                d = ml.pedir("GET", f"/billing/integration/periods/key/{p.get('key')}/group/ML/details",
                             params={"document_type": "BILL", "limit": 1000, "offset": offset})
                if d.status_code != 200:
                    log(f"Detalle de facturación {p.get('key')}: error {d.status_code}")
                    break
                lote = d.json().get("results") or []
                for x in lote:
                    x["_periodo"] = f"{ini} a {fin}"
                cargos += lote
                if len(lote) < 1000:
                    break
                offset += 1000
        log(f"Cargos de facturación ML: {len(cargos)}")
    cargos = pd.json_normalize(cargos, sep=" · ") if cargos else pd.DataFrame()
    return ventas, cargos, ml.user_id


# ------------------------------------------------------------------ Mercado Pago
NOMBRES_MP = {
    "DATE": "Fecha", "SOURCE_ID": "ID operación", "EXTERNAL_REFERENCE": "Referencia",
    "RECORD_TYPE": "Tipo de registro", "DESCRIPTION": "Descripción",
    "NET_CREDIT_AMOUNT": "Crédito neto", "NET_DEBIT_AMOUNT": "Débito neto",
    "GROSS_AMOUNT": "Monto bruto", "MP_FEE_AMOUNT": "Comisión MP/ML",
    "FINANCING_FEE_AMOUNT": "Costo de cuotas", "SHIPPING_FEE_AMOUNT": "Costo de envío",
    "TAXES_AMOUNT": "Impuestos y retenciones", "COUPON_AMOUNT": "Cupón",
    "INSTALLMENTS": "Cuotas", "PAYMENT_METHOD": "Medio de pago", "TAX_DETAIL": "Detalle impuestos",
    "TRANSACTION_APPROVAL_DATE": "Fecha aprobación", "ORDER_ID": "Orden ML",
    "SHIPPING_ID": "Envío ID", "PACK_ID": "Pack", "BALANCE_AMOUNT": "Saldo",
}
IMPORTES_MP = ["NET_CREDIT_AMOUNT", "NET_DEBIT_AMOUNT", "GROSS_AMOUNT", "MP_FEE_AMOUNT",
               "FINANCING_FEE_AMOUNT", "SHIPPING_FEE_AMOUNT", "TAXES_AMOUNT", "COUPON_AMOUNT",
               "BALANCE_AMOUNT"]


def datos_mp():
    from mp.cliente import MP
    mp = MP()
    pagos = []
    for p in mp.pagos(DESDE, HASTA):
        fd = p.get("fee_details") or []
        fecha = pd.Timestamp(p.get("date_created")).tz_convert("America/Argentina/Buenos_Aires")
        pagos.append({
            "Fecha": fecha.tz_localize(None), "Día": fecha.strftime("%Y-%m-%d"),
            "ID pago": str(p.get("id")), "Estado": p.get("status"),
            "Detalle estado": p.get("status_detail"), "Operación": p.get("operation_type"),
            "Descripción": p.get("description"),
            "Orden ML": str((p.get("order") or {}).get("id") or ""),
            "Medio de pago": p.get("payment_method_id"), "Tipo": p.get("payment_type_id"),
            "Cuotas": p.get("installments"), "Monto": num(p.get("transaction_amount")),
            "Devuelto": num(p.get("transaction_amount_refunded")),
            "Comisiones": sum(num(f.get("amount")) for f in fd),
            "Impuestos": num(p.get("taxes_amount")),
            "Neto recibido": num((p.get("transaction_details") or {}).get("net_received_amount")),
            "Fecha liberación": str(p.get("money_release_date") or "")[:10],
            "Estado liberación": p.get("money_release_status"),
        })
    pagos = pd.DataFrame(pagos)
    log(f"Pagos de MP: {len(pagos)}")

    lib = mp.liberaciones(DESDE, HASTA)
    for c in IMPORTES_MP:
        if c in lib.columns:
            lib[c] = pd.to_numeric(lib[c], errors="coerce").fillna(0)

    # Movimientos de dinero: todas las transacciones + pagos/transferencias que hizo la cuenta
    mov = []
    tr = mp.transacciones(DESDE, HASTA)
    for _, x in tr.iterrows():
        tipo = str(x.get("TRANSACTION_TYPE") or "").upper()
        neto = num(x.get("SETTLEMENT_NET_AMOUNT"))
        bruto = num(x.get("TRANSACTION_AMOUNT"))
        medio = str(x.get("PAYMENT_METHOD_TYPE") or "")
        unidad = str(x.get("BUSINESS_UNIT") or "")
        orden = str(x.get("ORDER_ID") or x.get("EXTERNAL_REFERENCE") or "")
        if tipo in ("WITHDRAWAL", "PAYOUT", "PAYOUTS"):
            cat = "Egreso · Transferencia/retiro a cuenta bancaria"
        elif tipo == "WITHDRAWAL_CANCEL":
            cat = "Ingreso · Transferencia cancelada (vuelve a la cuenta)"
        elif tipo in ("REFUND", "CHARGEBACK", "DISPUTE"):
            cat = "Egreso · Devolución / reclamo / contracargo"
        elif neto < 0 and tipo == "SETTLEMENT":
            cat = "Egreso · Transferencia/pago desde el saldo"
        elif neto < 0:
            cat = "Egreso · Otros débitos"
        elif "mercado libre" in unidad.lower() or "MERCADOLIBRE" in unidad.upper().replace(" ", ""):
            cat = "Ingreso · Venta Mercado Libre"
        elif medio in ("bank_transfer", "account_money") and x.get("PAYER_NAME"):
            cat = "Ingreso · Transferencia recibida"
        elif medio in ("bank_transfer", "account_money"):
            cat = "Ingreso · Transferencia / ingreso de dinero"
        else:
            cat = "Ingreso · Cobro directo (QR, link, tarjeta)"
        fecha = pd.to_datetime(x.get("TRANSACTION_DATE"), errors="coerce", utc=True)
        fecha = fecha.tz_convert("America/Argentina/Buenos_Aires") if pd.notna(fecha) else None
        mov.append({
            "Fecha": fecha.tz_localize(None) if fecha is not None else None,
            "Día": fecha.strftime("%Y-%m-%d") if fecha is not None else "",
            "Mes": fecha.strftime("%Y-%m") if fecha is not None else "",
            "Categoría": cat, "Tipo MP": tipo, "Descripción": x.get("DESCRIPTION"),
            # en los egresos PAYER_NAME es la propia cuenta, no el destinatario
            "Contraparte": (x.get("PAYER_NAME") or "") if neto >= 0 else "",
            "Documento": " ".join(str(v) for v in (x.get("PAYER_ID_TYPE"), x.get("PAYER_ID_NUMBER"))
                                 if pd.notna(v) and v),
            "Banco / billetera origen": x.get("POI_BANK_NAME") or x.get("POI_WALLET_NAME") or "",
            "Canal": unidad, "Medio": medio, "Monto bruto": bruto,
            "Ingreso": neto if neto > 0 else 0.0, "Egreso": -neto if neto < 0 else 0.0,
            "Comisiones": num(x.get("FEE_AMOUNT")) + num(x.get("MKP_FEE_AMOUNT")),
            "Retenciones": num(x.get("TAXES_AMOUNT")), "Orden ML": orden,
            "ID operación": str(x.get("SOURCE_ID") or ""), "Cuenta destino": "",
            "Origen dato": "Transacciones MP",
        })
    enviados, apodos = 0, {}

    def apodo(cid):
        if cid not in apodos:
            try:
                r = requests.get(f"https://api.mercadolibre.com/users/{cid}", timeout=20)
                apodos[cid] = (r.json().get("nickname") or "") if r.status_code == 200 else ""
            except Exception:
                apodos[cid] = ""
        return apodos[cid]

    for p in mp.pagos_enviados(DESDE, HASTA):
        if p.get("status") not in ("approved", "authorized"):
            continue
        enviados += 1
        fecha = pd.Timestamp(p.get("date_created")).tz_convert("America/Argentina/Buenos_Aires")
        ad = p.get("additional_info") or {}
        monto = num(p.get("transaction_amount"))
        mov.append({
            "Fecha": fecha.tz_localize(None), "Día": fecha.strftime("%Y-%m-%d"),
            "Mes": fecha.strftime("%Y-%m"),
            "Categoría": ("Egreso · Transferencia enviada a cuenta MP"
                          if p.get("operation_type") == "money_transfer" else "Egreso · Pago realizado"),
            "Tipo MP": p.get("operation_type"), "Descripción": p.get("description"),
            "Contraparte": apodo(p.get("collector_id")), "Documento": "",
            "Banco / billetera origen": "", "Canal": "", "Medio": p.get("payment_type_id"),
            "Monto bruto": monto, "Ingreso": 0.0, "Egreso": monto, "Comisiones": 0.0,
            "Retenciones": 0.0, "Orden ML": str((p.get("order") or {}).get("id") or ""),
            "ID operación": str(p.get("id")),
            "Cuenta destino": f"{p.get('collector_id')} {apodo(p.get('collector_id'))}".strip(),
            "Origen dato": "Pagos enviados MP",
        })
    log(f"Pagos/transferencias enviados desde la cuenta: {enviados}")
    mov = pd.DataFrame(mov)
    if not mov.empty:
        mov = mov.drop_duplicates().sort_values("Fecha")
    log(f"Movimientos de dinero: {len(mov)}")
    return pagos, lib, mov, mp.user_id


# ------------------------------------------------------------------ armado del Excel
def main():
    ventas = cargos = pagos = lib = mov = pd.DataFrame()
    ml_user = mp_user = None

    codigo = os.environ.get("ML_CODIGO", "").strip()
    if codigo:
        ventas, cargos, ml_user = datos_ml(codigo)
    else:
        log("Sin código de Mercado Libre: se arma solo la parte de Mercado Pago.")

    if os.environ.get("MP_ACCESS_TOKEN"):
        pagos, lib, mov, mp_user = datos_mp()
    else:
        log("Sin MP_ACCESS_TOKEN: se arma solo la parte de Mercado Libre.")

    if ml_user and mp_user and str(ml_user) != str(mp_user):
        log("⚠️  ATENCIÓN: el token de Mercado Pago es de OTRA cuenta que la de Mercado Libre. "
            "Generalo desde la cuenta que vende en ML.")

    # Neto realmente liberado por MP, cruzado con cada venta por número de pago
    if not ventas.empty and not lib.empty and "SOURCE_ID" in lib.columns:
        neto = (lib.assign(n=lib.get("NET_CREDIT_AMOUNT", 0) - lib.get("NET_DEBIT_AMOUNT", 0))
                .groupby("SOURCE_ID")["n"].sum())
        def liberado(ids):
            return sum(neto.get(i.strip(), 0) for i in str(ids).split(",") if i.strip())
        por_orden = ventas.groupby("Orden")["Pago MP"].first().map(liberado)
        ventas["Neto liberado MP (orden)"] = ventas["Orden"].map(por_orden)
        # se muestra una sola vez por orden para no duplicar en órdenes con varios ítems
        ventas.loc[ventas.duplicated("Orden"), "Neto liberado MP (orden)"] = None

    hojas = {}
    validas = ventas[~ventas["Estado"].isin(["cancelled", "invalid"])] if not ventas.empty else ventas
    sumas = ["Cantidad", "Bruto", "Comisión ML", "Envío a cargo", "Impuestos", "Neto estimado"]

    if not validas.empty:
        def resumir(g):
            agg = validas.groupby(g).agg(Ventas=("Orden", "nunique"), **{c: (c, "sum") for c in sumas})
            agg["Ticket promedio"] = (agg["Bruto"] / agg["Ventas"]).round(2)
            return agg.reset_index()
        mensual = resumir("Mes")
        diario = resumir("Día")
    else:
        mensual = diario = pd.DataFrame()

    if not lib.empty and "DATE" in lib.columns:
        lib["_mes"] = pd.to_datetime(lib["DATE"], errors="coerce", utc=True) \
            .dt.tz_convert("America/Argentina/Buenos_Aires").dt.strftime("%Y-%m")
        lib["_dia"] = pd.to_datetime(lib["DATE"], errors="coerce", utc=True) \
            .dt.tz_convert("America/Argentina/Buenos_Aires").dt.strftime("%Y-%m-%d")
        cols = [c for c in ("NET_CREDIT_AMOUNT", "NET_DEBIT_AMOUNT", "TAXES_AMOUNT") if c in lib.columns]
        for clave, base in (("_mes", "Mes"), ("_dia", "Día")):
            m = lib.groupby(clave)[cols].sum().rename(columns={
                "NET_CREDIT_AMOUNT": "MP: ingresos liberados", "NET_DEBIT_AMOUNT": "MP: egresos",
                "TAXES_AMOUNT": "MP: retenciones"})
            m["MP: neto del período"] = m.get("MP: ingresos liberados", 0) - m.get("MP: egresos", 0)
            m.index.name = base
            m = m.reset_index()
            if base == "Mes":
                mensual = m if mensual.empty else mensual.merge(m, on="Mes", how="outer")
            else:
                diario = m if diario.empty else diario.merge(m, on="Día", how="outer")
        lib = lib.drop(columns=["_mes", "_dia"])

    if not mensual.empty:
        hojas["Resumen mensual"] = mensual.sort_values("Mes")
    if not diario.empty:
        hojas["Ventas por día"] = diario.sort_values("Día")
    if not ventas.empty:
        hojas["Detalle de ventas"] = ventas.sort_values("Fecha")
        prod = validas.groupby(["SKU", "Producto"]).agg(
            Ventas=("Orden", "nunique"), **{c: (c, "sum") for c in sumas}).reset_index()
        hojas["Por producto"] = prod.sort_values("Bruto", ascending=False)
    if not cargos.empty:
        hojas["Cargos ML"] = cargos
    if not mov.empty:
        res = mov.pivot_table(index="Mes", columns="Categoría", values=["Ingreso", "Egreso"],
                              aggfunc="sum", fill_value=0)
        res = pd.concat([res["Ingreso"].loc[:, lambda d: d.columns.str.startswith("Ingreso")],
                         res["Egreso"].loc[:, lambda d: d.columns.str.startswith("Egreso")]], axis=1)
        res["TOTAL INGRESOS"] = mov.groupby("Mes")["Ingreso"].sum()
        res["TOTAL EGRESOS"] = mov.groupby("Mes")["Egreso"].sum()
        res["RESULTADO"] = res["TOTAL INGRESOS"] - res["TOTAL EGRESOS"]
        hojas["Resumen de dinero"] = res.astype(float).reset_index()
        hojas["Movimientos de dinero"] = mov
        egr = mov[mov["Egreso"] > 0].copy()
        if not egr.empty:
            # clave única por egreso (un mismo ID puede tener reclamo y devolución)
            dup = egr.duplicated("ID operación", keep=False)
            egr.loc[dup, "ID operación"] = egr.loc[dup, "ID operación"] + "·" + egr.loc[dup, "Tipo MP"]
            a_identificar = egr[~egr["Categoría"].str.contains("Devolución")]
            ident = None
            try:
                import planilla_egresos
                ident = planilla_egresos.sincronizar(a_identificar, log)
            except Exception as e:
                log(f"Planilla de proveedores: no se pudo actualizar ({type(e).__name__}: {str(e)[:200]})")
            if ident is not None and not ident.empty:
                egr = egr.merge(ident, on="ID operación", how="left")
            else:
                egr["Proveedor"] = egr["Alias / CBU"] = egr["Titular"] = ""
            cols = ["Fecha", "Día", "Mes", "Categoría", "Proveedor", "Alias / CBU", "Titular",
                    "Egreso", "Descripción", "Cuenta destino", "Contraparte", "Medio", "Tipo MP",
                    "ID operación", "Origen dato"]
            hojas["Egresos"] = egr[[c for c in cols if c in egr.columns]]
            pp = egr[~egr["Categoría"].str.contains("Devolución")].copy()
            pp["Proveedor"] = pp["Proveedor"].fillna("").replace("", "(sin identificar)")
            hojas["Egresos por proveedor"] = (pp.pivot_table(index="Proveedor", columns="Mes",
                                              values="Egreso", aggfunc="sum", fill_value=0)
                                              .assign(TOTAL=lambda d: d.sum(axis=1))
                                              .sort_values("TOTAL", ascending=False)
                                              .astype(float).reset_index())
        ent = mov[(mov["Ingreso"] > 0) & (mov["Categoría"] != "Ingreso · Venta Mercado Libre")]
        if not ent.empty:
            hojas["Ingresos fuera de ML"] = ent
    if not lib.empty:
        hojas["Dinero liberado MP"] = lib.rename(columns=NOMBRES_MP)
    if not pagos.empty:
        hojas["Cobros MP"] = pagos.sort_values("Fecha")

    if not hojas:
        raise SystemExit("No hubo datos para el período elegido.")

    archivo = f"salida/reporte_ventas_{ETIQUETA}.xlsx"
    with pd.ExcelWriter(archivo, engine="openpyxl") as xl:
        for nombre, df in hojas.items():
            df.to_excel(xl, sheet_name=nombre[:31], index=False)
            hoja = xl.sheets[nombre[:31]]
            hoja.freeze_panes = "A2"
            hoja.auto_filter.ref = hoja.dimensions
            for i, col in enumerate(df.columns, start=1):
                letra = hoja.cell(row=1, column=i).column_letter
                largo = max([len(str(col))] + [len(str(v)) for v in df[col].head(300)])
                hoja.column_dimensions[letra].width = min(max(10, largo + 2), 45)
                if pd.api.types.is_float_dtype(df[col]) and col not in ("Cantidad", "Cuotas"):
                    for celda in hoja[letra][1:]:
                        celda.number_format = '"$" #,##0.00'
    log(f"Listo: {archivo} — hojas: {', '.join(hojas)}")


if __name__ == "__main__":
    main()
