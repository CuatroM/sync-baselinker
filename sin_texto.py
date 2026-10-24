"""
Portada sin textos: en fotos de producto sobre fondo blanco, borra lo que no es el producto
(marca escrita arriba, medidas, "Incluye packaging", etiquetas con flechas, miniaturas sueltas)
sin mover ni recortar el producto. ML no acepta textos ni logos en la foto principal.

Cómo decide: separa la foto en manchas (lo que no es fondo blanco). Se queda con la mancha más
grande y con las que tengan un tamaño parecido (otras piezas del mismo producto). Las manchas bajas
y anchas (renglones de texto) y las chicas (miniaturas, íconos, letras sueltas) se borran.
Si la foto no tiene fondo blanco (foto ambientada) no se toca.
"""
import io
import os
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import Image
from scipy import ndimage


def _idioma():
    try:
        langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=20).stdout
        return "spa+eng" if "spa" in langs.split() else "eng"
    except Exception:
        return "eng"


IDIOMA = _idioma() if shutil.which("tesseract") else "eng"


def palabras(im):
    """Cajas de palabras que encuentra el OCR (tesseract). Lista vacía si no está instalado."""
    if not shutil.which("tesseract"):
        return []
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.png")
        im.save(p)
        try:
            out = subprocess.run(["tesseract", p, "-", "--psm", "11", "-l", IDIOMA, "tsv"], capture_output=True, text=True,
                                 timeout=60).stdout
        except Exception:
            return []
    cajas = []
    for linea in out.splitlines()[1:]:
        c = linea.split("\t")
        if len(c) < 12 or not c[11].strip():
            continue
        try:
            conf = float(c[10])
        except ValueError:
            continue
        txt = c[11].strip()
        if conf >= 55 and sum(ch.isalnum() for ch in txt) >= 2:
            cajas.append(tuple(int(c[i]) for i in (6, 7, 8, 9)))
    return cajas


def borrar_palabras(im, fondo):
    """Borra las palabras escritas sobre el fondo blanco (no las impresas sobre el producto)."""
    a = np.asarray(im).copy()
    H, W = fondo.shape
    hechas = []
    for x, y, w, h in palabras(im):
        x0, y0, x1, y1 = max(x - 3, 0), max(y - 3, 0), min(x + w + 3, W), min(y + h + 3, H)
        zona = fondo[y0:y1, x0:x1]
        if zona.size == 0:
            continue
        # el contorno de la palabra tiene que ser fondo blanco (si no, está impresa sobre el producto)
        anillo = np.concatenate([zona[0], zona[-1], zona[:, 0], zona[:, -1]])
        # palabra de verdad: renglón bajo y con mucho blanco entre letras (el OCR a veces "lee" un producto)
        if h > 0.08 * H or w > 0.6 * W:
            continue
        if anillo.mean() >= 0.9 and zona.mean() >= 0.5:
            a[y0:y1, x0:x1] = 255                         # toda la caja (sin dejar sombra de las letras)
            fondo[y0:y1, x0:x1] = True
            hechas.append((x0, y0, x1, y1))
    return Image.fromarray(a), hechas


def limpiar(datos):
    """Devuelve (bytes JPEG, cantidad de elementos borrados). 0 = sin cambios."""
    im = Image.open(io.BytesIO(datos)).convert("RGB")
    a = np.asarray(im).astype(np.int16)
    h, w = a.shape[:2]
    fondo = (a.min(axis=2) > 232) & (np.ptp(a, axis=2) < 22)
    m = max(3, round(min(h, w) * 0.03))
    borde = np.concatenate([fondo[:m].ravel(), fondo[-m:].ravel(), fondo[:, :m].ravel(), fondo[:, -m:].ravel()])
    if fondo.mean() < 0.35 or borde.mean() < 0.85:   # foto ambientada o fondo de color: no se toca
        return datos, 0
    # infografía (barra oscura de título arriba, como las fichas VERSA): no se toca
    banda = ~fondo[: max(1, round(h * 0.14))]
    if (banda.mean(axis=1) > 0.6).mean() > 0.3:
        return datos, 0
    im, hechas = borrar_palabras(im, fondo)
    n_pal = len(hechas)
    a = np.asarray(im).astype(np.int16)
    # unir letras de una misma palabra / piezas cercanas
    obj = ndimage.binary_dilation(~fondo, iterations=max(2, round(min(h, w) / 160)))
    lab, n = ndimage.label(obj)
    if n <= 1 and not n_pal:
        return datos, 0
    cajas = ndimage.find_objects(lab)
    areas = ndimage.sum(~fondo, lab, range(1, n + 1))      # píxeles reales de cada mancha
    info = []
    for i, sl in enumerate(cajas):
        y0, y1, x0, x1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
        info.append({"i": i + 1, "ch": y1 - y0, "cw": x1 - x0, "area": areas[i], "caja": (x0, y0, x1, y1)})
    if not info:
        return datos, 0
    mayor = max(info, key=lambda c: c["area"])
    caja_mayor = mayor["ch"] * mayor["cw"]
    quedan, borrar = [], []
    cerca = max(12, round(min(h, w) * 0.03))   # la leyenda está pegada a su miniatura

    def junto_a_texto(c):
        x0, y0, x1, y1 = c["caja"]
        return any(x0 - cerca < px1 and px0 < x1 + cerca and y0 - cerca < py1 and py0 < y1 + cerca
                   for px0, py0, px1, py1 in hechas)

    for c in info:
        relleno = c["area"] / max(1, c["ch"] * c["cw"])                    # las letras dejan mucho blanco
        texto = c["ch"] < 0.10 * h and c["cw"] > 1.8 * c["ch"] and relleno < 0.5   # renglón: bajo, ancho y calado
        chico = c["area"] < 0.12 * mayor["area"] and c["ch"] * c["cw"] < 0.25 * caja_mayor
        mancha = c["area"] < 0.004 * mayor["area"] or (c["ch"] < 0.035 * h and c["cw"] < 0.035 * w)  # restos, números sueltos
        x0, y0, x1, y1 = c["caja"]
        en_borde = (y1 < 0.22 * h or y0 > 0.82 * h) and c["cw"] < 0.5 * w              # logo de marca arriba / medidas abajo
        # solo se borra lo que acompaña a un texto (logo de marca, miniatura con leyenda) o restos mínimos
        # solo se borran restos de texto (logos escritos, leyendas); las miniaturas y piezas del producto quedan
        resto = relleno < 0.5 and c["area"] < 0.05 * mayor["area"] and c["ch"] < 0.10 * h   # "Love" en cursiva, "+", ícono de marca
        if c is mayor or not ((texto and (en_borde or junto_a_texto(c))) or (resto and junto_a_texto(c))):
            quedan.append(c)
        else:
            borrar.append(c)
    if not borrar and not n_pal:
        return datos, 0
    # se parte de la foto original (con las palabras ya borradas) y solo se pinta de blanco lo que se saca:
    # así los productos blancos o transparentes no pierden partes
    sacar = np.isin(lab, [c["i"] for c in borrar])     # la mancha entera (incluye el borde suave de las letras)
    b = np.asarray(im).copy()
    b[sacar] = 255
    out = io.BytesIO()
    Image.fromarray(b).save(out, "JPEG", quality=92)
    return out.getvalue(), len(borrar) + n_pal
