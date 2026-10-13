"""Lista las carpetas de Drive de fotos y fichas y prueba descargar un archivo de cada una."""
import collections
import os
import re

from drive.carpeta import descargar, listar

SKUS = [s.strip() for s in os.environ.get("SKUS", "").split(",") if s.strip()]
for nombre, link in (("FOTOS", os.environ["DRIVE_FOTOS"]), ("FICHAS", os.environ["DRIVE_FICHAS"])):
    items = listar(link)
    archivos = [x for x in items if not x["carpeta"]]
    print(f"\n===== {nombre}: {len(archivos)} archivos, {len(items) - len(archivos)} carpetas")
    ext = collections.Counter(os.path.splitext(x["nombre"])[1].lower() for x in archivos)
    print("Extensiones:", dict(ext))
    for x in items[:40]:
        print(("  [carpeta] " if x["carpeta"] else "  ") + x["ruta"])
    if len(items) > 40:
        print(f"  ... y {len(items) - 40} más")
    for sku in SKUS:
        hay = [x["ruta"] for x in archivos if re.search(rf"(^|[^0-9A-Za-z]){re.escape(sku)}([^0-9A-Za-z]|$)", x["ruta"])]
        print(f"  SKU {sku}: {hay[:6]}")
    if archivos:
        b = descargar(archivos[0]["id"])
        print(f"Prueba de descarga '{archivos[0]['nombre']}': {len(b):,} bytes, inicio {b[:8]!r}")
