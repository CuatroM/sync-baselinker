"""
Diagnóstico del sitio del proveedor (no escribe nada en ningún lado).

1. Abre la URL inicial y muestra el formulario de login (campos, acción, método).
2. Si están cargados los secrets PROVEEDOR_USER / PROVEEDOR_PASS, intenta loguearse
   y lista los links del panel que parezcan descargas (Excel, CSV, exportar, lista...).

Las credenciales nunca se imprimen.
"""

import os
import re
import sys
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URL = os.environ.get("URL_PROVEEDOR") or sys.argv[1]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
PALABRAS = re.compile(r"xls|xlsx|csv|export|descarg|download|lista|precio|stock|reporte|report",
                      re.I)


def describir_pagina(r, titulo):
    print(f"\n===== {titulo} =====")
    print(f"Estado: {r.status_code} | URL final: {r.url}")
    print(f"Content-Type: {r.headers.get('content-type')}")
    if r.history:
        print("Redirecciones:", " -> ".join(f"{h.status_code} {h.url}" for h in r.history))
    sopa = BeautifulSoup(r.text, "html.parser")
    print("Título:", (sopa.title.string.strip() if sopa.title and sopa.title.string else "-"))
    gen = sopa.find("meta", attrs={"name": "generator"})
    if gen:
        print("Plataforma (meta generator):", gen.get("content"))
    pistas = [p for p in ("wp-content", "woocommerce", "laravel", "csrf", "livewire",
                          "prestashop", "magento", "codeigniter", "ci_session")
              if p in r.text.lower() or p in str(r.cookies.keys()).lower()]
    if pistas:
        print("Pistas de plataforma:", ", ".join(pistas))
    print("Cookies:", list(r.cookies.keys()) or "-")
    return sopa


def describir_formularios(sopa, base):
    forms = sopa.find_all("form")
    print(f"\nFormularios encontrados: {len(forms)}")
    for i, f in enumerate(forms, 1):
        print(f"\n  Formulario {i}: method={f.get('method', 'get').upper()} "
              f"action={urljoin(base, f.get('action') or '')}")
        for campo in f.find_all(["input", "select", "textarea", "button"]):
            tipo = campo.get("type", campo.name)
            valor = campo.get("value", "")
            if tipo in ("password",) or len(valor) > 40:
                valor = f"<{len(valor)} caracteres>"
            print(f"    - {campo.name} type={tipo} name={campo.get('name')} "
                  f"id={campo.get('id')} value={valor!r}")
    return forms


CLAVES_JS = re.compile(r"usu_|login|ajax|\$\.post|fetch\(|\.php|location|submit|url\s*:", re.I)


def describir_scripts(ses, sopa, base):
    """Muestra las líneas de JavaScript que parecen manejar el login (botón type=button)."""
    print("\n----- JavaScript relacionado con el login -----")
    fuentes = []
    for s in sopa.find_all("script"):
        if s.get("src"):
            src = urljoin(base, s["src"])
            print(f"  script externo: {src}")
            if "jquery" in src.lower() or "bootstrap" in src.lower() or not src.startswith(
                    base.split("/")[0] + "//" + base.split("/")[2]):
                continue
            try:
                fuentes.append((src, ses.get(src, timeout=30).text))
            except requests.RequestException as e:
                print(f"    (no se pudo leer: {e})")
        elif s.string:
            fuentes.append(("inline", s.string))
    for origen, codigo in fuentes:
        lineas = [l.strip() for l in codigo.splitlines() if CLAVES_JS.search(l)]
        if lineas:
            print(f"\n  [{origen}]")
            for l in lineas[:40]:
                print("   ", l[:200])
    for b in sopa.find_all("button"):
        print(f"\n  botón: texto={b.get_text(strip=True)!r} onclick={b.get('onclick')!r} "
              f"class={b.get('class')}")


def listar_links(sopa, base, titulo):
    vistos = set()
    print(f"\n----- Links que parecen descargas o listados ({titulo}) -----")
    for a in sopa.find_all("a", href=True):
        href = urljoin(base, a["href"])
        texto = " ".join(a.get_text().split())[:60]
        if (PALABRAS.search(href) or PALABRAS.search(texto)) and href not in vistos:
            vistos.add(href)
            print(f"  {texto!r} -> {href}")
    if not vistos:
        print("  (ninguno)")
    print(f"\n----- Menú / todos los links internos ({titulo}) -----")
    for a in sopa.find_all("a", href=True)[:80]:
        print(f"  {' '.join(a.get_text().split())[:50]!r} -> {urljoin(base, a['href'])}")


def main():
    ses = requests.Session()
    ses.headers["User-Agent"] = UA
    r = ses.get(URL, timeout=60, allow_redirects=True)
    sopa = describir_pagina(r, "Página inicial")
    print("\n----- HTML de la página inicial -----")
    print(r.text[:15000])
    forms = describir_formularios(sopa, r.url)
    describir_scripts(ses, sopa, r.url)

    usuario = os.environ.get("PROVEEDOR_USER")
    clave = os.environ.get("PROVEEDOR_PASS")
    if not (usuario and clave):
        print("\nNo hay secrets PROVEEDOR_USER / PROVEEDOR_PASS: no se intenta el login.")
        return
    form = next((f for f in forms if f.find("input", attrs={"type": "password"})), None)
    if not form:
        print("\nNo encontré un formulario con contraseña en la página inicial.")
        return

    datos = {}
    for campo in form.find_all("input"):
        nombre = campo.get("name")
        if not nombre:
            continue
        tipo = (campo.get("type") or "text").lower()
        if tipo == "password":
            datos[nombre] = clave
        elif tipo in ("text", "email") and not campo.get("value"):
            datos[nombre] = usuario
        elif tipo in ("checkbox", "radio"):
            if campo.has_attr("checked"):
                datos[nombre] = campo.get("value", "on")
        else:
            datos[nombre] = campo.get("value", "")
    boton = form.find(["button", "input"], attrs={"type": "submit"})
    if boton is not None and boton.get("name"):
        datos[boton["name"]] = boton.get("value", "")

    accion = urljoin(r.url, form.get("action") or "")
    metodo = (form.get("method") or "post").lower()
    print(f"\nIntentando login: {metodo.upper()} {accion} con campos {list(datos)}")
    r2 = (ses.post(accion, data=datos, timeout=60) if metodo == "post"
          else ses.get(accion, params=datos, timeout=60))
    sopa2 = describir_pagina(r2, "Después del login")
    if sopa2.find("input", attrs={"type": "password"}):
        print("\n⚠ Sigue apareciendo un campo de contraseña: el login probablemente falló "
              "(usuario/clave, captcha o un paso extra).")
    listar_links(sopa2, r2.url, "después del login")


if __name__ == "__main__":
    main()
