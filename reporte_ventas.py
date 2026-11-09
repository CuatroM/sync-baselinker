"""
Reportes VENTAS — ventas de Mercado Libre y movimiento de dinero de Mercado Pago.

Solo lectura: no modifica nada en ML ni en MP.

Entradas (variables de entorno):
  PERIODO     vacío = mes en curso hasta hoy · "2026-10" = un mes · "2026-10-01:2026-10-15" = rango
              "ayer" · "hoy"
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
                 params={"group": "ML", "document_type": "BILL", "offset": 0, "limit": 24})
    if r.status_code != 200:
        log(f"Facturación ML no disponible ({r.status_code}): revisá el permiso de Facturación "
            "en la app del DevCenter.")
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
    return pagos, lib, mp.user_id


# ------------------------------------------------------------------ armado del Excel
def main():
    ventas = cargos = pagos = lib = pd.DataFrame()
    ml_user = mp_user = None

    codigo = os.environ.get("ML_CODIGO", "").strip()
    if codigo:
        ventas, cargos, ml_user = datos_ml(codigo)
    else:
        log("Sin código de Mercado Libre: se arma solo la parte de Mercado Pago.")

    if os.environ.get("MP_ACCESS_TOKEN"):
        pagos, lib, mp_user = datos_mp()
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
