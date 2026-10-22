"""
Crea publicaciones nuevas en Mercado Libre a partir de las fichas FEMA.

Por cada SKU:
  - título (máx. 60), categoría, atributos de la ficha + obligatorios, descripción con el formato acordado
  - fotos desde la carpeta de Drive (SKU.jpg, SKU_02.jpg…) y la imagen de ficha técnica al final
  - precio = precio de BaseLinker (mayorista del Excel + 25 %) + MARGEN % encima; stock fijo STOCK (1)
  - solo crea en Mercado Libre: no escribe nada en BaseLinker (la vinculación se hace a mano en BASE)
  - SKU = CÓDIGO#FEMA, publicación Clásica, Mercado Envíos, queda PAUSADA para revisar
DRY_RUN=1: arma todo y lo valida contra Mercado Libre (/items/validate) sin crear nada.
"""
import csv
import json
import os
import re
import sys
import time

import yaml
try:
    from PIL import ImageFile
    ImageFile.LOAD_TRUNCATED_IMAGES = True     # fotos de Drive con unos bytes de menos
except ImportError:
    pass

import sync
from drive.carpeta import descargar, listar
from ml.cliente import ML, log
from ml import fichas_map as fm

PROVEEDOR = (os.environ.get("PROVEEDOR") or "FEMA").strip().upper()
XP = PROVEEDOR == "EXTRAPOWER"
JYA = PROVEEDOR == "JYA"
GEN = XP or JYA          # proveedores con fichas armadas desde su web (reglas genéricas de categoría y obligatorios)
SUFIJO = "#EXTRAPOWER" if XP else "#JYA" if JYA else "#FEMA"
dry = os.environ.get("DRY_RUN", "1") == "1"
_sk = os.environ["SKUS"].strip()
if _sk.startswith("@"):                      # @tanda1 -> datos/tanda1.json
    skus = json.load(open(f"datos/{_sk[1:]}.json", encoding="utf-8"))
else:
    skus = [s.strip() for s in _sk.split(",") if s.strip()]
if not skus:
    raise SystemExit("No se indicaron SKUs: completá el campo skus (por ejemplo @xp_prueba)")
print(f"{len(skus)} SKUs a procesar ({PROVEEDOR})", flush=True)
margen = float(os.environ["MARGEN"].replace(",", ".").replace("%", ""))
tipo = os.environ.get("TIPO", "gold_special")            # gold_special = Clásica
envio = os.environ.get("ENVIO", "me2")
stock_fijo = int(os.environ.get("STOCK") or 1)

TITULOS = {   # títulos cuidados (máx. 60 caracteres); si no hay, se arma desde la ficha
    "69351050": "Aspiradora Industrial Polvo Y Liquido 60 L 1200w Fema Inox",
    "69351065": "Aspiradora Industrial Polvo Y Liquido 20 L 1400w Fema Inox",
    "69351110": "Aspiradora Industrial 80l 2000w 2 Motores Polvo Liquido Fema",
    "69503874": "Criquet Carro Hidraulico 2 Toneladas 125-300 Mm Fema",
    "69504110": "Guinche Pluma Plegable Hidraulico 2 Toneladas Taller Fema",
    "69503222": "Elevador De Autos 2 Columnas 4000 Kg 3hp 220v Traba Manual",
    "69503223": "Elevador De Autos 2 Columnas 4000 Kg 3hp 380v Traba Manual",
    "69350850": "Aparejo Electrico Fema 800 Eco 800 Kg 220v Doble Gancho 30m",
}
CATEGORIAS = {   # categoría forzada por familia (si no, la sugiere Mercado Libre)
    "Aspiradora Polvo y Líquido": "MLA371973",
    "Criquet Carro": "MLA433673",
}
# reglas por tipo de producto (sobre el título): (expresión, categoría fija o None, búsqueda para Mercado Libre)
REGLAS_CAT = [
    (r"^(Lija|HOJA Lija|Bandas Tela|Rollos Tela|Tela Esmeril|Espuma Abrasiva|Fibra Abrasiva|Rollos de Fibra"
     r"|Discos Speed Grip|Discos Speed Lok|Rueda Flap Lija)", "MLA384753", None),               # Lijas
    (r"^(Disco|Ruedas? |Rueda Convoluta|Clipper|Para Sensitiva|Bujes de Reduccion)", "MLA372119", None),  # Discos y Piedras
    (r"^Lima ", "MLA30832", None),                                                               # Limas
    (r"^Cinta Embalar", "MLA405117", None),                                                      # Cintas de Embalaje
    (r"^Cinta (Doble|de Papel|Automotor|DUCT|Silver)|Enmascarar", "MLA105436", None),           # Cintas Adhesivas
    (r"^Silicona|^Sellador de (Roscas|Juntas)", "MLA435294", None),                              # Selladores y Siliconas
    (r"^(Traba Roscas|Adhesivo Instant|Cola Vin|Cola Spray|Gel Tekbond|Kit Retrovisor)", "MLA105406", None),  # Pegamentos
    (r"^(Pan Afilado|Afilador|Afilado de Sierras)", "MLA413465", None),                          # Afiladores Manuales
    (r"^Soporte de Chasis", None, "soporte caballete para moto taller"),
    (r"^(Boina|Soporte|Adaptador Soporte)", "MLA424490", None),                                  # Pads
    (r"^Repuesto Filtro", "MLA455478", None),                                                    # Cartuchos para Máscaras
    (r"^(Grafito Seco|LUB )", "MLA392346", None),                                                # Lubricantes
    (r"^Filtro de Pintura", None, "colador filtro de pintura para soplete"),
    (r"^Sellador (Parabrisas|Carrocería)", None, "sellador poliuretano automotor"),
    (r"^Pasta para Pulir", None, "pasta para pulir automotor"),
    # ---- tanda 4
    (r"^Piedra Partida", "MLA413514", None),                                                     # Piedra Partida
    (r"^Adhesivo Cerámico", "MLA433729", None),                                                  # Adhesivos para Pisos y Muros
    (r"^Nylon", "MLA429066", None),                                                              # Tanzas
    (r"^(Cadena|Uniones para Cadena)", "MLA455588", None),                                       # Cadenas para Motosierras
    (r"^Motor (Eléctrico )?WEG", "MLA413673", None),                                             # Motores Eléctricos
    (r"^Motor (Nafta|Diesel|Briggs)", "MLA413316", None),                                        # Motores Estacionarios
    (r"^(Caño Expelente|Caño Aspirante|Manga Plana|Caño Riego)", "MLA11040", None),              # Mangueras
    (r"^Eje Vibrador", "MLA403878", None),                                                       # Vibradores de Inmersión
    (r"^Antiempastante", "MLA384753", None),                                                     # Lijas
    (r"^Adhesivo Calzadista", "MLA105406", None),                                                # Pegamentos
    (r"^Limpiador Weber", None, "limpiador para ceramicos y porcelanato"),
    (r"^Succionador", None, "bomba manual trasvase combustible"),
    (r"^Funda para Pistola", None, "pico surtidor combustible"),
    # ---- tanda 2
    (r"^(Acelerante de Fragüe|Anticongelante|Aditivo Vinílico|Desmoldante|Desencofrante|Infiltración)", "MLA435312", None),  # Aditivos
    (r"^(Hormigón Impreso|Capa Color)", "MLA456027", None),                                       # Morteros
    (r"^(Pintura Asfáltica|Imprimación Asfáltica)", "MLA372048", None),                           # Membrana Asfáltica
    (r"^Llave T", "MLA30831", None),                                                             # Llaves
    (r"^Grancilla|^Arena", "MLA393048", None),                                                   # Arena
    (r"^Taco Diamantado", "MLA372119", None),                                                    # Discos y Piedras
    (r"^Regla Vibradora", None, "regla vibradora para hormigon"),
    (r"^Isover Andina", "MLA372044", None),                                                      # Cielo Raso
    (r"^Isover (?!Herramientas|Escuadra|Repuesto|Cola|Clavos|Cinta)", "MLA434826", None),         # Rollos Aislantes
    (r"^Aislante Isolant|^Banda Acoustic|^Burlete Puertelete", "MLA434826", None),
    (r"^Motocompresor", None, "motocompresor para compresor de aire"),
    (r"^Escarificador", None, "escarificadora de pisos hormigon"),
    (r"^Juego 4 Paletas Alisadora", None, "paletas para alisadora de hormigon helicoptero"),
    (r"^Impermeabilizante Planiseal|^Impermeabilizante Mapelastic", None, "impermeabilizante cementicio"),
    (r"^Desmoldante|^Desencofrante", None, "desmoldante para encofrados"),
    (r"^Capa Color", None, "hormigon impreso color"),
    (r"^Acelerante de Fragüe", None, "acelerante de fragüe hormigon"),
    (r"^Malla de Fibra de Vidrio", None, "malla de fibra de vidrio para revoque"),
    (r"^Esquinero de PVC|^Perfil Esquinero", None, "esquinero pvc para revoque"),
    (r"^Cable Sumergible", None, "cable sumergible para bomba"),
    (r"^Mezcla para Baldosas", None, "adhesivo para baldosas ceramicos"),
    (r"^Martillo Hidráulico|^Punta para Martillo Hidráulico", None, "martillo hidraulico para retroexcavadora"),
    (r"^(Velcro y Goma|Goma Porta Velcro)", None, "plato porta lija velcro pulidora"),
    (r"^Pico Boquilla", None, "pico airless para maquina de pintar"),
]
RAICES_OK = {"Herramientas", "Construcción", "Accesorios para Vehículos", "Industrias y Oficinas"}
if XP:   # EXTRAPOWER también vende hogar, camping y electro (anafes, calefones, iluminación)
    RAICES_OK |= {"Hogar, Muebles y Jardín", "Electrodomésticos y Aires Ac.", "Deportes y Fitness"}
