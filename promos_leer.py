"""Solo lectura: vuelca a la rama diagnostico cómo está hoy «Promos - Decidir» (encabezados, fórmulas y valores),
para revisar las fórmulas que cargaste a mano y contar las decisiones. No escribe nada en la planilla."""
import json
import yaml

from promos_sheet import Hoja, T_DEC

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
h = Hoja(cfg["planilla_ml"]["sheet_id"])


def leer(modo):
    r = h.gs.get(f"{h.url}/values/'{T_DEC}'", params={"valueRenderOption": modo})
    r.raise_for_status()
    return r.json().get("values") or []


form, val = leer("FORMULA"), leer("UNFORMATTED_VALUE")
loc = h.gs.get(h.url, params={"fields": "properties(locale,timeZone)"}).json()
enc = val[0]
idd = enc.index("DECISIÓN (SI/NO)")
dec = {}
for r in val[1:]:
    d = str(r[idd] if len(r) > idd else "").strip().upper()
    dec[d or "(vacío)"] = dec.get(d or "(vacío)", 0) + 1
# filas de muestra: las primeras 3 con SI, 3 con NO y 3 sin marcar
mu = []
for quiero in ("SI", "NO", ""):
    mu += [i for i, r in enumerate(val[1:], 1) if str(r[idd] if len(r) > idd else "").strip().upper() == quiero][:3]
out = {"locale": loc, "filas": len(val) - 1, "decisiones": dec, "encabezado": enc,
       "muestras": [{"fila": i + 1, "formulas": form[i] if i < len(form) else [], "valores": val[i]} for i in mu]}
# ¿en qué columnas hay fórmulas y cuántas filas las tienen?
cols = {}
for r in form[1:]:
    for j, c in enumerate(r):
        if isinstance(c, str) and c.startswith("="):
            cols[j] = cols.get(j, 0) + 1
out["columnas_con_formula"] = {str(k): v for k, v in sorted(cols.items())}
# fórmulas sueltas: en columnas donde casi ninguna fila tiene fórmula
out["formulas_sueltas"] = [{"fila": n, "columna": j, "mla": r[3] if len(r) > 3 else "", "formula": c}
                           for n, r in enumerate(form[1:], 2) for j, c in enumerate(r)
                           if isinstance(c, str) and c.startswith("=") and cols.get(j, 0) < (len(form) - 1) / 2]
json.dump(out, open("promos_planilla.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps({k: out[k] for k in ("locale", "filas", "decisiones", "columnas_con_formula", "formulas_sueltas")},
                 ensure_ascii=False))
