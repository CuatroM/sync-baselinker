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

# ---------------- control: SI con utilidad negativa y NO con utilidad positiva (según tu columna AW)
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

iu = next((i for i, t in enumerate(enc) if str(t).upper().startswith("UTILIDAD NETA")), None)
ig = next((i for i, t in enumerate(enc) if str(t).strip().upper() == "GANANCIA"), None)
if iu is not None:
    def g(r, i):
        return r[i] if len(r) > i else ""

    def num(x):
        return x if isinstance(x, (int, float)) else None

    cols_out = [0, 1, 3, 4, 5, 8, 9, 13, 17, 18, 19, 26, iu, iu + 2, ig]
    wb = Workbook()
    resumen = {}
    for nombre, cond in (("SI con pérdida", lambda d, u: d == "SI" and u is not None and u < 0),
                         ("NO con ganancia", lambda d, u: d == "NO" and u is not None and u > 0),
                         ("Sin costo", None)):
        ws = wb.active if nombre == "SI con pérdida" else wb.create_sheet(nombre)
        ws.title = nombre
        ws.append(["FILA"] + [enc[i] if i is not None and i < len(enc) else "" for i in cols_out])
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="02561A")
        n = 0
        por_promo = {}
        for k, r in enumerate(val[1:], 2):
            d, u = str(g(r, idd)).strip().upper(), num(g(r, iu))
            if (cond(d, u) if cond else g(r, 9) in ("", None)):
                n += 1
                por_promo[g(r, 1)] = por_promo.get(g(r, 1), 0) + 1
                ws.append([k] + [g(r, i) if i is not None else "" for i in cols_out])
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        resumen[nombre] = {"total": n, "por_promocion": por_promo}
    wb.save("promos_control.xlsx")
    tot = {}
    for r in val[1:]:
        d, u = str(g(r, idd)).strip().upper(), num(g(r, iu))
        k = (d or "(vacío)", "positiva" if u and u > 0 else "negativa" if u is not None and u < 0 else "cero/sin dato")
        tot[" / ".join(k)] = tot.get(" / ".join(k), 0) + 1
    sc = {}
    for r in val[1:]:
        if g(r, 9) in ("", None):
            k = str(g(r, 4)).split("#")[-1].upper() if "#" in str(g(r, 4)) else "SIN SUFIJO"
            sc[k] = sc.get(k, 0) + 1
    print("CONTROL", json.dumps({"decision_vs_utilidad": tot, **resumen, "sin_costo_por_proveedor": sc}, ensure_ascii=False))
