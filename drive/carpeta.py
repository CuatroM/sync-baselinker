"""
Acceso a carpetas públicas de Google Drive ("Cualquier persona con el enlace"), sin API key.
- listar(folder_id): archivos y subcarpetas (nombre, id, es_carpeta), recorriendo subcarpetas.
- descargar(file_id): bytes del archivo.
"""
import html
import re
import time

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def _id_de(link_o_id):
    m = re.search(r"/folders/([A-Za-z0-9_-]+)", link_o_id) or re.search(r"id=([A-Za-z0-9_-]+)", link_o_id)
    return m.group(1) if m else link_o_id.strip()


def listar(carpeta, ruta="", profundidad=3):
    fid = _id_de(carpeta)
    r = requests.get(f"https://drive.google.com/embeddedfolderview?id={fid}", headers=UA, timeout=60)
    r.raise_for_status()
    out = []
    for m in re.finditer(r'<div class="flip-entry" id="entry-([A-Za-z0-9_-]+)".*?'
                         r'<div class="flip-entry-title">(.*?)</div>', r.text, re.S):
        eid, nombre = m.group(1), html.unescape(m.group(2)).strip()
        bloque = r.text[m.start():m.end() + 400]
        es_carpeta = "/folders/" in bloque or "drive/folders" in bloque
        out.append({"id": eid, "nombre": nombre, "ruta": f"{ruta}{nombre}", "carpeta": es_carpeta})
        if es_carpeta and profundidad > 0:
            time.sleep(0.3)
            out += listar(eid, f"{ruta}{nombre}/", profundidad - 1)
    return out


def descargar(file_id):
    s = requests.Session()
    s.headers.update(UA)
    url = "https://drive.usercontent.google.com/download"
    r = s.get(url, params={"id": file_id, "export": "download"}, timeout=120)
    if "text/html" in r.headers.get("content-type", ""):      # aviso de archivo grande
        ok = re.search(r'name="confirm" value="([^"]+)"', r.text)
        uuid = re.search(r'name="uuid" value="([^"]+)"', r.text)
        if ok:
            r = s.get(url, params={"id": file_id, "export": "download", "confirm": ok.group(1),
                                   "uuid": uuid.group(1) if uuid else ""}, timeout=300)
    r.raise_for_status()
    if "text/html" in r.headers.get("content-type", ""):
        raise RuntimeError(f"Drive no entregó el archivo {file_id} (¿carpeta no pública?)")
    return r.content
