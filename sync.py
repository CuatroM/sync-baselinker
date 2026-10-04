"""
Sincronización de precio y stock: Excel del proveedor -> BaseLinker (Base.com)

Flujo:
  1. Descarga la lista de precios/stock (Excel o CSV) del sitio del proveedor.
  2. Lee las columnas configuradas en config.yaml (código, precio, stock).
  3. Trae el catálogo de BaseLinker y vincula por SKU o EAN.
  4. Actualiza SOLO los productos cuyo precio o stock cambió
     (lotes de 1000, respetando el límite de 100 pedidos/minuto).
  5. Deja un reporte CSV con lo actualizado y los códigos no encontrados.

Variables de entorno (en GitHub van como "Secrets"):
  BL_TOKEN          token de la API de BaseLinker (obligatorio)
  PROVEEDOR_USER    usuario del sitio del proveedor (si pide login)
  PROVEEDOR_PASS    contraseña del sitio del proveedor (si pide login)
  DRY_RUN=1         simula todo sin escribir en BaseLinker
"""

import csv
import io
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import yaml

BL_URL = "https://api.baselinker.com/connector.php"
LOTE = 1000              # máximo de productos por llamada de actualización
PAUSA_API = 0.7          # segundos entre llamadas (~85/min, debajo del límite de 100)
BASE = Path(__file__).parent


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------

