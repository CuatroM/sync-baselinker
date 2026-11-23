"""Adhiere a las promociones de Mercado Libre las publicaciones marcadas SI en «Promos - Decidir».

- DRY_RUN=1 (por defecto): no toca nada, solo dice qué haría.
- Precio en ofertas de precio tachado (DEAL: Black Friday, Navidad, Precio tachado, MK):
  el de PRECIO A PONER si lo completaste; si no, el PRECIO QUE PROPONE ML (columna N), que es sobre el que
  calculan tus fórmulas. Si queda fuera del rango mínimo–máximo de ML, no se adhiere y se avisa.
- Ofertas que arma ML (SMART: Potencia tus ventas, Ofertas Compartidas; PRICE_MATCHING): se aceptan tal cual.
- QUITAR_NO=1: además saca de la promoción las publicaciones marcadas NO que ya estén adheridas.
- SOLO: opcional, IDs de promoción separados por coma.
Antes de adherir vuelve a leer cada promoción en ML, así usa el estado y el rango de precios actuales.
"""
import csv
import json
import os
import time

import yaml

from ml.cliente import ML, log
from promos_sheet import Hoja, T_DEC

DRY = os.environ.get("DRY_RUN", "1").strip() != "0"
QUITAR_NO = os.environ.get("QUITAR_NO", "0").strip() == "1"
SOLO = {x.strip() for x in (os.environ.get("SOLO") or "").split(",") if x.strip()}
LIMITE = int(os.environ.get("LIMITE") or 0)

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
h = Hoja(cfg["planilla_ml"]["sheet_id"])
ml = ML(os.environ.get("ML_CODIGO"))


def num(x):
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).replace("$", "").replace(".", "").replace(",", ".")) if str(x).strip() else None
    except ValueError:
        return None


# ---------------- planilla
val = h.leer(f"'{T_DEC}'")
enc = val[0]
I = {k: enc.index(k) for k in ("ID PROMO", "PROMOCIÓN", "TIPO", "MLA", "SKU", "TÍTULO", "PRECIO QUE PROPONE ML",
                               "DECISIÓN (SI/NO)", "PRECIO A PONER", "ESTADO EN PROMO")}
pedidos = {}   # promo_id -> {mla: fila}
for n, r in enumerate(val[1:], 2):
    r = r + [""] * (len(enc) - len(r))
    d = str(r[I["DECISIÓN (SI/NO)"]]).strip().upper()
    if d not in ("SI", "NO") or (SOLO and r[I["ID PROMO"]] not in SOLO):
        continue
    if d == "NO" and not QUITAR_NO:
        continue
    pedidos.setdefault(r[I["ID PROMO"]], {})[r[I["MLA"]]] = {"fila": n, "decision": d, "r": r}
log(f"Planilla: {sum(len(v) for v in pedidos.values())} filas a procesar en {len(pedidos)} promociones "
    f"({'PRUEBA, no se cambia nada' if DRY else 'REAL'}{', quitando las NO adheridas' if QUITAR_NO else ''})")

# ---------------- estado actual en ML
promos = {p["id"]: p for p in (ml.pedir("GET", f"/seller-promotions/users/{ml.user_id}", reintentos=2,
                                        params={"app_version": "v2"}).json().get("results") or [])}


def items_de(pid, tipo):
    out, sa = {}, None
    while True:
        params = {"promotion_type": tipo, "app_version": "v2", "limit": 50}
        if sa:
            params["search_after"] = sa
        r = ml.pedir("GET", f"/seller-promotions/promotions/{pid}/items", reintentos=3, params=params)
        if r.status_code != 200:
            log(f"   no se pudieron leer los ítems de {pid} ({r.status_code})")
            break
        d = r.json()
        for i in d.get("results") or []:
            out[i["id"]] = i
        sa = (d.get("paging") or {}).get("searchAfter")
        if not d.get("results") or not sa:
            break
    return out