if JYA:  # J&A: marroquinería, bazar, regalería, textil, librería, juguetes
    RAICES_OK |= {"Hogar, Muebles y Jardín", "Ropa y Accesorios", "Deportes y Fitness", "Juegos y Juguetes",
                  "Arte, Librería y Mercería", "Belleza y Cuidado Personal", "Bebés", "Electrodomésticos y Aires Ac.",
                  "Animales y Mascotas", "Souvenirs, Cotillón y Fiestas", "Accesorios para Vehículos", "Computación",
                  "Celulares y Teléfonos", "Electrónica, Audio y Video", "Salud y Equipamiento Médico"}
RAICES_FAM = {   # familias que además pueden ir en otras categorías raíz
    "Electrobombas": {"Hogar, Muebles y Jardín"},
    "Repuestos": {"Hogar, Muebles y Jardín", "Electrodomésticos y Aires Ac."},
    "Energía Solar": {"Hogar, Muebles y Jardín", "Electrodomésticos y Aires Ac."},
    "Ventiladores": {"Electrodomésticos y Aires Ac.", "Hogar, Muebles y Jardín"},
    "Pinturas": {"Hogar, Muebles y Jardín"},
    "Jardinería": {"Hogar, Muebles y Jardín"},
    "Acoples": {"Hogar, Muebles y Jardín"},
    "Seguridad": {"Hogar, Muebles y Jardín"},
    "Limpieza": {"Hogar, Muebles y Jardín"},
    "Aire Libre": {"Hogar, Muebles y Jardín", "Deportes y Fitness"},
    "Aspiradoras": {"Electrodomésticos y Aires Ac."},
    "Escaleras": {"Hogar, Muebles y Jardín"},
}
CAT_NO = {"Camillas para Mecánicos", "Cortadoras de Concreto", "Cortadoras de Pelo", "Rollos de Embalaje",
          "Sensitivas", "Frascos con Gotero", "Strass Autoadhesivos", "Porta Cds y Dvds", "Barras de Puntal",
          "Bidón Lavamanos", "Vibradores de Inmersión", "Refrigerante y Anticongelante", "Bloques de Hormigón",
          "Selladoras de Bolsas", "Tambores de Almacenamiento", "Matafuegos", "Llaves de Paso", "Motores", "Otros",
          "Tanque Expansión Refrigerante", "Baldosas de Cemento", "Pisos de Madera", "Paneles de Puertas"}
GTIN = json.load(open("datos/gtin.json", encoding="utf-8")) if os.path.exists("datos/gtin.json") else {}
if JYA and os.path.exists("datos/jya_gtin.json"):   # códigos de barras de J&A (Excel SKU / EAN del usuario)
    GTIN = json.load(open("datos/jya_gtin.json", encoding="utf-8"))
precio_minimo = float((os.environ.get("PRECIO_MINIMO") or "100000").replace(".", "").replace(",", "."))
_cat_cache = {}


def info_cat(cat):
    if cat not in _cat_cache:
        _cat_cache[cat] = ml.get(f"/categories/{cat}")
    return _cat_cache[cat]


_por_nombre = {}


def categoria_por_nombre(nombres, raiz="MLA1500"):
    """Busca en el árbol de 'Construcción' una categoría hoja cuyo nombre contenga alguno de los textos dados."""
    clave = (tuple(nombres), raiz)
    if clave in _por_nombre:
        return _por_nombre[clave]
    pend, vistos, encontrada = [raiz], 0, None
    while pend and vistos < 120 and not encontrada:
        cid = pend.pop(0)
        vistos += 1
        hijos = info_cat(cid).get("children_categories") or []
        for h in hijos:
            if any(n.lower() in h["name"].lower() for n in nombres):
                encontrada = h["id"]
                break
        pend += [h["id"] for h in hijos]
    # si no es hoja, bajar a "Otros" o al primer hijo
    while encontrada and info_cat(encontrada).get("children_categories"):
        hijos = info_cat(encontrada)["children_categories"]
        encontrada = next((h["id"] for h in hijos if h["name"].lower().startswith("otros")), hijos[0]["id"])
    _por_nombre[clave] = encontrada
    log(f"categoría por nombre {nombres}: {encontrada} {info_cat(encontrada).get('name') if encontrada else ''}")
    return encontrada


CAT_NOMBRE = [   # (expresión sobre el título, nombres de categoría a buscar dentro de Construcción)
    (r"^Juego 4 Paletas Alisadora", ["Equipos para la Construcción", "Equipos para Construcción", "Maquinaria", "Materiales de Construcción"]),
    (r"^(Malla|Esquinero|Perfil Esquinero|Tela de Refuerzo)", ["Materiales de Construcción", "Materiales para Construcción", "Equipos para la Construcción"]),
]


import unicodedata

