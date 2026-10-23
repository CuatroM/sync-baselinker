"""Quita el logo del proveedor (Extra Power / Versa / Black Jack) de la esquina inferior izquierda de una foto.
Busca las manchas que están enteras dentro de esa esquina y separadas del producto, y las pinta de blanco.
Si la mancha toca el producto o sale de la esquina, no la toca."""
import io

import numpy as np
from PIL import Image
from scipy import ndimage


def quitar_logo(datos, zona_x=0.36, zona_y=0.62):
    im = Image.open(io.BytesIO(datos)).convert("RGB")
    a = np.asarray(im).astype(np.int16)
    h, w = a.shape[:2]
    fondo = (a.min(axis=2) > 235) & (np.ptp(a, axis=2) < 20)      # blanco o casi blanco
    objeto = ~fondo
    objeto = ndimage.binary_dilation(objeto, iterations=3)           # une las letras del logo
    lab, n = ndimage.label(objeto)
    if not n:
        return datos, False
    borrar = np.zeros((h, w), bool)
    cambios = False
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        y0, y1, x0, x1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
        if x1 <= w * zona_x and y0 >= h * zona_y and y1 <= h * 0.97 and (x1 - x0) > w * 0.08:
            borrar |= lab == i
            cambios = True
    if not cambios:
        return datos, False
    b = np.asarray(im).copy()
    b[ndimage.binary_dilation(borrar, iterations=2)] = 255
    out = io.BytesIO()
    Image.fromarray(b).save(out, "JPEG", quality=92)
    return out.getvalue(), True
