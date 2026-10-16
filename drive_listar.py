"""Lista una carpeta pública de Drive (con subcarpetas) y guarda salida/drive_listado.csv. Solo lectura."""
import csv
import os

from drive.carpeta import listar

os.makedirs("salida", exist_ok=True)
items = listar(os.environ["CARPETA"], profundidad=int(os.environ.get("PROFUNDIDAD") or 3))
with open("salida/drive_listado.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["id", "ruta", "nombre", "carpeta"])
    for x in items:
        w.writerow([x["id"], x["ruta"], x["nombre"], "1" if x["carpeta"] else ""])
print(f"{len(items)} elementos ({sum(1 for x in items if not x['carpeta'])} archivos, "
      f"{sum(1 for x in items if x['carpeta'])} carpetas)")
for x in items[:40]:
    print(("[carpeta] " if x["carpeta"] else "") + x["ruta"])
