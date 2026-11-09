"""
Conexión con la API de Mercado Pago (solo lectura).

Usa el Access Token de producción de la aplicación "Reportes VENTAS", guardado en el
secret MP_ACCESS_TOKEN. A diferencia de Mercado Libre, no hace falta autorizar en cada
corrida.

Importante: el repo es público y los logs de Actions se ven desde afuera, así que acá
nunca se imprimen importes ni datos de compradores, solo cantidades.
"""
import io
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

API = "https://api.mercadopago.com"


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# Columnas del reporte de "dinero liberado". Si Mercado Pago rechaza alguna, se reintenta
# con la lista mínima.
COLUMNAS_LIBERACIONES = [
    "DATE", "SOURCE_ID", "EXTERNAL_REFERENCE", "RECORD_TYPE", "DESCRIPTION",
    "NET_CREDIT_AMOUNT", "NET_DEBIT_AMOUNT", "GROSS_AMOUNT", "MP_FEE_AMOUNT",
    "FINANCING_FEE_AMOUNT", "SHIPPING_FEE_AMOUNT", "TAXES_AMOUNT", "COUPON_AMOUNT",
    "INSTALLMENTS", "PAYMENT_METHOD", "TAX_DETAIL", "TRANSACTION_APPROVAL_DATE",
    "ORDER_ID", "SHIPPING_ID", "PACK_ID", "BALANCE_AMOUNT",
]
COLUMNAS_MINIMAS = ["DATE", "SOURCE_ID", "EXTERNAL_REFERENCE", "RECORD_TYPE", "DESCRIPTION",
                    "NET_CREDIT_AMOUNT", "NET_DEBIT_AMOUNT", "GROSS_AMOUNT"]


def leer_csv(contenido):
    """El reporte viene separado por comas, pero algunos campos (detalle de impuestos)
    traen separadores adentro; se prueba en orden y, como último recurso, se saltean
    las filas mal formadas avisando cuántas fueron."""
    for sep in (",", ";"):
        try:
            return pd.read_csv(io.BytesIO(contenido), sep=sep, dtype=str)
        except Exception:
            pass
    total = contenido.count(b"\n")
    df = pd.read_csv(io.BytesIO(contenido), sep=",", dtype=str, engine="python",
                     on_bad_lines="skip")
    log(f"⚠️  {max(total - 1 - len(df), 0)} filas del reporte de MP venían mal formadas y se saltearon")
    return df