def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def a_numero(valor):
    """Convierte '$ 1.234,56', '1234.56', 1234 -> 1234.56. Devuelve None si no puede."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    s = re.sub(r"[^\d,.\-]", "", str(valor))
    if not s or s in "-.,":
        return None
    if "," in s and "." in s:
        # el separador que aparece último es el decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if s.count(",") == 1 else s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def normalizar_codigo(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    s = str(valor).strip()
    if s.endswith(".0") and s[:-2].isdigit():   # Excel convierte códigos numéricos a float
        s = s[:-2]
    return s.upper()


# ----------------------------------------------------------------------------
# 1. Descarga del proveedor
# ----------------------------------------------------------------------------

def descargar_lista(cfg):
    p = cfg["proveedor"]
    ses = requests.Session()
    ses.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
    usuario = os.environ.get("PROVEEDOR_USER", "")
    clave = os.environ.get("PROVEEDOR_PASS", "")
    modo = p.get("login", "ninguno")

    if modo == "basic":
        ses.auth = (usuario, clave)
    elif modo == "formulario":
        campos = dict(p.get("login_campos_extra") or {})
        campos[p["login_campo_usuario"]] = usuario
        campos[p["login_campo_clave"]] = clave
        r = ses.post(p["login_url"], data=campos, timeout=60)
        r.raise_for_status()
        if 'type="password"' in r.text or "type='password'" in r.text:
            raise RuntimeError("El login en el sitio del proveedor falló "
                               "(revisar PROVEEDOR_USER / PROVEEDOR_PASS).")
        log("Login en el sitio del proveedor OK")

    log(f"Descargando lista: {p['url_lista']}")
    if p.get("datos_descarga"):          # descarga por formulario (botón que hace POST)
        r = ses.post(p["url_lista"], data=p["datos_descarga"], timeout=300)
    else:
        r = ses.get(p["url_lista"], timeout=300)
    r.raise_for_status()
    contenido = r.content
    nombre = r.headers.get("content-disposition", "")
    log(f"Recibido: {len(contenido):,} bytes | {r.headers.get('content-type')} | {nombre}")
    if contenido[:15].lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise RuntimeError(
            "El proveedor devolvió una página web en lugar del archivo. "
            "Probablemente el login falló o cambió el link de descarga."
        )
    return contenido


def leer_lista(contenido, cfg):
    p = cfg["proveedor"]
    col = cfg["columnas"]
    fila_enc = int(p.get("fila_encabezado", 1)) - 1
    formato = p.get("formato", "auto")
    if formato == "auto":
        formato = "excel" if contenido[:2] in (b"PK", b"\xd0\xcf") else "csv"

    if formato == "excel":
        df = pd.read_excel(io.BytesIO(contenido), sheet_name=p.get("hoja") or 0,
                           header=fila_enc, dtype=object)
    else:
        texto = contenido.decode(p.get("codificacion", "utf-8-sig"), errors="replace")
        df = pd.read_csv(io.StringIO(texto), sep=p.get("separador_csv") or None,
                         engine="python", header=fila_enc, dtype=object)

    df.columns = [str(c).strip() for c in df.columns]
    faltan = [c for c in (col["codigo"], col.get("precio"), col.get("stock"))
              if c and c not in df.columns]
    if faltan:
        raise RuntimeError(f"No encuentro las columnas {faltan} en el archivo. "
                           f"Columnas disponibles: {list(df.columns)}")

    reglas = cfg.get("reglas", {})
    mult = float(reglas.get("multiplicador_precio", 1) or 1)
    dec = int(reglas.get("decimales_precio", 2))
    textos_stock = {str(k).strip().lower(): v
                    for k, v in (reglas.get("stock_texto") or {}).items()}
    stock_max = reglas.get("stock_maximo")
    stock_min_publicar = int(reglas.get("stock_minimo_publicar", 0) or 0)

    lista = {}
    for _, fila in df.iterrows():
        codigo = normalizar_codigo(fila[col["codigo"]])
        if not codigo:
            continue
        item = {}
        if col.get("precio"):
            precio = a_numero(fila[col["precio"]])
            if precio is not None and precio > 0:
                item["precio"] = round(precio * mult, dec)
        if col.get("stock"):
            crudo = fila[col["stock"]]
            stock = a_numero(crudo)
            if stock is None and str(crudo).strip().lower() in textos_stock:
                stock = textos_stock[str(crudo).strip().lower()]
            if stock is not None:
                stock = max(0, int(stock))
                if stock < stock_min_publicar:
                    stock = 0
                if stock_max is not None:
                    stock = min(stock, int(stock_max))
                item["stock"] = stock
        if item:
            lista[codigo] = item
    log(f"Lista del proveedor: {len(lista)} productos con datos válidos")
    return lista


# ----------------------------------------------------------------------------
# 2. BaseLinker
# ----------------------------------------------------------------------------

class BaseLinker:
    def __init__(self, token):
        self.token = token
        self.ultima = 0.0

    def llamar(self, metodo, params, reintentos=3):
        for intento in range(1, reintentos + 1):
            espera = PAUSA_API - (time.time() - self.ultima)
            if espera > 0:
                time.sleep(espera)
            self.ultima = time.time()
            try:
                r = requests.post(BL_URL, headers={"X-BLToken": self.token},
                                  data={"method": metodo, "parameters": json.dumps(params)},
                                  timeout=120)
                r.raise_for_status()
                datos = r.json()
            except (requests.RequestException, ValueError) as e:
                log(f"{metodo}: error de conexión ({e}), intento {intento}/{reintentos}")
                time.sleep(10 * intento)
                continue
            if datos.get("status") == "SUCCESS":
                return datos
            msg = f"{datos.get('error_code')}: {datos.get('error_message')}"
            if "limit" in msg.lower() and intento < reintentos:
                log(f"{metodo}: límite de API alcanzado, esperando 60 s")
                time.sleep(60)
                continue
            raise RuntimeError(f"BaseLinker {metodo} -> {msg}")
        raise RuntimeError(f"BaseLinker {metodo}: sin respuesta tras {reintentos} intentos")

    def catalogo(self, inventory_id):
        """Devuelve {product_id: {sku, ean, stock{}, prices{}}} de todo el catálogo."""
        productos, pagina = {}, 1
        while True:
            d = self.llamar("getInventoryProductsList",
                            {"inventory_id": inventory_id, "page": pagina})
            lote = d.get("products") or {}
            productos.update(lote)
            if len(lote) < 1000:
                break
            pagina += 1
        log(f"Catálogo BaseLinker: {len(productos)} productos")
        return productos


# ----------------------------------------------------------------------------
# 3. Comparar y actualizar
# ----------------------------------------------------------------------------

def sincronizar(cfg, lista, bl, dry_run):
    b = cfg["baselinker"]
    inv = int(b["inventory_id"])
    grupo = str(b["price_group_id"])
    deposito = str(b["warehouse_id"])
    campo = cfg["columnas"].get("vincular_por", "sku").lower()

    catalogo = bl.catalogo(inv)
    indice = {}
    for pid, prod in catalogo.items():
        clave = normalizar_codigo(prod.get(campo))
        if clave:
            indice[clave] = (pid, prod)

    cambios_stock, cambios_precio, reporte, no_encontrados = {}, {}, [], []
    for codigo, item in lista.items():
        if codigo not in indice:
            no_encontrados.append(codigo)
            continue
        pid, prod = indice[codigo]
        stock_actual = (prod.get("stock") or {}).get(deposito)
        precio_actual = (prod.get("prices") or {}).get(grupo)

        if "stock" in item and (stock_actual is None or int(stock_actual) != item["stock"]):
            cambios_stock[pid] = {deposito: item["stock"]}
            reporte.append([codigo, pid, "stock", stock_actual, item["stock"]])
        if "precio" in item and (precio_actual is None
                                 or abs(float(precio_actual) - item["precio"]) >= 0.01):
            cambios_precio[pid] = {grupo: item["precio"]}
            reporte.append([codigo, pid, "precio", precio_actual, item["precio"]])

    log(f"Cambios detectados: {len(cambios_stock)} de stock, {len(cambios_precio)} de precio. "
        f"Códigos del proveedor sin vincular en BaseLinker: {len(no_encontrados)}")

    advertencias = []
    if not dry_run:
        for metodo, cambios in (("updateInventoryProductsStock", cambios_stock),
                                ("updateInventoryProductsPrices", cambios_precio)):
            ids = list(cambios)
            for i in range(0, len(ids), LOTE):
                parte = {pid: cambios[pid] for pid in ids[i:i + LOTE]}
                d = bl.llamar(metodo, {"inventory_id": inv, "products": parte})
                log(f"{metodo}: {d.get('counter', 0)} actualizados")
                for pid, err in (d.get("warnings") or {}).items():
                    advertencias.append([pid, metodo, err])
    else:
        log("DRY_RUN activo: no se escribió nada en BaseLinker")

    guardar_reporte(reporte, no_encontrados, advertencias, dry_run)
    return len(reporte), len(no_encontrados), len(advertencias)


def guardar_reporte(reporte, no_encontrados, advertencias, dry_run):
    carpeta = BASE / "reportes"
    carpeta.mkdir(exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d-%H%M")
    with open(carpeta / f"cambios-{sello}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["codigo", "product_id", "campo", "antes", "ahora",
                    "(simulado)" if dry_run else ""])
        w.writerows(reporte)
    with open(carpeta / f"sin-vincular-{sello}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["codigo_proveedor"])
        w.writerows([[c] for c in sorted(no_encontrados)])
    if advertencias:
        with open(carpeta / f"advertencias-{sello}.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["product_id", "metodo", "mensaje"])
            w.writerows(advertencias)


# ----------------------------------------------------------------------------

def main():
    cfg = yaml.safe_load((BASE / "config.yaml").read_text(encoding="utf-8"))
    dry_run = os.environ.get("DRY_RUN", "0") == "1" or cfg.get("dry_run", False)
    token = os.environ.get("BL_TOKEN")
    if not token:
        sys.exit("Falta el secret BL_TOKEN (token de la API de BaseLinker).")

    lista = leer_lista(descargar_lista(cfg), cfg)
    if len(lista) < int(cfg.get("reglas", {}).get("minimo_productos_lista", 1)):
        sys.exit("La lista del proveedor vino vacía o incompleta: no se actualiza nada "
                 "para no poner stock en cero por error.")

    cambios, sin_vincular, adv = sincronizar(cfg, lista, BaseLinker(token), dry_run)
    log(f"Listo. {cambios} cambios, {sin_vincular} sin vincular, {adv} advertencias.")
    if adv:
        sys.exit(1)   # marca la ejecución en rojo para que llegue el aviso por mail


if __name__ == "__main__":
    main()
