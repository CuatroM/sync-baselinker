"""
Quita el logo del proveedor (POWER / Extra Power, BLACK JACK, VERSA) que la web de EXTRAPOWER estampa
abajo a la izquierda de las fotos. Busca cada logo por comparación de forma (plantillas en datos/logos/),
a varias escalas, solo en la zona inferior izquierda; lo borra y rellena el hueco con lo que lo rodea
(blanco si está sobre el fondo, o la continuación del producto si lo toca).
"""
import io
import os

import cv2
import numpy as np
from PIL import Image

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos", "logos")
UMBRAL = float(os.environ.get("UMBRAL_LOGO") or 0.72)
_TPL = []
for _n in sorted(os.listdir(_DIR)) if os.path.isdir(_DIR) else []:
    if _n.endswith(".png"):
        _t = cv2.cvtColor(np.asarray(Image.open(os.path.join(_DIR, _n)).convert("RGB")), cv2.COLOR_RGB2BGR)
        _TPL.append((_n[:-4], cv2.cvtColor(_t, cv2.COLOR_BGR2GRAY), _t))
ESCALAS = [round(s, 2) for s in np.arange(0.55, 1.36, 0.05)]


def _buscar(img):
    """Devuelve la mejor coincidencia (puntaje, nombre, x, y, plantilla escalada) en la zona inferior izquierda."""
    h, w = img.shape[:2]
    y0z = int(h * 0.35)
    zona = cv2.cvtColor(img[y0z:, : max(int(w * 0.55), 260)], cv2.COLOR_BGR2GRAY)
    mejor = None
    for nombre, tg, t in _TPL:
        for s in ESCALAS:
            tgs = cv2.resize(tg, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            th, tw = tgs.shape[:2]
            if th >= zona.shape[0] or tw >= zona.shape[1]:
                continue
            r = cv2.matchTemplate(zona, tgs, cv2.TM_CCOEFF_NORMED)
            _, v, _, (x, y) = cv2.minMaxLoc(r)
            if mejor is None or v > mejor[0]:
                mejor = (v, nombre, x, y + y0z, cv2.resize(t, (tw, th), interpolation=cv2.INTER_AREA))
    return mejor


def quitar_logo(datos, umbral=None):
    """datos: bytes de la imagen. Devuelve (bytes JPEG, nombre del logo o "")."""
    umbral = UMBRAL if umbral is None else umbral
    im = Image.open(io.BytesIO(datos)).convert("RGB")
    img = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)
    quitados = []
    for _ in range(2):                       # por si hay más de un logo
        m = _buscar(img)
        if not m or m[0] < umbral:
            break
        v, nombre, x, y, ts = m
        th, tw = ts.shape[:2]
        # la marca del producto puede tener el mismo logo: el del proveedor está pegado al borde izquierdo
        if x > img.shape[1] * 0.12 + 40:
            break
        marca = (ts.min(axis=2) < 225).astype(np.uint8)
        marca = cv2.dilate(marca, np.ones((5, 5), np.uint8), iterations=2)
        mask = np.zeros(img.shape[:2], np.uint8)
        mask[y:y + th, x:x + tw] = marca
        img = cv2.inpaint(img, mask * 255, 6, cv2.INPAINT_TELEA)
        # lo que quedó casi blanco, blanco puro (sin manchas grises)
        reg = img[y:y + th, x:x + tw]
        casi = (reg.min(axis=2) > 225) & (marca > 0)
        reg[casi] = 255
        quitados.append(f"{nombre}:{v:.2f}")
    if not quitados:
        return datos, ""
    out = io.BytesIO()
    Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).save(out, "JPEG", quality=92)
    return out.getvalue(), ",".join(quitados)