res, hechos = [], 0
for pid, filas in pedidos.items():
    p = promos.get(pid)
    if not p:
        for mla, f in filas.items():
            res.append([pid, f["r"][I["PROMOCIÓN"]], mla, f["r"][I["SKU"]], f["r"][I["TÍTULO"]], f["decision"], "",
                        "NO SE HIZO", "La promoción ya no figura en ML (terminó o venció el plazo)", f["fila"]])
        continue
    tipo = p["type"]
    actuales = items_de(pid, tipo)
    log(f"{pid} | {tipo} | {p.get('name')} | {len(filas)} marcadas | {len(actuales)} en ML")
    for mla, f in filas.items():
        if LIMITE and hechos >= LIMITE:
            break
        r, it = f["r"], actuales.get(mla)
        fila = [pid, p.get("name"), mla, r[I["SKU"]], r[I["TÍTULO"]], f["decision"]]
        if not it:
            res.append(fila + ["", "NO SE HIZO", "La publicación ya no es candidata en esta promoción", f["fila"]])
            continue
        est = it.get("status")
        if f["decision"] == "NO":
            if est not in ("started", "pending"):
                continue
            res_txt, precio = "SE QUITA", ""
            if not DRY:
                prm = {"promotion_type": tipo, "promotion_id": pid, "app_version": "v2"}
                if it.get("offer_id"):   # en las ofertas de ML (SMART, PRICE_MATCHING) hay que indicar cuál
                    prm["offer_id"] = it["offer_id"]
                rr = ml.pedir("DELETE", f"/seller-promotions/items/{mla}", reintentos=2, params=prm)
                res_txt = "QUITADA" if rr.status_code in (200, 204) else f"ERROR {rr.status_code}"
                det = "" if rr.status_code in (200, 204) else rr.text[:250]
                time.sleep(0.3)
            else:
                det = "Prueba: se quitaría de la promoción"
            res.append(fila + [precio, res_txt, det, f["fila"]])
            hechos += 1
            continue
        if est in ("started", "pending"):
            res.append(fila + [it.get("price") or "", "YA ESTABA", "Ya adherida", f["fila"]])
            continue
        cuerpo = {"promotion_id": pid, "promotion_type": tipo}
        precio = ""
        if tipo in ("DEAL", "SELLER_CAMPAIGN", "DOD", "LIGHTNING"):
            pp = num(r[I["PRECIO A PONER"]])
            precio = pp or num(r[I["PRECIO QUE PROPONE ML"]]) or it.get("suggested_discounted_price")
            mn, mx = it.get("min_discounted_price"), it.get("max_discounted_price")
            if not precio or (mn and precio < mn) or (mx and precio > mx):
                res.append(fila + [precio or "", "NO SE HIZO",
                                   f"Precio fuera del rango que permite ML ({mn} a {mx})", f["fila"]])
                continue
            cuerpo["deal_price"] = round(precio, 2)
        elif tipo in ("SMART", "PRICE_MATCHING", "PRICE_MATCHING_MELI_ALL", "UNHEALTHY_STOCK"):
            if not it.get("offer_id"):
                res.append(fila + ["", "NO SE HIZO", "ML no devolvió la oferta (offer_id) para aceptar", f["fila"]])
                continue
            cuerpo["offer_id"] = it["offer_id"]
            precio = it.get("price") or it.get("suggestion_final_price") or ""
        if DRY:
            res.append(fila + [precio, "SE ADHERIRÍA", json.dumps(cuerpo, ensure_ascii=False), f["fila"]])
        else:
            rr = ml.pedir("POST", f"/seller-promotions/items/{mla}", reintentos=2, params={"app_version": "v2"},
                          json=cuerpo)
            ok = rr.status_code in (200, 201)
            res.append(fila + [precio, "ADHERIDA" if ok else f"ERROR {rr.status_code}", "" if ok else rr.text[:250],
                               f["fila"]])
            time.sleep(0.3)
        hechos += 1

os.makedirs("salida", exist_ok=True)
with open("salida/promos_adhesion.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh, delimiter=";")
    w.writerow(["ID PROMO", "PROMOCIÓN", "MLA", "SKU", "TÍTULO", "DECISIÓN", "PRECIO", "RESULTADO", "DETALLE",
                "FILA PLANILLA"])
    w.writerows(res)
cuenta = {}
for x in res:
    k = x[7] if not str(x[7]).startswith("ERROR") else "ERROR"
    cuenta[k] = cuenta.get(k, 0) + 1
log(f"Resultado: {cuenta}")
errores = {}
for x in res:
    if str(x[7]).startswith("ERROR") or x[7] == "NO SE HIZO":
        errores[x[8][:120]] = errores.get(x[8][:120], 0) + 1
for k, v in sorted(errores.items(), key=lambda z: -z[1])[:15]:
    log(f"   {v} × {k}")
