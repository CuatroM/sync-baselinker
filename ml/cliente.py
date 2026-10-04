"""
Conexión con la API de Mercado Libre — modo "a demanda".

Cada ejecución recibe el código TG-... de una autorización recién hecha (vale ~10 minutos
y un solo uso), lo canjea por un access token y trabaja con él. No se guarda ninguna llave.

Secrets usados: ML_CLIENT_ID, ML_CLIENT_SECRET
"""
import os
import time
from datetime import datetime

import requests

API = "https://api.mercadolibre.com"
REDIRECT_URI = "https://github.com/CuatroM/sync-baselinker"


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def access_token(codigo):
    codigo = (codigo or "").strip()
    if "code=" in codigo:                      # por si pegan la dirección completa
        codigo = codigo.split("code=", 1)[1].split("&", 1)[0]
    if not codigo.startswith("TG-"):
        raise SystemExit("Falta el código: pegá el texto que empieza con TG- (está en la "
                         "dirección del navegador después de autorizar, luego de 'code=').")
    for v in ("ML_CLIENT_ID", "ML_CLIENT_SECRET"):
        if not os.environ.get(v):
            raise SystemExit(f"Falta el secret {v} en GitHub.")
    r = requests.post(f"{API}/oauth/token", timeout=30,
                      headers={"accept": "application/json"},
                      data={"grant_type": "authorization_code",
                            "client_id": os.environ["ML_CLIENT_ID"],
                            "client_secret": os.environ["ML_CLIENT_SECRET"],
                            "code": codigo, "redirect_uri": REDIRECT_URI})
    if r.status_code != 200:
        raise SystemExit(f"Mercado Libre rechazó el código ({r.status_code}): {r.text}\n"
                         "El código vale ~10 minutos y un solo uso: autorizá de nuevo y "
                         "corré el proceso enseguida.")
    t = r.json()
    log(f"Conectado a Mercado Libre (usuario {t.get('user_id')})")
    return t["access_token"], t["user_id"]


class ML:
    def __init__(self, codigo):
        self.token, self.user_id = access_token(codigo)
        self.ses = requests.Session()
        self.ses.headers["Authorization"] = f"Bearer {self.token}"

    def pedir(self, metodo, ruta, reintentos=4, **kw):
        for intento in range(1, reintentos + 1):
            r = self.ses.request(metodo, f"{API}{ruta}", timeout=60, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(5 * intento)
                continue
            return r
        return r

    def put(self, ruta, cuerpo):
        return self.pedir("PUT", ruta, json=cuerpo)

    def get(self, ruta, **params):
        r = self.pedir("GET", ruta, params=params)
        r.raise_for_status()
        return r.json()

    def ids_publicaciones(self, **filtros):
        """Todos los IDs de publicaciones del vendedor (búsqueda con scroll)."""
        ids, scroll = [], None
        while True:
            params = {"search_type": "scan", "limit": 100, **filtros}
            if scroll:
                params["scroll_id"] = scroll
            d = self.get(f"/users/{self.user_id}/items/search", **params)
            lote = d.get("results") or []
            if not lote:
                break
            ids += lote
            scroll = d.get("scroll_id")
        return ids

    def publicaciones(self, ids, atributos="id,title,status,price,available_quantity,"
                                           "seller_custom_field,attributes,variations,shipping"):
        """Detalle de publicaciones, de a 20 por pedido."""
        for i in range(0, len(ids), 20):
            for x in self.get("/items", ids=",".join(ids[i:i + 20]), attributes=atributos):
                if x.get("code") == 200:
                    yield x["body"]


def sku_de(item):
    """SKU de una publicación: atributo SELLER_SKU o, si no, seller_custom_field."""
    for a in item.get("attributes") or []:
        if a.get("id") == "SELLER_SKU" and a.get("value_name"):
            return a["value_name"]
    return item.get("seller_custom_field") or ""
