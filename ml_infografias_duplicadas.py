"""
Saca las imágenes informativas que quedaron repetidas en una publicación (pasó cuando ML respondió 409
pero igual había aplicado el cambio, y el reintento las volvió a agregar).
- Para cada publicación de ITEMS (o todas las del registro si ITEMS=todas): baja sus fotos y las compara con las
  2 imágenes informativas de ese producto. Deja la primera aparición de cada una (fotos 2 y 3) y saca las repetidas.
- Nunca saca la portada ni fotos que no se parezcan a las informativas.
DRY_RUN=1 (por defecto): solo informa.
"""
import io
import json
import os
import re

import numpy as np
from PIL import Image

import ml_portada_util as pu
from ml.cliente import ML, log

dry = (os.environ.get("DRY_RUN") or "1").strip() != "0"
ITEMS = (os.environ.get("ITEMS") or "").strip()
registro = json.load(open("datos/infografias_subidas.json", encoding="utf-8"))
ids = list(registro) if ITEMS.lower() == "todas" else ["MLA" + re.sub(r"\D", "", x) for x in re.split(r"[\s,;]+", ITEMS) if re.sub(r"\D", "", x)]
ml = ML(os.environ.get("ML_CODIGO"))
log(f"{len(ids)} publicaciones | {'SIMULACIÓN' if dry else 'REAL'}")


def huella(datos):
    im = Image.open(io.BytesIO(datos)).convert("L").resize((96, 96), Image.LANCZOS)
    return np.asarray(im, dtype=np.float32)


def parecida(a, b):
    return float(np.abs(a - b).mean()) < 3


res = []
for iid in ids:
    try:
        reg = registro.get(iid)
        if not reg:
            log(f"{iid}: no está en el registro, no se toca")
            continue
        refs = [huella(open(os.path.join("infografias", r), "rb").read()) for r in reg["archivos"]]
        it = ml.get(f"/items/{iid}", attributes="id,pictures,variations")
        pics = it.get("pictures") or []
        vistos, quitar = [False] * len(refs), []
        for k, p in enumerate(pics):
            if k == 0:
                continue
            h = huella(pu.bajar(p))
            for j, r in enumerate(refs):
                if parecida(h, r):
                    if vistos[j]:
                        quitar.append(p["id"])
                    vistos[j] = True
                    break
        if not quitar:
            log(f"{iid}: {len(pics)} fotos, sin repetidas")
            res.append("sin repetidas")
            continue
        quedan = [p["id"] for p in pics if p["id"] not in quitar]
        if dry:
            log(f"{iid}: {len(pics)} fotos -> se sacarían {len(quitar)} repetidas, quedan {len(quedan)}")
            res.append("se sacarían")
            continue
        cuerpo = {"pictures": [{"id": x} for x in quedan]}
        if it.get("variations"):
            cuerpo["variations"] = [{"id": v["id"], "picture_ids": [x for x in v.get("picture_ids") or [] if x in quedan] or quedan[:1]}
                                    for v in it["variations"]]
        r = ml.put(f"/items/{iid}", cuerpo)
        ok = r.status_code in (200, 201)
        log(f"{iid}: {len(pics)} fotos -> " + (f"saqué {len(quitar)} repetidas, quedan {len(quedan)}" if ok else f"ERROR {r.status_code} {r.text[:200]}"))
        res.append("arreglada" if ok else "error")
    except Exception as e:
        log(f"{iid}: ERROR {e}")
        res.append("error")
from collections import Counter
log("Resumen: " + str(Counter(res)))