# Repuestos: categorías que ML sugiere pero no corresponden a una pieza (máquinas enteras o rubros ajenos)
CAT_NO_REP = {
    "Cilindros para Tóner", "Compresores de Freno de Aire", "Anteojos y Antiparras", "Separadores de Clara y Yema",
    "Canastos Freidores", "Cuenta Ganado", "Bares", "Visores para Cascos", "Manijas Levanta Vidrios",
    "Depósitos de Líquido de Frenos", "Arnés de Seguridad", "Esponjas de Limpieza", "Cierres Centralizados",
    "Defensas", "Alambrados", "Manijas para Puertas", "Barrales Organizadores", "Kits de Envoltura Térmica",
    "Resortes de Bisagra", "Cajas para Térmicas", "Interruptores Automáticos", "Topes de Amortiguador",
    "Pernos de Caliper", "Trabas de Discos", "Juntas de Válvula EGR", "Anillos Sincronizadores", "Lubricantes",
    "Aditivos", "Grupos Electrógenos", "Motosierras a Explosión", "Motosierras Eléctricas", "Compresores",
    "Motobombas a Combustión", "Motobombas", "Motocultivadores", "A Combustión", "Hoyadoras de Combustión",
    "Bombas Centrífugas", "Motores Estacionarios", "Motores Eléctricos", "Cortacerco", "Fumigadores y Pulverizadores",
    "De Banco", "Engrapadoras y Clavadoras", "Destornilladores", "Sierras", "Pads", "Discos y Piedras",
    "Bancos de Trabajo", "Cortadoras de Cerámica", "Aparejos Manuales", "Rectificadoras", "Afiladores",
    "Sopladores de Supercargador", "Tableros", "De Balizas", "Bolsas y Canastos Recolectores", "Colectores de Polvo",
    "Tanques de Agua", "Grampas Prensa Cable", "Llaves", "Hidrolavadoras", "Soldadoras", "Hormigoneras",
    "Cobertores de Caja", "Plaquetas", "Resortes a Gas", "Para Motor", "Soportes para Barrales", "Ventiladores",
    "Bobinadoras", "Pinzas para Anillos", "Llaves de Luz", "Porta Electrodos", "Taladros a Combustión",
    "Prensas Mecánicas", "Llaves Saca Bujías", "Pinzas de Corte", "Acoples Rápidos", "Aceites para Motor", "Controles",
    "Rodillos Compactadores", "Elevadores de Autos", "Aparejos Eléctricos", "A Gas", "Motores Nafteros",
    "Pulverizadores a Explosión", "Llaves de Corte", "Secadores de Pasta", "Canastos de mimbre", "Carteles",
    "Tableros para Herramientas", "Tapas de Tanques de Agua", "Conectores Eléctricos", "Arnés de Cables",
    "Tapas de Estátores", "Disipadores", "Palancas de Caja de Cambios", "Varillas de Frenos",
    "Generadores", "Desmalezadoras", "Bordeadoras", "Cortadoras de Césped", "Aspiradoras", "Sopladores",
}
CAT_GENERICA_REP = {"Kits de Repuestos", "Repuestos", "Repuestos y Accesorios", "Otros Repuestos"}
MAQUINAS = [
    (r"motosierra|\bespada\b|\bcadena\b", "motosierra"), (r"desmalezadora|bordeadora", "desmalezadora"),
    (r"cortac[eé]sped|tractor|\bmtd\b|murray|poulan|stiga|cesped", "cortacesped"), (r"soplador", "soplador"),
    (r"cortacerco", "cortacerco"), (r"generador|\bgen\.|\d+ ?kw\b|grupo electr", "grupo electrogeno"),
    (r"compresor", "compresor"), (r"hidrolavadora", "hidrolavadora"), (r"soldadora|soldadura", "soldadora"),
    (r"hormigonera", "hormigonera"), (r"motobomba|electrobomba|\bbomba\b", "bomba de agua"),
    (r"aspiradora", "aspiradora"), (r"transpalet|autoelevador", "autoelevador"), (r"pisonador|vibroapisonador", "pisonador"),
    (r"hoyadora", "motohoyadora"), (r"briggs|honda|\bgx ?\d|\bhp\b", "motor naftero"),
]


def _norm(s):
    s = unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z]{3,}", s)


def _raiz(w):
    if re.search(r"[^aeiou]es$", w) and len(w) > 5:
        return w[:-2]
    return w[:-1] if w.endswith("s") else w


def _raices(s):
    return {_raiz(w) for w in _norm(s) if w not in {"para", "con", "los", "las", "del", "otros", "tipo"}}


def _cabeza(s):
    """Primera palabra con contenido (la pieza): 'Juegos de Aros' -> 'juego'."""
    ws = [w for w in _norm(s) if w not in {"para", "con", "los", "las", "del", "otros", "tipo", "kit", "kits", "juego", "juegos"}]
    return _raiz(ws[0]) if ws else ""


# palabras de rubros ajenos: si la categoría las tiene, no es para un repuesto de máquina
AJENAS_REP = re.compile(r"cortina|port[oó]n|pileta|lavarropa|reloj|comestible|carne|pasta|mimbre|baño|mueble|robot|canilla|"
                        r"cloro|precio|perchero|banqueta|aires ac|remoto|bocina|puerta|carrocer|jard[ií]n|neum[aá]tic|"
                        r"hidroneum|presurizad|aire comprimido|respirador|portátil|calefacci|cascos|vidrio|toner|tóner|licuadora|hornalla|"
                        r"decorativ|ajo\b|bidon|bidón|monopat|punta y pala|seguridad|bobinas compresoras|cocina|termo|solar|"
                        r"hisopo|cierre centralizado|antip[aá]nico|sommier|enchufe|vehicular|^tazas$|desagote|frecuencia|"
                        r"esclavo|distribuidor|residuales|caja de cambio|de freno|cajas de paso|faros|pastel|"
                        r"navide|cepillo|basura|bombas de calor|ox[ií]geno|dispensador|protectores bra|sopapa|"
                        r"carcasas de controles|riego|electrodos para soldadura|separadores de ruedas|aros centradores|pistolas para pintar|"
                        r"estaciones de soldad|cartuchos para m|para mesas|antivuelco|c[aá]psula|molinete|prensas hidr|"
                        r"patas de cajas|tubos de direcci|^a carb|rastrillo|cargadores de pared|^bandejas$|"
                        r"[oó]smosis|textil|carnicer|ropa|embalaje|ventana|amortiguador|terminales de direcci|suspensi|"
                        r"aerodin|cilindros maestros|bolas de enganche|^asientos$|"
                        r"dibujo|heladera|cocteler|higi[eé]nico|comandas|escombros", re.I)

def categoria_repuesto(f, t):
    """Repuestos: la categoría tiene que nombrar la pieza del título (no la máquina entera ni otro rubro).
    Primero categorías de herramientas/hogar; si no hay, de vehículos; si no, una genérica de repuestos."""
    tit = {_cabeza(f["nombre"])}   # la pieza: primera palabra del nombre
    maq = next((m for e, m in MAQUINAS if re.search(e, t, re.I)), "")
    pieza = " ".join(f["nombre"].split()[:2])
    consultas = [t, f"{pieza} {maq}".strip(), f"{f['nombre'].split()[0]} para {maq}" if maq else pieza]
    vehiculo = None
    for q in consultas:
        for d in ml.get("/sites/MLA/domain_discovery/search", q=q, limit=8) or []:
            ci = info_cat(d["category_id"])
            nom = ci.get("name") or ""
            raiz = ((ci.get("path_from_root") or [{}])[0]).get("name")
            if raiz not in RAICES_OK and raiz not in RAICES_FAM["Repuestos"]:
                continue
            if nom in CAT_NO or nom in CAT_NO_REP:
                continue
            if AJENAS_REP.search(nom):
                continue
            if not (nom in CAT_GENERICA_REP or _cabeza(nom) in tit and _cabeza(nom)):
                continue
            if raiz == "Accesorios para Vehículos":
                vehiculo = vehiculo or d["category_id"]
                continue
            return d["category_id"]
    if vehiculo:
        return vehiculo
    for raiz in ("MLA407134", "MLA1574", "MLA1500"):
        c = categoria_por_nombre(["Repuestos"], raiz)
        if c:
            return c
    return None


