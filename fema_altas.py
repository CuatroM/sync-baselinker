"""
Solo lectura: arma el Excel para dar de alta en BaseLinker los productos de la lista de FEMA que todavía
no están en el inventario (los "sin vincular" de la sincronización). No escribe nada en BaseLinker:
el archivo lo importa el usuario. Precio = PRECIO MAYORISTA × multiplicador (igual que la sincronización).
Salida: salida/FEMA_altas_BaseLinker.xlsx
"""
import io
import os

import pandas as pd
import yaml

import sync

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
b = cfg["baselinker"]
contenido = sync.descargar_lista(cfg)
lista = sync.leer_lista(contenido, cfg)
df = pd.read_excel(io.BytesIO(contenido), dtype=object)
df.columns = [str(c).strip().upper() for c in df.columns]
col_desc = next((c for c in df.columns if c.startswith("DESCRIP")), None)
sync.log(f"Columnas: {list(df.columns)} | con descripción: {df[col_desc].notna().sum() if col_desc else 0} de {len(df)}")
sync.log("Ejemplo: " + str(df.head(3).to_dict("records")))
bl = sync.BaseLinker(os.environ["BL_TOKEN"])
cat = bl.catalogo(int(b["inventory_id"]))
suf = (b.get("sufijo_sku") or "").upper()
en_bl = {sync.normalizar_codigo(str(p.get("sku") or "").upper().replace(suf, "")) for p in cat.values()}
filas = []
for _, r in df.iterrows():
    cod = sync.normalizar_codigo(r.get("SKU"))
    if not cod or cod in en_bl or cod not in lista:
        continue
    it = lista[cod]
    filas.append({"SKU": f"{cod}{b.get('sufijo_sku') or ''}", "NOMBRE": str(r.get(col_desc) or "").strip() if col_desc else "",
                  "PRECIO": it.get("precio"), "STOCK": it.get("stock", 0), "IVA": r.get("IMPUESTOS"),
                  "UNIDAD DE VENTA": r.get("UNIDAD DE VENTA")})
os.makedirs("salida", exist_ok=True)
out = pd.DataFrame(filas)
out.to_excel("salida/FEMA_altas_BaseLinker.xlsx", index=False)
sync.log(f"Para dar de alta: {len(out)} | con stock: {(out['STOCK'] > 0).sum() if len(out) else 0} | "
         f"sin precio: {out['PRECIO'].isna().sum() if len(out) else 0}")
