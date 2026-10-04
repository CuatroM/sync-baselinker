"""Descarga la lista del proveedor con la config actual y muestra su estructura."""
import io
import yaml
import pandas as pd
import sync

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
contenido = sync.descargar_lista(cfg)
print("Primeros bytes:", contenido[:20])
if contenido[:2] in (b"PK", b"\xd0\xcf"):
    xl = pd.ExcelFile(io.BytesIO(contenido))
    print("Hojas:", xl.sheet_names)
    for h in xl.sheet_names[:3]:
        df = pd.read_excel(xl, sheet_name=h, header=None, nrows=15, dtype=object)
        print(f"\n=== Hoja {h!r}: primeras 15 filas (sin encabezado asumido) ===")
        print(df.to_string(max_colwidth=40))
        total = pd.read_excel(xl, sheet_name=h, header=None, dtype=object)
        print(f"Total filas: {len(total)} | columnas: {total.shape[1]}")
else:
    texto = contenido.decode("utf-8", errors="replace")
    lineas = texto.splitlines()
    print(f"Texto/CSV: {len(lineas)} líneas")
    print("\n".join(lineas[:15]))
