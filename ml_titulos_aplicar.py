"""
Cambia el título de las publicaciones según LISTA (por defecto datos/titulos_aprobados.json):
[{item, sku, titulo, titulo_anterior}]. Antes de cambiar comprueba que el SKU coincida y que el título
actual sea el anterior esperado (si alguien lo cambió a mano, no se pisa). Guarda el título anterior.
DRY_RUN=1 solo informa.
"""
import csv
import json
import os
import re
import time
from collections import Counter

from ml.cliente import ML, log, sku_de

dry = (os.environ.get("DRY_RUN") or "0").strip() != "0"
lista = json.load(open(os.environ.get("LISTA") or "datos/titulos_aprobados.json", encoding="utf-8"))
desde, limite = int(os.environ.get("DESDE") or 0), int(os.environ.get("LIMITE") or 0)
lista = lista[desde:desde + limite] if limite else lista[desde:]
ml = ML(os.environ.get("ML_CODIGO"))
log(f"{len(lista)} títulos | {'SIMULACIÓN' if dry else 'REAL'}")
filas = []
for n, x in enumerate(lista, 1):
    f = {"sku": x["sku"], "publicacion": x["item"], "titulo_anterior": "", "titulo_nuevo": x["titulo"],
         "titulo_que_armo_ml": "", "agregado_ml": "", "resultado": ""}
    try:
        it = ml.get(f"/items/{x['item']}", attributes="id,title,status,sold_quantity,attributes,seller_custom_field")
        f["titulo_anterior"] = it.get("title")
        if (sku_de(it) or "").upper() != x["sku"].upper():
            raise RuntimeError(f"el SKU en ML es {sku_de(it)!r}")
        if it.get("title") == x["titulo"]:
            f["resultado"] = "ya tenía ese título"
        elif x.get("titulo_anterior") and it.get("title") != x["titulo_anterior"] \
                and not (it.get("title") or "").lower().startswith(x["titulo"].lower()):
            f["resultado"] = "NO SE TOCA: el título cambió desde la exportación"
        elif dry:
            f["resultado"] = "SE CAMBIARÍA"
        else:
            r = ml.put(f"/items/{x['item']}", {"title": x["titulo"]})
            if r.status_code == 400 and "family_name" in r.text:
                # publicaciones "producto de usuario": ML arma el título = family_name + datos de la variante
                # (color, talle, diseño). Se calcula ese agregado y se saca del nombre para que no se repita.
                fam_actual = (ml.get(f"/items/{x['item']}", attributes="family_name").get("family_name") or "").strip()
                tit_actual = (it.get("title") or "").strip()
                agregado = tit_actual[len(fam_actual):].strip() if fam_actual and \
                    tit_actual.lower().startswith(fam_actual.lower()) else ""
                fam = x["titulo"]
                for pedazo in sorted(agregado.split(), key=len, reverse=True):
                    fam = re.sub(rf"(?i)(^|\s){re.escape(pedazo)}(?=\s|$)", " ", fam, count=1)
                fam = re.sub(r"\s+", " ", fam).strip()
                # sin conectores colgando al final ("Talle", "Color", "Con"...)
                while fam.split() and fam.split()[-1].lower() in ("con", "color", "talle", "de", "para", "y", "x", "en"):
                    fam = fam.rsplit(" ", 1)[0]
                f["agregado_ml"] = agregado
                r = ml.put(f"/items/{x['item']}/family_name", {"family_name": fam})
                if r.status_code in (200, 201):
                    time.sleep(1)
                    f["titulo_que_armo_ml"] = ml.get(f"/items/{x['item']}", attributes="title").get("title")
            if r.status_code in (200, 201):
                f["resultado"] = "TÍTULO CAMBIADO"
            else:
                f["resultado"] = f"ERROR {r.status_code} {r.text[:250]}"
            time.sleep(0.2)
    except Exception as e:
        f["resultado"] = f"ERROR: {e}"[:300]
    if n % 100 == 0 or n <= 5 or f["resultado"].startswith(("ERROR", "NO SE")):
        log(f"{n}/{len(lista)} {x['sku']}: {f['resultado']} {f['titulo_que_armo_ml'] or ''}")
    filas.append(f)
os.makedirs("salida", exist_ok=True)
with open("salida/titulos_aplicados.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(filas)
log("Resumen: " + str(Counter(r["resultado"].split(":")[0].split(" {")[0] for r in filas)))