CAT_FAMILIA = {}   # EXTRAPOWER: categoría ya elegida para cada familia (Espátula, Fresa…) en esta corrida
XP_CAT = [   # EXTRAPOWER: categorías fijas donde la sugerencia de ML se equivoca
    (r"^Arco de Sierra", "MLA455250"),          # Corte y Desbaste > Otros (Serruchos pide tipo de serrucho)
    (r"^Corta ?Ladrillos", "MLA414196"),        # Cinceles y Formones
]


def elegir_categoria(f, t):
    """Categoría: forzada por familia o por regla; si no, la primera sugerencia de ML que sea de herramientas."""
    if XP:
        for exp, c in XP_CAT:
            if re.search(exp, t, re.I):
                return c
    if f["familia"] == "Repuestos":
        return categoria_repuesto(f, t)
    if CATEGORIAS.get(f["familia"]):
        return CATEGORIAS[f["familia"]]
    for exp, nombres in CAT_NOMBRE:
        if re.search(exp, t, re.I):
            c = categoria_por_nombre(nombres)
            if c:
                return c
    consultas = [t]
    for exp, fija, q in REGLAS_CAT:
        if re.search(exp, t, re.I):
            if fija:
                return fija
            consultas = [q, t]
            break
    consultas.append(" ".join(f["nombre"].split()[:3]))
    if JYA:   # J&A: ML se confunde con la marca en el título -> primero el nombre sin marca ni código, y el rubro de la tienda
        sin_m = f["nombre"]
        for w in (f.get("marca") or "").split() + ["Love", "Las"]:
            sin_m = re.sub(rf"(?i)\b{re.escape(w)}\b", " ", sin_m)
        sin_m = " ".join(w for w in sin_m.split() if not re.search(r"\d{3,}", w))
        rubro = (f.get("categoria_tienda") or "").split(">")[-1].strip()
        consultas = [sin_m, f"{rubro} {' '.join(sin_m.split()[:2])}".strip()] + consultas
        if re.match(r"(?i)(set de )?(broches?|pinzas? clip|colitas|hebillas?|scrunchies?|scunzi|vinchas?)\b", f["nombre"]):
            consultas = ["broches para el pelo", "accesorios para el pelo"] + consultas
    if GEN:   # la búsqueda de ML a veces vuelve vacía: más intentos con el nombre acortado y lo ya elegido por familia
        pal = [w for w in re.findall(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]+", f["nombre"]) if len(w) > 2]
        consultas += [" ".join(pal[:4]), " ".join(pal[:2]), " ".join(pal[:1])]
        if CAT_FAMILIA.get(f["familia"]) and f["familia"].lower() not in ("set", "kit", "juego", "accesorio", "accesorios", "repuesto", "pack"):
            consultas.append(None)
    for q in consultas:
        if q is None:
            return CAT_FAMILIA[f["familia"]]
        res = ml.get("/sites/MLA/domain_discovery/search", q=q, limit=6) or []
        if GEN and not res:
            time.sleep(1)
            res = ml.get("/sites/MLA/domain_discovery/search", q=q, limit=6) or []
        for d in res:
            ci = info_cat(d["category_id"])
            raiz = ((ci.get("path_from_root") or [{}])[0]).get("name")
            if (raiz in RAICES_OK or raiz in RAICES_FAM.get(f["familia"], set())) and ci.get("name") not in CAT_NO:
                CAT_FAMILIA.setdefault(f["familia"], d["category_id"])
                return d["category_id"]
    return None

import glob
fichas = {}
for _arch in sorted(glob.glob("datos/xp_fichas*.json" if XP else "datos/jya_fichas*.json" if JYA else "datos/fichas_*.json")):
    for _f in json.load(open(_arch, encoding="utf-8")):
        fichas.setdefault(_f["sku"], _f)
cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
b = cfg["extrapower"] if XP else cfg["jya"] if JYA else cfg["baselinker"]

# ---------------- primero la autorización de Mercado Libre (el código vence en minutos)
ml = ML(os.environ.get("ML_CODIGO"))

# ---------------- precio y stock desde BaseLinker
bl = sync.BaseLinker(os.environ["BL_TOKEN"])
cat_bl = bl.catalogo(int(b["inventory_id"]))
por_sku = {sync.normalizar_codigo(p.get("sku")): p for p in cat_bl.values()}

# ---------------- IVA por SKU desde el Excel de FEMA (columna IMPUESTOS)
import io
import pandas as pd
if JYA:   # J&A: precio de la web (sin IVA) guardado en la ficha
    IVA = {sync.normalizar_codigo(k): 21 for k in fichas}
    PRECIO_XLS = {sync.normalizar_codigo(k): float(v.get("precio_web") or 0) for k, v in fichas.items()}
    MULT = 1
elif XP:   # EXTRAPOWER: lista de precios del proveedor (LISTA +IVA, IVA 21 %)
    _lx = json.load(open("datos/extrapower_lista.json", encoding="utf-8"))
    IVA = {sync.normalizar_codigo(x["cod"]): 21 for x in _lx}
    PRECIO_XLS = {sync.normalizar_codigo(x["cod"]): float(x["lista"] or 0) for x in _lx}
    MULT = 1
else:
    _xls = pd.read_excel(io.BytesIO(sync.descargar_lista(cfg)), dtype=object)
    _xls.columns = [str(c).strip().upper() for c in _xls.columns]
    IVA = {sync.normalizar_codigo(r["SKU"]): r.get("IMPUESTOS") for _, r in _xls.iterrows()}
    MULT = float(cfg.get("reglas", {}).get("multiplicador_precio", 1) or 1)
    PRECIO_XLS = {sync.normalizar_codigo(r["SKU"]): sync.a_numero(r.get("PRECIO MAYORISTA")) for _, r in _xls.iterrows()}

# ---------------- datos extra por SKU (por nombre de atributo de Mercado Libre)
EXTRA = {
    "69504110": {"Tipo de grúa": "Plegable", "Requiere ensamblado": "Sí"},
    "69351050": {"Color": "Gris"}, "69351065": {"Color": "Gris"}, "69351110": {"Color": "Gris"},
}
COLORES = ["gris", "negro", "rojo", "azul", "amarillo", "naranja", "verde", "blanco", "plateado", "celeste",
           "marrón", "violeta", "rosa"]


def color_de(f, t):
    """Color más aproximado según título o ficha; si no hay ninguno, Blanco."""
    texto = (t + " " + f["nombre"] + " " + " ".join(f"{k} {v}" for k, v in f["specs"].items())).lower()
    for c in COLORES:
        if re.search(rf"\b{c}\b", texto):
            return "Gris" if c == "plateado" else c.capitalize()
    return "Blanco"
for linea in (os.environ.get("EXTRA") or "").split(";"):
    # formato: SKU|Atributo=Valor|Atributo=Valor ; SKU|...   o  *|Atributo=Valor para todos
    partes = [x.strip() for x in linea.split("|") if x.strip()]
    if len(partes) > 1:
        EXTRA.setdefault(partes[0], {}).update(dict(x.split("=", 1) for x in partes[1:] if "=" in x))

# ---------------- fotos y fichas en Drive
fotos = [] if GEN else [x for x in listar(os.environ["DRIVE_FOTOS"], profundidad=0) if not x["carpeta"]]
fichas_img = [] if GEN else [x for x in listar(os.environ["DRIVE_FICHAS"], profundidad=0) if not x["carpeta"]]
EXT = (".jpg", ".jpeg", ".png", ".webp")


def imagenes(sku):
    if GEN:   # EXTRAPOWER / J&A: fotos de la web del proveedor (guardadas en la ficha)
        return [{"nombre": u.rsplit("/", 1)[-1], "url": u} for u in (fichas.get(sku) or {}).get("fotos") or []][:10]
    def ok(n, extra=""):
        base, ext = os.path.splitext(n)
        return ext.lower() in EXT and re.fullmatch(rf"{re.escape(sku)}{extra}", base, re.I)
    prod = [x for x in fotos if ok(x["nombre"]) or ok(x["nombre"], r"[_\- ]\(?[0-9a-z]{1,3}\)?")]
    vistos, unicas = set(), []
    for x in sorted(prod, key=lambda x: (os.path.splitext(x["nombre"])[0].lower() != sku.lower(), x["nombre"])):
        base = os.path.splitext(x["nombre"])[0].lower()
        if base not in vistos:                     # si hay .jpg y .png con el mismo nombre, uno solo
            vistos.add(base)
            unicas.append(x)
    ficha = [x for x in fichas_img if os.path.splitext(x["nombre"])[1].lower() in EXT
             and re.fullmatch(rf"{re.escape(sku)}_ficha(nueva)?", os.path.splitext(x["nombre"])[0], re.I)]
    return unicas[:9] + ficha[:1]


def texto_ml(t):
    for a, b2 in (("⁻¹", "-1"), ("⁻", "-"), ("¹", "1"), ("²", "2"), ("³", "3"), ("−", "-"), ("…", "..."),
                  ("\u200b", ""), ("\xa0", " ")):
        t = t.replace(a, b2)
    # ML solo acepta texto plano: se descarta cualquier símbolo raro que quede (emojis, flechas, etc.)
    t = "".join(c for c in t if c in "\n\t•" or (" " <= c <= "~") or ("\xa1" <= c <= "\xff"))
    for a, b2 in (("≤", "hasta "), ("≥", "desde "), ("±", "+/-"), ("’", "'"), ("“", '"'), ("”", '"'),
                  ("–", "-"), ("—", "-"), ("×", "x"), ("Μm", "micrones"), ("µm", "micrones"), ("μm", "micrones"),
                  ("Μ", "M"), ("µ", "u"), ("μ", "u"), ("<", "hasta "), (">", "desde ")):
        t = t.replace(a, b2)
    return t


def titulo(f):
    t = TITULOS.get(f["sku"]) or f.get("titulo") or f["nombre"]
    if len(t) > 60:
        t = t[:60].rsplit(" ", 1)[0]
    return t


DESCARTE_GTIN = "DESCARTADO: la categoría exige código de barras y no lo tenemos (no se publica)"


def sin_gtin(r):
    """Regla: si Mercado Libre exige GTIN y no tenemos código de barras, el producto no se publica."""
    return r.status_code not in (200, 201, 204) and "[GTIN]" in r.text


def _sa(x):
    return unicodedata.normalize("NFKD", str(x).lower()).encode("ascii", "ignore").decode()


# valor por defecto cuando el texto no dice nada (atributo: palabras a buscar entre las opciones, en orden)
DEFECTO_OPC = {
    "POWER_SUPPLY_TYPE": ["corriente", "electric", "cable"],
    "HAND_SAW_TYPE": ["carpintero"], "FAUCET_CONTROL_TYPE": ["monocomando"],
    "UTILITY_KNIVE_TYPE": ["hoja segmentada", "retractil"], "HAND_PLANE_TYPE": ["bolsillo"], "IS_THERMIC": ["no"],
    "INCLUDES_CARTRIDGE": ["no"], "TOOL_TYPE": ["cincel", "manual"], "CHLORINE_FORMAT": ["granulad", "pastilla", "liquid"],
    "ALGAECIDE_FORMAT": ["liquid"], "PRODUCT_TYPE": ["escobillon", "escoba", "regaton"], "MOUNTING_TYPE": ["de pie"],
    "REQUIRES_ASSEMBLY": ["no"], "INCLUDES_ASSEMBLY_MANUAL": ["no"], "IS_FOLDABLE": ["no"],
    "AIR_PUMP_TYPE": ["mano", "manual", "pie"], "GENDER": ["unisex", "sin genero"],
    "SCREW_TYPE": ["autoperforante"], "APPLICATION": ["agua"], "WALL_ANCHOR_PLUG_TYPE": ["universal"],
    "ORGANIZER_TYPE": ["caja"], "RODENT_TRAP_TYPE": ["de resorte"], "FAN_TYPE": ["de piso"],
    "ELECTRIC_SHOWER_HEAD_TYPE": ["fijo"],
    "SIZE": ["unico", "u"], "HAT_AND_CAP_TYPE": ["gorra"], "MATE_GOURD_TYPE": ["acero", "mate"],
    "DOCUMENT_TYPE": ["pasaporte"],
}


def completar_por_texto(attrs, req, f, t):
    """EXTRAPOWER: obligatorios de lista cerrada. Elige la opción que aparece en el título/ficha;
    si no aparece ninguna, usa el valor por defecto razonable de DEFECTO_OPC."""
    texto = _sa(" ".join([t, f.get("nombre", ""), f.get("uso_pdf", ""), " ".join(f"{k} {v}" for k, v in f["specs"].items())]))
    for aid, a in req.items():
        if aid in attrs:
            continue
        if not a.get("values"):
            if aid == "CUTTING_BLADES_NUMBER":
                attrs[aid] = "1"
            elif aid in ("THERMO_CAPACITY", "CAPACITY", "UNIT_VOLUME"):
                m = re.search(r"(?i)(\d+(?:[.,]\d+)?)\s*(lts?|litros?|l|ml|cc)\b", t)
                if m:
                    u = m.group(2).lower()
                    attrs[aid] = f"{m.group(1).replace(',', '.')} {'mL' if u in ('ml', 'cc') else 'L'}"
            elif aid == "PIECES_NUMBER":
                m = re.search(r"(?i)x\s*(\d+)\s*(?:piezas|pzas?|pcs)|(\d+)\s*(?:piezas|pzas?|pcs)", t)
                attrs[aid] = (m.group(1) or m.group(2)) if m else "1"
            continue
        ops = [v["name"] for v in a["values"] if v.get("name")]
        if aid == "POWER_SUPPLY_TYPE":
            pref = (["bateria", "recargable", "inalambric"] if re.search(r"recargable|bateria|inalambric|\b\d+\s*v\b(?!.*220)", texto)
                    else ["neumatic"] if "neumatic" in texto else DEFECTO_OPC[aid])
            el = next((o for p in pref for o in ops if p in _sa(o)), None)
        elif aid == "SCREW_TYPE" and "madera" in texto:
            el = next((o for o in ops if "madera" in _sa(o)), None)
        elif aid == "WALL_ANCHOR_PLUG_TYPE" and re.search(r"tope|expans", texto):
            el = next((o for o in ops if "expans" in _sa(o)), None)
        elif aid == "RODENT_TRAP_TYPE" and re.search(r"pega|adhes", texto):
            el = next((o for o in ops if "adhes" in _sa(o)), None)
        elif aid == "UTILITY_KNIVE_TYPE" and "circular" in texto:
            el = next((o for o in ops if "rotativ" in _sa(o)), None)
        else:
            el = next((o for o in sorted(ops, key=len, reverse=True) if len(_sa(o)) > 2 and re.search(rf"\b{re.escape(_sa(o))}", texto)), None)
            if not el:
                el = next((o for p in DEFECTO_OPC.get(aid, []) for o in ops if _sa(o).startswith(p) or p in _sa(o)), None)
        if el:
            attrs[aid] = el


def completar_obligatorios(attrs, cat_attrs, f, t):
    """Atributos obligatorios con opciones cerradas que se deducen del título / marca."""
    tl = t.lower()
    req = {a["id"]: a for a in cat_attrs if (a.get("tags") or {}).get("required")}
    if GEN:
        completar_por_texto(attrs, req, f, t)
    def poner(aid, val):
        if aid in req and aid not in attrs and val:
            ops = [v["name"] for v in req[aid].get("values") or []]
            attrs[aid] = next((o for o in ops if o.lower() == val.lower()), val)
    if "llave" in tl or "juego llaves" in tl:
        poner("WRENCH_TYPE", "Francesa" if "francesa" in tl or "ajustable" in tl else
              "Allen en T" if "allen" in tl and ("mango t" in tl or "llave t" in tl) else "Allen" if "allen" in tl else
              "Tipo T" if "torx" in tl or "llave t" in tl else "Crique" if "crique" in tl else "Combinada")
    poner("INSULATION_TYPES", "Acústico" if re.search(r"acusti|acoustic|panel pf|bajo piso|alfombra|andina|densus", tl) else "Térmico")
    if "clavo" in tl:
        poner("NAIL_HEAD_TYPE", "Plana")
        poner("NAIL_BODY_TYPE", "Helicoidal" if "espiral" in tl else "Liso")
    poner("MORTAR_TYPE", "Autonivelante" if "nivelad" in tl else "Reparador")
    poner("PAINT_TYPE", "Látex" if "látex" in tl else None)
    if re.search(r"membrana|impermeab|emulsi", tl):
        poner("PAINT_TYPE", "Impermeabilizante")
    poner("IS_FLAMMABLE", "Sí" if "solvente" in tl else "No")
    if re.search(r"\b18v\b|bater", tl):
        poner("POWER_SUPPLY_TYPE", "Batería")
    poner("POWER_SUPPLY_TYPES", "Energía industrial" if "380" in tl else "Corriente doméstica")
    poner("POWER_SUPPLY_TYPE", "Energía industrial" if "380" in tl else "Corriente doméstica" if re.search(r"220|controlador", tl) else None)
    if "chapa" in tl:
        poner("ROOF_PANEL_SHAPE", "Sinusoidal" if "sinusoidal" in tl else "Plana" if "lisa" in tl else "Trapezoidal")
    m = re.search(r"(\d+)\s*litros", tl)
    if m:
        poner("VOLUME_CAPACITY", f"{m.group(1)} L")
    poner("MANUFACTURER", f.get("marca") or "FEMA")
    poner("IS_FACTORY_KIT", "Sí" if re.search(r"juego|kit|set", tl) else "No")
    poner("FAN_TYPE", "De pared" if "pared" in tl else None)
    m = re.search(r"(\d+(?:,\d+)?)\s*mm", tl)
    if m:
        poner("THICKNESS", f"{m.group(1)} mm")
    m = re.search(r"(\d+(?:,\d+)?)\s*kva", tl)
    if m:
        poner("MAX_POWER", f"{m.group(1).replace(',', '.')} kVA")
    if "aerosol" in tl:
        poner("PAINT_TYPE", "Alta temperatura" if "alta temp" in tl else "Esmalte")
    if re.search(r"caño|manga|manguera", tl):
        poner("APPLICATION", "Industrial" if "combustible" in tl else "Agua")
    m = re.search(r"(\d+)\s*peldaños", tl)
    if m:
        poner("STEPS_NUMBER", m.group(1))
        poner("IS_FOLDABLE", "Sí")
    if re.search(r"tijera de podar|serrucho", tl):
        poner("POWER_SUPPLY_TYPE", "Operación manual")
    if re.search(r"pulverizador|cuenta litros", tl):
        poner("POWER_SUPPLY_TYPE", "Batería")
    if "bosch" in tl and "batería" not in tl:
        poner("POWER_SUPPLY_TYPE", "Corriente doméstica")
    poner("INDUSTRIAL_SAW_TYPE", "Circular" if "sierra copa" in tl else None)
    if re.search(r"nafta|combustible", tl):
        poner("FUEL_TYPE", "Nafta" if "nafta" in tl else "Diésel")
    poner("FINISHING_TROWELS_TYPE", "Fratacho" if "fratach" in tl else None)
    if re.search(r"v[aá]lvula|valbula|vavula", tl):
        poner("ENGINE_VALVE_TYPE", "De escape" if re.search(r"escape|sal\.?gases", tl) else "De admisión" if re.search(r"admisi|\bad\.", tl) else None)
    poner("SALE_FORMAT", "Unidad")
    if re.search(r"\brueda|\bllanta", tl):   # medidas de rueda desde el título, en mm
        def _mm(v, u):
            v = float(v.replace(",", "."))
            return v * 25.4 if u in ('"', "pulg") else v * 10 if u == "cm" else v
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(mm|cm|\"|pulg)?\s*x\s*(\d+(?:[.,]\d+)?)\s*(mm|cm|\"|pulg)", tl)
        if m:
            u = m.group(4)
            poner("WHEEL_DIAMETER", f"{round(_mm(m.group(1), m.group(2) or u))} mm")
            poner("WHEEL_WIDTH", f"{round(_mm(m.group(3), u))} mm")
        else:
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*(\"|pulg|mm)", tl)
            if m:
                poner("WHEEL_DIAMETER", f"{round(_mm(m.group(1), m.group(2)))} mm")
    poner("SCREW_TYPE", "Autoperforante" if "parker" in tl else None)
    poner("WHEELBARROW_TYPE", "Construcción" if "carretilla" in tl else None)
    poner("PRODUCT_TYPE", "Taladro/Destornillador" if "punta" in tl else None)
    poner("HAND_SAW_TYPE", "Poda curvo" if "poda" in tl else None)
    poner("ORGANIZER_TYPE", "Caja" if "caja" in tl else None)
    m = re.search(r"(\d+(?:,\d+)?)\s*m\b", tl)
    if m:
        poner("LENGTH", f"{m.group(1)} m")
    if re.search(r"^(perfil pg[cu]|montante|solera|perfil omega)", tl):
        poner("CONSTRUCTION_PROFILE_SHAPE", "U")
    poner("TOOL_TYPE", "Máquina de pintar airless" if "airless" in tl else None)
    if "martillo" in tl:
        poner("HAMMER_TYPE", "Bola")   # pedido del usuario para estos martillos
    if "hormigonera" in tl:
        poner("POWER_SUPPLY_TYPE", "Corriente doméstica")   # sin opción a combustión: se carga como eléctrica (a corregir)
    if "pistola para pintar" in tl:
        poner("POWER_SUPPLY_TYPE", "Neumática")
    if re.search(r"espuma|sellador", tl):
        poner("UNIT_WEIGHT", "35 kg")   # valor provisorio indicado por el usuario (a corregir)
    if "arena" in tl or "grancilla" in tl:
        poner("CONSTRUCTION_SAND_YIELD_OF_SALES_UNIT", "0.01 m³")
        poner("SAND_THICKNESS", "Fina" if "fina" in tl else "Gruesa")
        poner("SALES_UNIT", "Bolsa")


res = []
for sku in skus:
    f = fichas.get(sku)
    fila = {"sku": sku}
    try:
        if not f:
            raise RuntimeError("no hay ficha cargada para este SKU")
        # regla: solo se publica lo que existe en la lista de precios de FEMA (de donde salen precio y stock)
        if sync.normalizar_codigo(sku) not in PRECIO_XLS:
            fila["resultado"] = f"EXCLUIDO: no figura en la lista de precios de {PROVEEDOR}"
            log(f"{sku}: {fila['resultado']}")
            res.append(fila)
            continue
        # ¿ya publicado?
        ya = []
        for est in ("active", "paused"):
            ya += ml.get(f"/users/{ml.user_id}/items/search", seller_sku=sku + SUFIJO, status=est).get("results") or []
        if ya:
            r = ml.pedir("GET", f"/items/{ya[0]}/description")
            if r.status_code == 404 or not (r.json().get("plain_text") or "").strip():
                if dry:
                    fila["resultado"] = f"YA EXISTE {ya[0]} — se cargaría la descripción que falta"
                else:
                    rd = ml.pedir("POST", f"/items/{ya[0]}/description", json={"plain_text": texto_ml(fm.descripcion(f))})
                    if rd.status_code not in (200, 201):
                        rd = ml.pedir("PUT", f"/items/{ya[0]}/description", params={"api_version": 2},
                                      json={"plain_text": texto_ml(fm.descripcion(f))})
                    fila["resultado"] = (f"YA EXISTE {ya[0]} — descripción cargada" if rd.status_code in (200, 201)
                                         else f"ERROR: descripción de {ya[0]}: {rd.status_code} {rd.text[:300]}")
                fila["publicacion"] = ya[0]
                log(f"{sku}: {fila['resultado']}")
                res.append(fila)
                continue
            raise RuntimeError(f"ya existe publicación con este SKU: {ya}")
        p = por_sku.get(sync.normalizar_codigo(sku + SUFIJO))
        base = float(((p or {}).get("prices") or {}).get(str(b["price_group_id"])) or 0)
        origen = "BaseLinker"
        if base <= 0:   # no está en BaseLinker: mismo cálculo desde el Excel de FEMA (mayorista × 1,25)
            m = PRECIO_XLS.get(sync.normalizar_codigo(sku))
            base, origen = (round(m * MULT, 2), "Excel FEMA") if m else (0, "")
        if base <= 0:
            raise RuntimeError(f"sin precio: no está en BaseLinker ni en la lista de {PROVEEDOR}")
        precio = round(base * (1 + margen / 100), 2)
        t = titulo(f)
        cat = elegir_categoria(f, t)
        if not cat:
            raise RuntimeError("Mercado Libre no sugirió una categoría de herramientas/construcción/vehículos")
        cat_info = info_cat(cat)
        cat_attrs = [{"id": a.get("id"), "name": a.get("name"), "value_type": a.get("value_type"),
                      "tags": a.get("tags"), "allowed_units": [u.get("id") for u in a.get("allowed_units") or []],
                      "values": [{"id": v.get("id"), "name": v.get("name")} for v in a.get("values") or []]}
                     for a in ml.get(f"/categories/{cat}/attributes")]
        attrs = fm.atributos_para(f, cat_attrs)
        attrs.update({"BRAND": f.get("marca") or PROVEEDOR.title(), "MODEL": f["specs"].get("Modelo") or sku, "SELLER_SKU": sku + SUFIJO,
                      "PART_NUMBER": sku, "MPN": sku})
        if GTIN.get(sku):
            attrs["GTIN"] = GTIN[sku]["gtin"]
            fila["gtin"] = GTIN[sku]["gtin"]
        else:
            attrs["EMPTY_GTIN_REASON"] = "El producto no tiene código registrado"
        iva = sync.a_numero(IVA.get(sync.normalizar_codigo(sku)))
        if iva is not None:
            attrs["VALUE_ADDED_TAX"] = f"{fm.fmt(iva)} %"
        attrs["IMPORT_DUTY"] = "0 %"
        por_nombre = {a["name"].lower(): a for a in cat_attrs}
        extra = {**EXTRA.get("*", {}), **EXTRA.get(sku, {})}
        if "COLOR" in {a["id"] for a in cat_attrs} and not any(k.lower() == "color" for k in extra):
            c_req = any(a["id"] == "COLOR" and (a.get("tags") or {}).get("required") for a in cat_attrs)
            col = color_de(f, t)
            # EXTRAPOWER: el color por defecto (Blanco) solo si ML lo exige; si no, no se inventa
            if not GEN or col != "Blanco" or c_req or re.search(r"\bblanc[oa]\b", (t + " " + f["nombre"]).lower()):
                extra["Color"] = col
        for nom, val in extra.items():
            a = por_nombre.get(nom.strip().lower())
            if not a:
                continue
            opciones = [v["name"] for v in a.get("values") or []]
            elegido = next((o for o in opciones if o.lower() == val.lower()), None) or \
                next((o for o in opciones if val.lower() in o.lower()), None) or val
            attrs[a["id"]] = elegido
        # datos genéricos que algunas categorías exigen
        cont = " ".join(str(v) for k, v in f["specs"].items() if k.lower().startswith("contenido")) + " " + t
        mv = re.search(r"(\d+(?:[.,]\d+)?)\s*ml\b", cont, re.I)
        mg = re.search(r"(\d+(?:[.,]\d+)?)\s*(g|gr|kg)\b", cont, re.I)
        if "UNIT_VOLUME" not in attrs and mv:
            attrs["UNIT_VOLUME"] = f"{mv.group(1).replace(',', '.')} mL"
        if "UNIT_WEIGHT" not in attrs and mg:
            attrs["UNIT_WEIGHT"] = f"{mg.group(1).replace(',', '.')} {'kg' if mg.group(2).lower() == 'kg' else 'g'}"
        attrs.setdefault("PRODUCT_NAME", t)
        if "es set" in por_nombre and not any(k.lower() == "es set" for k in extra):
            attrs.setdefault(por_nombre["es set"]["id"], "No")
        completar_obligatorios(attrs, cat_attrs, f, t)
        ids_cat = {a["id"] for a in cat_attrs}
        atributos = [{"id": k, "value_name": v} for k, v in attrs.items() if k in ids_cat]
        faltan = [a["name"] for a in cat_attrs if (a.get("tags") or {}).get("required")
                  and a["id"] not in attrs and a["id"] not in ("ITEM_CONDITION",)]
        imgs = imagenes(sku)
        if not GEN and not [x for x in imgs if "_ficha" not in x["nombre"].lower()]:
            # producto sin foto propia: imagen genérica de la caja FEMA (+ ficha si existe)
            caja = [x for x in fotos if os.path.splitext(x["nombre"])[0].lower() == "caja_fema"
                    and os.path.splitext(x["nombre"])[1].lower() in EXT]
            imgs = caja[:1] + imgs
        if not imgs:
            raise RuntimeError("no hay fotos para este SKU" + ("" if GEN else " en Drive"))
        fila["iva"] = attrs.get("VALUE_ADDED_TAX")
        fila["origen_precio"] = origen
        fila["opciones_obligatorios"] = " || ".join(
            f"{a['name']}: {', '.join(v['name'] for v in (a.get('values') or [])[:15])}"
            for a in cat_attrs if a["name"] in faltan)
        fila.update({"titulo": t, "categoria": f"{cat} {cat_info.get('name')}", "precio_base": base,
                     "precio": precio, "stock": stock_fijo, "fotos": " | ".join(x["nombre"] for x in imgs),
                     "atributos": len(atributos), "obligatorios_sin_dato": ", ".join(faltan)})
        item = {"title": t, "category_id": cat, "price": precio, "currency_id": "ARS",
                "available_quantity": stock_fijo, "buying_mode": "buy_it_now", "condition": "new",
                "listing_type_id": tipo, "attributes": atributos,
                "sale_terms": [{"id": "WARRANTY_TYPE", "value_name": "Garantía de fábrica"},
                               {"id": "WARRANTY_TIME", "value_name": "6 meses"}],
                "shipping": {"mode": envio, "local_pick_up": False, "free_shipping": False}}
        def agrandar(datos):
            """Mercado Libre pide mínimo 500 px: se amplía la imagen sobre fondo blanco a 800 px."""
            import io as _io
            from PIL import Image
            im = Image.open(_io.BytesIO(datos)).convert("RGB")
            esc = 800 / max(im.size)
            im = im.resize((max(1, round(im.width * esc)), max(1, round(im.height * esc))), Image.LANCZOS)
            lienzo = Image.new("RGB", (max(im.width, 600), max(im.height, 600)), "white")
            lienzo.paste(im, ((lienzo.width - im.width) // 2, (lienzo.height - im.height) // 2))
            out = _io.BytesIO()
            lienzo.save(out, "JPEG", quality=92)
            return out.getvalue()

        def subir(x):
            import time
            if x.get("url"):
                import requests as _rq
                _r = _rq.get(x["url"], timeout=60, headers={"User-Agent": "Mozilla/5.0"})
                if _r.status_code != 200 or len(_r.content) < 2000:
                    log(f"{sku}: no pude bajar {x['url']}: {_r.status_code}")
                    return None
                datos = _r.content
                try:   # ML pide mínimo 500 px: las fotos chicas de la web se agrandan antes de subir
                    import io as _io
                    from PIL import Image
                    _im = Image.open(_io.BytesIO(datos))
                    if max(_im.size) < 500:
                        datos = agrandar(datos)
                    elif (_im.format or "").upper() not in ("JPEG", "PNG"):   # webp de la tienda -> JPEG
                        _b = _io.BytesIO()
                        _im.convert("RGB").save(_b, "JPEG", quality=90)
                        datos = _b.getvalue()
                except Exception:
                    pass
            else:
                datos = descargar(x["id"])
            nombre = x["nombre"]
            if x.get("url"):
                nombre = os.path.splitext(nombre)[0][:40] + ".jpg"
            for intento in range(3):
                up = ml.pedir("POST", "/pictures/items/upload", files={"file": (nombre, datos)})
                if up.status_code in (200, 201):
                    return {"id": up.json()["id"]}
                if "500 p" in up.text and not nombre.endswith("_800.jpg"):
                    datos, nombre = agrandar(datos), os.path.splitext(x["nombre"])[0] + "_800.jpg"
                    continue
                if (up.status_code == 413 or "not compatible" in up.text) and not nombre.endswith("_conv.jpg"):
                    # imagen muy pesada o en un formato que ML no acepta: se reconvierte a JPEG de 1600 px
                    import io as _io
                    from PIL import Image
                    im = Image.open(_io.BytesIO(datos)).convert("RGB")
                    im.thumbnail((1600, 1600))
                    if max(im.size) < 600:
                        datos = agrandar(datos)
                    else:
                        buf = _io.BytesIO()
                        im.save(buf, "JPEG", quality=88)
                        datos = buf.getvalue()
                    nombre = os.path.splitext(x["nombre"])[0] + "_conv.jpg"
                    continue
                time.sleep(3 * (intento + 1))
            log(f"{sku}: no pude subir {x['nombre']}: {up.status_code} {up.text[:200]}")
            return None

        def enviar(ruta):
            r = ml.pedir("POST", ruta, json=item)
            for espera in (30, 60, 120):   # error momentáneo de ML ("Try in a few minutes")
                if r.status_code in (200, 201, 204) or "normalization_unavailable" not in r.text:
                    break
                time.sleep(espera)
                r = ml.pedir("POST", ruta, json=item)
            if r.status_code not in (200, 201, 204) and "family_name" in r.text:
                item["family_name"] = item.pop("title")
                r = ml.pedir("POST", ruta, json=item)
            if r.status_code not in (200, 201, 204) and "minimum of price" in r.text and item["price"] < precio_minimo:
                # la categoría exige un precio mínimo: se sube al precio de resguardo acordado
                item["price"] = precio_minimo
                fila["precio"] = f"{precio_minimo:.2f} (subido al mínimo; calculado {precio})"
                r = ml.pedir("POST", ruta, json=item)
            return r

        if XP:
            # EXTRAPOWER: Mercado Libre baja las fotos directo de la web del proveedor
            # (la web bloquea las descargas desde GitHub después de muchas consultas)
            item["pictures"] = [{"source": x["url"]} for x in imgs]
        if dry:
            # en simulación se sube solo la primera foto (no queda asociada a ninguna publicación)
            item["pictures"] = item["pictures"][:1] if XP else [x for x in [subir(imgs[0])] if x]
            r = enviar("/items/validate")
            if sin_gtin(r):
                fila["resultado"] = DESCARTE_GTIN
            elif r.status_code in (200, 204):
                fila["resultado"] = "VALIDADO OK"
            else:
                causas = (r.json().get("cause") or []) if r.headers.get("content-type", "").startswith("application/json") else []
                errores = [c.get("message") for c in causas if c.get("type") == "error"]
                avisos = [c.get("message") for c in causas if c.get("type") != "error"]
                fila["resultado"] = ("VALIDACIÓN CON ERRORES: " + " / ".join(errores) if errores else "VALIDADO OK") + \
                    (" | AVISOS: " + " / ".join(avisos) if avisos else "")
        else:
            if not XP:
                item["pictures"] = [x for x in (subir(i) for i in imgs) if x]
            r = enviar("/items")
            if sin_gtin(r):
                fila["resultado"] = DESCARTE_GTIN
                log(f"{sku}: {fila['resultado']}")
                res.append(fila)
                continue
            if r.status_code not in (200, 201):
                raise RuntimeError(f"Mercado Libre rechazó la publicación: {r.status_code} {r.text[:600]}")
            nuevo = r.json()
            iid = nuevo["id"]
            ml.put(f"/items/{iid}", {"status": "paused"})
            rd = ml.pedir("POST", f"/items/{iid}/description", json={"plain_text": texto_ml(fm.descripcion(f))})
            fila.update({"publicacion": iid, "link": nuevo.get("permalink"),
                         "resultado": "CREADA (pausada)" + ("" if rd.status_code in (200, 201) else
                                                            f" — descripción falló {rd.status_code}: {rd.text[:200]}")})
    except Exception as e:
        fila["resultado"] = f"ERROR: {e}"
    log(f"{sku}: {fila.get('resultado')}")
    res.append(fila)

os.makedirs("salida", exist_ok=True)
cols = ["sku", "resultado", "publicacion", "link", "titulo", "categoria", "gtin", "origen_precio", "precio_base", "precio", "stock",
        "iva", "fotos", "atributos", "obligatorios_sin_dato", "opciones_obligatorios"]
with open("salida/ml-publicar-resultado.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=cols, delimiter=";", extrasaction="ignore")
    w.writeheader()
    w.writerows(res)
for r in res:
    print(json.dumps(r, ensure_ascii=False))
sys.exit(1 if any(str(r.get("resultado", "")).startswith("ERROR") for r in res) else 0)
