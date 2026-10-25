"""Funciones de portada compartidas: medir el producto, agrandarlo (con super-resolución) y bajar la foto."""
import io
import os

import numpy as np
import requests
from PIL import Image

from ml.cliente import log

web = requests.Session()


def contenido(im):
    a = np.asarray(im.convert("RGB")).astype(np.int16)
    nb = np.argwhere(~((a.min(axis=2) > 235) & (np.ptp(a, axis=2) < 20)))
    if not len(nb):
        return None
    (y0, x0), (y1, x1) = nb.min(0), nb.max(0) + 1
    return x0, y0, x1, y1


SR = {}


def superres(rec):
    """Amplía con una red neuronal (OpenCV dnn_superres). Si no está disponible, devuelve la misma imagen."""
    if SR.get("falla"):
        return rec
    try:
        import cv2
        lado = max(rec.size)
        esc = 4 if lado < 400 else 2 if lado < 900 else 0
        if not esc:
            return rec
        if esc not in SR:
            modelo = f"/tmp/EDSR_x{esc}.pb" if lado < 250 else f"/tmp/FSRCNN_x{esc}.pb"
            if not os.path.exists(modelo):
                url = ("https://raw.githubusercontent.com/Saafke/EDSR_Tensorflow/master/models/" if "EDSR" in modelo
                       else "https://raw.githubusercontent.com/Saafke/FSRCNN_Tensorflow/master/models/") + os.path.basename(modelo)
                open(modelo, "wb").write(web.get(url, timeout=120).content)
            sr = cv2.dnn_superres.DnnSuperResImpl_create()
            sr.readModel(modelo)
            sr.setModel("edsr" if "EDSR" in modelo else "fsrcnn", esc)
            SR[esc] = sr
        a = cv2.cvtColor(np.asarray(rec), cv2.COLOR_RGB2BGR)
        b = SR[esc].upsample(a)
        return Image.fromarray(cv2.cvtColor(b, cv2.COLOR_BGR2RGB))
    except Exception as e:
        log(f"super-resolución no disponible ({e}); uso ampliación común")
        SR["falla"] = True
        return rec


def agrandar(im, caja, lado=640, lienzo_px=800):
    rec = im.convert("RGB").crop(caja)
    if max(rec.size) < lado:
        rec = superres(rec)
    f = lado / max(rec.size)
    rec = rec.resize((max(1, round(rec.width * f)), max(1, round(rec.height * f))), Image.LANCZOS)
    lienzo = Image.new("RGB", (lienzo_px, lienzo_px), "white")
    lienzo.paste(rec, ((lienzo_px - rec.width) // 2, (lienzo_px - rec.height) // 2))
    out = io.BytesIO()
    lienzo.save(out, "JPEG", quality=92)
    return out.getvalue()


def bajar(p):
    """Bytes de la foto (prueba la versión grande -F y luego la normal)."""
    for u in ((p.get("secure_url") or "").replace("-O.", "-F."), p.get("secure_url"), p.get("url"),
              f"https://http2.mlstatic.com/D_{p['id']}-F.jpg", f"https://http2.mlstatic.com/D_{p['id']}-O.jpg"):
        if not u:
            continue
        r = web.get(u, timeout=60)
        if r.status_code == 200:
            try:
                Image.open(io.BytesIO(r.content)).verify()
                return r.content
            except Exception:
                pass
    raise RuntimeError("no pude bajar la portada")