class MP:
    def __init__(self):
        token = (os.environ.get("MP_ACCESS_TOKEN") or "").strip()
        if not token:
            raise SystemExit("Falta el secret MP_ACCESS_TOKEN en GitHub.")
        self.ses = requests.Session()
        self.ses.headers["Authorization"] = f"Bearer {token}"
        r = self.pedir("GET", "/users/me")
        if r.status_code != 200:
            raise SystemExit(f"Mercado Pago rechazó el token ({r.status_code}). Revisá que "
                             "MP_ACCESS_TOKEN sea el Access Token de PRODUCCIÓN (APP_USR-...).")
        self.user_id = r.json().get("id")
        log(f"Conectado a Mercado Pago (usuario {self.user_id})")

    def pedir(self, metodo, ruta, reintentos=4, **kw):
        r = None
        for intento in range(1, reintentos + 1):
            r = self.ses.request(metodo, f"{API}{ruta}", timeout=90, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(5 * intento)
                continue
            return r
        return r

    # ---------- Pagos recibidos (sincrónico) ----------
    def pagos(self, desde, hasta):
        """Todos los pagos cobrados entre dos datetime (con zona), día por día."""
        dia = desde
        while dia < hasta:
            fin = min(dia + timedelta(days=1), hasta)
            offset = 0
            while True:
                r = self.pedir("GET", "/v1/payments/search", params={
                    "range": "date_created", "begin_date": dia.isoformat(timespec="milliseconds"),
                    "end_date": fin.isoformat(timespec="milliseconds"), "sort": "date_created",
                    "criteria": "asc", "limit": 100, "offset": offset})
                if r.status_code != 200:
                    log(f"Pagos {dia:%Y-%m-%d}: error {r.status_code}")
                    break
                lote = r.json().get("results") or []
                yield from lote
                if len(lote) < 100:
                    break
                offset += 100
            dia = fin

    # ---------- Reporte de dinero liberado (asincrónico) ----------
    def _config_liberaciones(self):
        cuerpo = {"columns": [{"key": k} for k in COLUMNAS_LIBERACIONES],
                  "file_name_prefix": "reportes-ventas", "display_timezone": "GMT-03",
                  "report_translation": "en", "separator": ",",
                  "include_withdrawal_at_end": False, "execute_after_withdrawal": False,
                  "scheduled": False,
                  # MP exige una frecuencia aunque no se programe nada (scheduled = False)
                  "frequency": {"type": "monthly", "value": 1, "hour": 6}}
        actual = self.pedir("GET", "/v1/account/release_report/config")
        metodo = "PUT" if actual.status_code == 200 else "POST"
        r = self.pedir(metodo, "/v1/account/release_report/config", json=cuerpo)
        if r.status_code >= 400:
            log(f"Config del reporte rechazada ({r.status_code}); pruebo con columnas mínimas")
            cuerpo["columns"] = [{"key": k} for k in COLUMNAS_MINIMAS]
            r = self.pedir(metodo, "/v1/account/release_report/config", json=cuerpo)
            if r.status_code >= 400:
                log(f"No se pudo configurar el reporte ({r.status_code}): {r.text[:300]}")

    def _listar(self):
        r = self.pedir("GET", "/v1/account/release_report/list")
        if r.status_code != 200:
            r = self.pedir("POST", "/v1/account/release_report/list")
        return r.json() if r.status_code == 200 else []

    def liberaciones(self, desde, hasta):
        """Reporte de dinero liberado del período. MP acepta hasta 90 días por pedido, así que
        se pide en tramos de 85 días y se juntan."""
        self._config_liberaciones()
        # MP no acepta fechas futuras: el último tramo termina en este momento
        ahora = datetime.now(desde.tzinfo) - timedelta(minutes=5)
        hasta = min(hasta, ahora)
        partes, ini = [], desde
        while ini < hasta:
            fin = min(ini + timedelta(days=85), hasta)
            try:
                df = self._liberaciones_tramo(ini, fin)
            except Exception as e:
                log(f"Tramo {ini:%Y-%m-%d} a {fin:%Y-%m-%d}: no se pudo leer ({type(e).__name__})")
                df = pd.DataFrame()
            if not df.empty:
                partes.append(df)
            ini = fin
        if not partes:
            return pd.DataFrame()
        todo = pd.concat(partes, ignore_index=True).drop_duplicates()
        log(f"Dinero liberado: {len(todo)} movimientos en total")
        return todo

    def _liberaciones_tramo(self, desde, hasta, espera_max=900):
        previos = {x.get("file_name") for x in self._listar()}
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        cuerpo = {"begin_date": pd.Timestamp(desde).tz_convert("UTC").strftime(fmt),
                  "end_date": pd.Timestamp(hasta).tz_convert("UTC").strftime(fmt)}
        for _ in range(3):
            r = self.pedir("POST", "/v1/account/release_report", json=cuerpo)
            if r.status_code in (200, 201, 202):
                break
            time.sleep(10)
        else:
            log(f"Mercado Pago no aceptó el pedido del reporte ({r.status_code}): {r.text[:300]}")
            return pd.DataFrame()
        log("Reporte de dinero liberado pedido; esperando a que Mercado Pago lo genere…")
        inicio = time.time()
        while time.time() - inicio < espera_max:
            time.sleep(20)
            nuevos = [x for x in self._listar() if x.get("file_name") not in previos
                      and str(x.get("file_name", "")).endswith(".csv")]
            if nuevos:
                nombre = nuevos[-1]["file_name"]
                d = self.pedir("GET", f"/v1/account/release_report/{nombre}")
                if d.status_code == 200 and d.content:
                    df = leer_csv(d.content)
                    df.columns = [str(c).strip().upper().replace(" ", "_") for c in df.columns]
                    log(f"Tramo {desde:%Y-%m-%d} a {hasta:%Y-%m-%d}: {len(df)} movimientos")
                    return df
        log("El reporte no estuvo listo a tiempo; probá correrlo de nuevo en unos minutos.")
        return pd.DataFrame()
