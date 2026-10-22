"""
Convierte una ficha FEMA (datos/fichas_fema.json) en:
  - atributos de Mercado Libre (según los que admite la categoría de cada publicación)
  - descripción en texto plano con el formato acordado.
"""
import re

# ------------------------------------------------------------------ utilidades

def nums(v):
    """Todos los números de un valor: '6 / 12' -> [6, 12]; '1.850 Watts' -> [1850]; '0,9 m' -> [0.9]."""
    if isinstance(v, (int, float)):
        return [float(v)]
    s = str(v)
    out = []
    for m in re.findall(r"\d+(?:[.,]\d+)*", s):
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", m):        # 1.850 -> 1850
            m = m.replace(".", "")
        out.append(float(m.replace(",", ".")))
    return out


def nmax(v):
    n = nums(v)
    return max(n) if n else None


def nfirst(v):
    n = nums(v)
    return n[0] if n else None


def fmt(x):
    """Número limpio: 1000.0 -> '1000', 0.133 -> '0.133'."""
    return str(int(x)) if float(x).is_integer() else str(round(x, 3))


def g(specs, *claves):
    for c in claves:
        if c in specs and specs[c] not in (None, ""):
            return specs[c]
    return None


def potencia_w(specs):
    v = g(specs, "Potencia (W)")
    if v is not None:
        return nmax(v)
    v = g(specs, "Potencia del motor", "Potencia")
    if v is not None:
        s = str(v).lower()
        n = nums(s)
        if not n:
            return None
        if "kw" in s:
            total = n[-1] * (n[0] if "x" in s and len(n) > 1 and s.strip().startswith(str(int(n[0]))) else 1)
            if re.match(r"^\s*2\s*x", s):
                total = n[-1] * 2
            elif re.search(r"x\s*2", s):
                total = n[0] * 2
            else:
                total = n[0]
            return round(total * 1000)
        if "hp" in s:
            return round(n[0] * 745.7)
        return n[0]
    v = g(specs, "Potencia del motor (HP)")
    if v is not None:
        return round(nfirst(v) * 745.7)
    v = g(specs, "Motor")
    if v is not None and "hp" in str(v).lower():
        return round(nfirst(v) * 745.7)
    return None


def voltaje(specs):
    v = g(specs, "Voltaje (V)")
    if v is None:
        for c in ("Motor", "Alimentación", "Potencia"):
            m = re.search(r"(\d{2,3})\s*V", str(specs.get(c, "")))
            if m:
                return int(m.group(1))
        return None
    return int(nfirst(v))


def peso_kg(specs):
    for c in ("Peso (kg)", "Peso bruto / neto (kg)", "Peso neto / bruto (kg)", "Peso bruto (kg)"):
        v = specs.get(c)
        if v is None:
            continue
        n = nums(v)
        if not n:
            continue
        if c == "Peso bruto / neto (kg)" and len(n) > 1:
            return n[1]
        return n[0]
    return None


def medidas(v):
    """'53 x 29 x 39' -> (53, 29, 39)"""
    n = nums(v)
    return tuple(n[:3]) if len(n) >= 3 else None


def capacidad_kg(specs):
    v = g(specs, "Capacidad máxima (kg)", "Capacidad (kg)", "Capacidad de arrastre (kg)",
          "Fuerza máxima de arrastre (kg)")
    return nmax(v) if v is not None else None


# ------------------------------------------------------------------ atributos candidatos

def candidatos(f):
    """Diccionario ID_ATRIBUTO -> value_name para todo lo que la ficha permite completar."""
    s, fam = f["specs"], f["familia"]
    a = {}
    V = voltaje(s)
    if V:
        a["VOLTAGE"] = f"{V}V"
        tipo = "Batería" if V <= 24 else ("Corriente doméstica" if V <= 230 else "Energía industrial")
        a["POWER_SUPPLY_TYPES"] = tipo
        a["POWER_SUPPLY_TYPE"] = tipo
    W = potencia_w(s)
    if W:
        a["POWER"] = f"{fmt(W)} W"
    cap = capacidad_kg(s)
    if cap and "Aspiradora" not in fam and "Accesorio" not in fam:
        for k in ("MAX_WEIGHT_SUPPORTED", "WEIGHT_CAPACITY", "MAX_SUPPORTED_WEIGHT"):
            a[k] = f"{fmt(cap)} kg"
        a["MAX_WEIGHT_CAPACITY"] = f"{fmt(cap / 1000)} t"
    p = peso_kg(s)
    if p:
        a["WEIGHT"] = f"{fmt(p)} kg"
        a["WINCH_WEIGHT"] = f"{fmt(p)} kg"
    alt = g(s, "Altura de elevación (m)") if "Aspiradora" not in fam else None
    if alt is not None:
        a["MAX_ROPE_HEIGHT"] = f"{fmt(nmax(alt))} m"
        a["MAX_LIFTING_HEIGHT"] = f"{fmt(nmax(alt))} m"
        a["MAX_CHAIN_LENGTH"] = f"{fmt(nmax(alt))} m"
    vel = g(s, "Velocidad (m/min)", "Velocidad de izado (m/min)")
    if vel is not None and "Malacate" not in fam:
        a["ELEVATION_SPEED"] = f"{fmt(round(nmax(vel) / 60, 3))} m/s"
    if s.get("Control remoto") == "Sí":
        a["WITH_REMOTE_CONTROL"] = "Sí"

    if fam == "Aparejo Manual a Cadena":
        a["MANUAL_HOISTS_TYPE"] = "De cadena"
    if fam.startswith("Carro de Traslación"):
        viga = g(s, "Ancho de viga aplicable (mm)", "Ancho de viga (mm)")
        if viga:
            n = nums(viga)
            a["MIN_OPENING_CAPACITY"], a["MAX_OPENING_CAPACITY"] = f"{fmt(n[0])} mm", f"{fmt(n[-1])} mm"
    if fam == "Patín de Carga":
        a["LOAD_MOVING_SKATE_TYPE"] = "Tortuga"
        a["WHEELS_NUMBER"] = fmt(nfirst(s["Ruedas"]))
        a["IS_SWIVEL_PRODUCT"] = "Sí" if "360" in str(s.get("Giro", "")) else None
        if "Largo con manija (m)" in s:
            a["WITH_HANDLE"] = "Sí"
        if "Base (cm)" in s:
            n = nums(s["Base (cm)"])
            a["WIDTH"], a["LENGTH"] = f"{fmt(n[0])} cm", f"{fmt(n[1])} cm"
        if "Altura de base a piso (cm)" in s:
            a["HEIGHT"] = f"{fmt(s['Altura de base a piso (cm)'])} cm"
    if fam in ("Malacate Manual", "Aparejo / Malacate Eléctrico 12 V"):
        cap = capacidad_kg(s)
        a["TRAWLING_CAPACITY"] = f"{fmt(cap)} kg"
        cable = g(s, "Largo de cable (m)", "Cinta / cable")
        if cable:
            a["CABLE_LENGTH"] = f"{fmt(nfirst(cable))} m"
        m = medidas(s.get("Medidas", "")) if s.get("Medidas") else None
        if m:
            a["WINCH_LENGTH"], a["WINCH_WIDTH"], a["WINCH_HEIGHT"] = (f"{fmt(x)} cm" for x in m)
        if s.get("Dimensiones (mm)"):
            m = medidas(s["Dimensiones (mm)"])
            a["WINCH_LENGTH"], a["WINCH_WIDTH"], a["WINCH_HEIGHT"] = (f"{fmt(x)} mm" for x in m)
    if fam == "Aspiradora Polvo y Líquido":
        a["VOLUME_CAPACITY"] = f"{fmt(s['Capacidad del tanque (L)'])} L"
        a["FREQUENCY"] = "50 Hz"
        a["SOUND_PRESSURE_LEVEL"] = f"{fmt(nfirst(s['Nivel de ruido (dB)']))} dBA"
        a["IS_SUITABLE_FOR_LIQUID"] = "Sí"
        a["IS_INDUSTRIAL"] = "Sí"
        a["IS_WIRELESS"] = "No"
        a["VACUUM_AND_STEAM_CLEANER_TYPES"] = "Tambor"
        if "sopladora" in str(s.get("Observaciones", "")).lower():
            a["ADDITIONAL_FUNCTIONS"] = "Sopladora"
        n = nums(s["Peso bruto / neto (kg)"])
        a["WEIGHT"] = f"{fmt(n[-1])} kg"
    if fam == "Arrancador / Cargador de Batería":
        a["LOAD_VOLTAGE"] = "12V/24V" if "24" in str(s.get("Voltaje de carga", "")) else "12V"
        c = g(s, "Corriente de carga promedio (A)")
        if c:
            a["OUTPUT_CURRENT"] = f"{fmt(nmax(c))} A"
        a.pop("POWER", None)
    if fam == "Sensitiva / Cortadora de Metales":
        a["MAX_RPM"] = f"{fmt(s['Velocidad (RPM)'])} rpm"
        a["DISC_DIAMETER"] = f"{fmt(s['Diámetro del disco (mm)'])} mm"
        if s.get("Capacidad de corte (mm)"):
            a["MAX_CUTTING_DEPTH"] = f"{fmt(s['Capacidad de corte (mm)'])} mm"
            a["CUTTING_ANGLES"] = s["Ángulos de corte"]
        m = medidas(s["Medidas (cm)"])
        a["LENGTH"], a["WIDTH"], a["HEIGHT"] = (f"{fmt(x)} cm" for x in m)
        a["WITH_SPINDLE_LOCK_BUTTON"] = "Sí"
        a["IS_WIRELESS"] = "No"
        if V and V > 230:
            a.pop("POWER_SUPPLY_TYPES", None)
    if fam == "Amoladora de Banco":
        a["POWER_GRINDER_TYPE"] = "De banco"
        a["DISC_DIAMETER"] = f"{fmt(s['Diámetro de piedra (mm)'])} mm"
        a["MAX_ROTATION_SPEED"] = f"{fmt(s['Velocidad (RPM)'])} rpm"
        a["IS_MINI_POWER_GRINDER"] = "No"
    if fam == "Balanceadora de Cubiertas":
        a["VOLTAGE"], a["FREQUENCY"], a["ELECTRICAL_PHASE"] = "220V", "50 Hz", "Monofásica"
        a["BALANCING_ACCURACY"] = "1 g"
        a["BALANCING_SPEED"] = f"{fmt(s['Velocidad de balanceo (RPM)'])} rpm"
        a["MAX_WHEEL_DIAMETER"] = '24 "'
        a["MAX_SUPPORTED_WEIGHT"] = "65 kg"
        a["WIDTH"], a["DEPTH"], a["HEIGHT"] = "960 mm", "76 cm", "1160 mm"
        a["WITH_DISPLAY"] = "Sí"
        a["POWER"] = "370 W"
        a["WEIGHT"] = f"{fmt(nums(s['Peso neto / bruto (kg)'])[0])} kg"
    if fam == "Elevador de Autos":
        a["POSTS_NUMBER"] = fmt(s["Columnas"]) if "Columnas" in s else None
        a["MAX_LIFTING_HEIGHT"] = f"{fmt(nfirst(s['Altura máxima de elevación (mm)']))} mm"
        for k, c in (("AVERAGE_LIFTING_TIME", "Tiempo de subida (s)"),
                     ("AVERAGE_LOWERING_TIME", "Tiempo de bajada (s)")):
            if isinstance(s.get(c), (int, float)):
                a[k] = f"{fmt(s[c])} s"
        if fam and "Tijera" in f["nombre"]:
            a["POWER"] = "2200 W"
    if fam == "Desarmadora de Cubiertas":
        a["VOLTAGE"], a["POWER"] = "220V", "370 W"
        a["LENGTH"], a["WIDTH"], a["HEIGHT"] = "113 cm", "90 cm", "103 cm"
        a["MAX_WHEEL_DIAMETER"] = '45 "'
        a["WHEEL_WIDTH"] = "406 mm"
        a["WORKING_PRESSURE"] = "1000 kPa"
        a["MIN_CLAMPING_DIAMETER"], a["MAX_CLAMPING_DIAMETER"] = '12 "', '26 "'
    if fam == "Soporte de Chasis":
        if "Altura mínima (mm)" in s:
            a["MIN_HEIGHT"], a["MAX_HEIGHT"] = f"{s['Altura mínima (mm)']} mm", f"{s['Altura máxima (mm)']} mm"
            a["IS_ADJUSTABLE"] = "Sí"
    if fam == "Criquet Carro":
        a["VEHICLE_JACK_FORMAT"] = "Carrito perfil bajo" if s.get("Perfil") == "Super bajo" else "Carrito"
        a["MIN_HEIGHT_LIFTED"] = f"{s['Altura mínima (mm)']} mm"
        a["MAX_HEIGHT_LIFTED"] = f"{s['Altura máxima (mm)']} mm"
    if fam == "Prensa Hidráulica":
        a["PRESSING_FORCE"] = f"{fmt(capacidad_kg(s) / 1000)} t"
    return {k: v for k, v in a.items() if v}


def unidad(valor):
    m = re.match(r"^[\d.]+\s*(.+)$", str(valor).strip())
    return m.group(1).strip() if m else None


def pesos_validos(f):
    out = []
    for c, v in f["specs"].items():
        if c.lower().startswith("peso"):
            out += [f"{fmt(x)} kg" for x in nums(v)]
    return out


def atributos_para(f, cat_attrs):
    """Filtra los candidatos a los atributos que admite la categoría (y unidades permitidas)."""
    por_id = {a["id"]: a for a in cat_attrs}
    out = {}
    for k, v in candidatos(f).items():
        a = por_id.get(k)
        if not a:
            continue
        t = a.get("tags") or {}
        if t.get("read_only") or t.get("hidden") or t.get("fixed"):
            continue
        if a["value_type"] == "number_unit":
            u = unidad(v)
            if a["allowed_units"] and u not in a["allowed_units"]:
                continue
        if a["value_type"] in ("boolean", "list") and a["values"]:
            if v not in {x["name"] for x in a["values"]}:
                continue
        out[k] = v
    return out


FACTORES = {"kg": 1, "t": 1000, "g": 0.001, "lb": 0.4536, "mm": 1, "cm": 10, "m": 1000, '"': 25.4,
            "w": 1, "kw": 1000, "hp": 745.7, "m/s": 1, "s": 1, "rpm": 1, "l": 1, "a": 1, "dba": 1,
            "kpa": 1, "bar": 100, "psi": 6.895}


def _base(v):
    m = re.match(r"^\s*(-?[\d.,]+)\s*(.*)$", str(v))
    if not m:
        return None
    try:
        n = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    u = m.group(2).strip().lower()
    if u in FACTORES:
        return n * FACTORES[u], ("long" if u in ("mm", "cm", "m", '"') else u if u not in ("t", "g", "lb") else "kg")
    return None


def mismo(a, b, tolerancia=0.03):
    """Compara valores de ML: ignora espacios/mayúsculas y unidades equivalentes (170 cm = 1700 mm)."""
    if a is None or b is None:
        return False
    n = lambda x: re.sub(r"\s+", "", str(x)).lower().replace(",", ".").replace(".0", "")
    if n(a) == n(b):
        return True
    x, y = _base(a), _base(b)
    if x and y:
        if x[1] in ("kw", "hp"): x = (x[0], "w")
        if y[1] in ("kw", "hp"): y = (y[0], "w")
        if x[1] == y[1] and max(abs(x[0]), abs(y[0])) > 0:
            return abs(x[0] - y[0]) / max(abs(x[0]), abs(y[0])) <= tolerancia
    return False


# ------------------------------------------------------------------ descripción

USOS = {
    "Aparejo / Malacate Eléctrico 12 V": ("Malacate eléctrico de arrastre a 12 V para remolcar, rescatar y arrastrar cargas pesadas. Se instala en camionetas, trailers y vehículos 4x4.",
                                          "talleres, gomerías y auxilio mecánico, campo y agro, náutica, offroad 4x4, construcción y servicios de remolque"),
    "Aparejo Manual a Cadena": ("Aparejo manual a cadena para elevar y posicionar cargas pesadas con precisión y sin necesidad de energía eléctrica.",
                                "talleres mecánicos, metalúrgicas, construcción, depósitos y logística, minería, agro y mantenimiento industrial"),
    "Carro de Traslación Manual": ("Carro de traslación para desplazar aparejos y cargas a lo largo de vigas, ideal para armar puentes grúa y monorrieles.",
                                   "talleres, metalúrgicas, depósitos, fábricas, construcción y mantenimiento industrial"),
    "Imán Elevador": ("Imán elevador permanente para levantar y trasladar planchas, chapas y piezas de acero de forma rápida y segura.",
                      "metalúrgicas, herrerías, talleres de corte y plegado, depósitos de chapa y fábricas"),
    "Grampa de Sujeción": ("Grampa de sujeción para izar y trasladar chapas y placas de acero en forma segura junto con su aparejo.",
                           "metalúrgicas, herrerías, astilleros, depósitos de chapa y construcción"),
    "Patín de Carga": ("Patín de carga tipo tortuga para mover máquinas, equipos y cargas pesadas con mínimo esfuerzo.",
                       "fábricas, mudanzas industriales, depósitos y logística, talleres, montajes industriales y mantenimiento"),
    "Aparejo Eléctrico 220 V Doble Gancho": ("Aparejo eléctrico monofásico para elevar cargas con seguridad y sin esfuerzo, con control remoto y freno automático.",
                                             "talleres mecánicos, metalúrgicas, carpinterías, depósitos, construcción, logística y mantenimiento"),
    "Aparejo Eléctrico a Cadena 380 V": ("Aparejo eléctrico a cadena trifásico de uso industrial para elevación continua de cargas pesadas.",
                                         "industrias, metalúrgicas, talleres pesados, puentes grúa, depósitos, minería y construcción"),
    "Aparejo Eléctrico a Cadena 380 V con Carro": ("Aparejo eléctrico a cadena trifásico con carro de traslación motorizado para elevar y trasladar cargas pesadas sobre viga.",
                                                   "industrias, metalúrgicas, puentes grúa, talleres pesados, depósitos, minería y construcción"),
    "Aparejo Eléctrico a Cable 380 V con Carro": ("Aparejo eléctrico a cable trifásico con carro de traslación para trabajo industrial intensivo.",
                                                  "industrias, metalúrgicas, puentes grúa, depósitos, minería y construcción"),
    "Carro de Traslación Eléctrico": ("Carro de traslación eléctrico para desplazar aparejos a lo largo de la viga sin esfuerzo.",
                                      "talleres, metalúrgicas, depósitos, fábricas y mantenimiento industrial"),
    "Soporte Giratorio para Aparejo": ("Soporte giratorio para montar aparejos eléctricos en columnas o paredes y ampliar el área de trabajo.",
                                       "talleres, carpinterías, construcción, depósitos y obras"),
    "Malacate Manual": ("Malacate manual de arrastre para tirar, tensar y remolcar cargas de forma simple y segura.",
                        "náutica, campo y agro, talleres, construcción, auxilio y transporte"),
    "Aspiradora Polvo y Líquido": ("Aspiradora industrial para polvo y líquido con tanque de acero inoxidable, robusta y fácil de vaciar.",
                                   "talleres, lavaderos de autos, carpinterías, obras y construcción, comercios, industrias y limpieza profesional"),
    "Accesorio para Aspiradora": ("Repuesto original para aspiradoras FEMA.",
                                  "talleres, lavaderos, obras, comercios y limpieza profesional"),
    "Arrancador / Cargador de Batería": ("Equipo para cargar baterías y asistir el arranque de vehículos en forma segura.",
                                         "talleres mecánicos, auxilio, gomerías, flotas, transportistas, agro y uso particular"),
    "Sensitiva / Cortadora de Metales": ("Sensitiva cortadora de metales para cortes rectos y en ángulo de perfiles, caños y hierros.",
                                         "herrerías, metalúrgicas, talleres, construcción, aberturas de aluminio e instalaciones"),
    "Amoladora de Banco": ("Amoladora de banco para afilar, desbastar y pulir herramientas y piezas metálicas.",
                           "talleres mecánicos, herrerías, carpinterías, metalúrgicas, agro y mantenimiento"),
    "Balanceadora de Cubiertas": ("Balanceadora de ruedas digital DUCA para un balanceo rápido y preciso.",
                                  "gomerías, talleres mecánicos, lubricentros, concesionarias y flotas"),
    "Elevador de Autos": ("Elevador de vehículos para trabajar cómodo y seguro debajo del auto.",
                          "talleres mecánicos, lubricentros, gomerías, concesionarias, flotas y servicios de mantenimiento"),
    "Desarmadora de Cubiertas": ("Desarmadora de neumáticos para montar y desmontar cubiertas de forma rápida y sin dañar las llantas.",
                                 "gomerías, talleres mecánicos, lubricentros y concesionarias"),
    "Soporte de Chasis": ("Soporte de chasis para sostener vehículos con seguridad durante el trabajo.",
                          "talleres mecánicos, gomerías, lubricentros y uso particular"),
    "Criquet Carro": ("Criquet carro hidráulico para levantar vehículos de forma rápida y segura.",
                      "talleres mecánicos, gomerías, lubricentros, auxilio y uso particular"),
    "Prensa Hidráulica": ("Prensa hidráulica de taller para extraer y colocar rodamientos, bujes, pernos y enderezar piezas.",
                          "talleres mecánicos, metalúrgicas, tornerías, agro y mantenimiento industrial"),
    "Guinche Pluma": ("Guinche pluma hidráulico plegable para levantar motores y cargas pesadas en el taller.",
                      "talleres mecánicos, rectificadoras, metalúrgicas, agro y mantenimiento"),
}

GANCHOS = [
    "Producto original FEMA, robusto y pensado para trabajo exigente.",
    "Calidad profesional al mejor precio: llevalo hoy y trabajá con tranquilidad.",
]


def fmt_valor(v):
    if isinstance(v, float):
        v = fmt(v)
    return str(v).replace(".", ",") if re.fullmatch(r"\d+\.\d+", str(v)) else str(v)


RUBROS_FAM = {
    "Abrasivos": "talleres metalúrgicos, herrerías, carpinterías, chapa y pintura, construcción, mantenimiento industrial y uso profesional",
    "Adhesivos y Selladores": "construcción, mantenimiento, talleres mecánicos, carpinterías, plomería, industria y hogar",
}


def descripcion_extrapower(f):
    """Descripción para productos de EXTRAPOWER (sin textos de FEMA)."""
    marca = f.get("marca") or ""
    original = f" {marca}" if marca and marca != "Genérica" else ""
    uso = f.get("uso_pdf") or f"{f.get('titulo') or f['nombre']}, producto{original} de calidad para uso profesional y hogar."
    lineas = [f["nombre"].upper(), "", uso, "", "CARACTERÍSTICAS"]
    for k, v in f["specs"].items():
        if k == "Marca" and not original:
            continue
        lineas.append(f"• {k}: {fmt_valor(v)}")
    for n in f.get("notas") or []:
        lineas += ["", n]
    if f.get("proveedor") == "JYA":
        lineas += ["", "¿POR QUÉ COMPRARNOS?",
                   "Producto nuevo" + (f" y original{original}" if original else "") +
                   ". Despachamos rápido y te atendemos antes y después de la compra.",
                   "", "¡Consultanos antes de comprar, respondemos rápido!"]
        return "\n".join(lineas)
    lineas += ["", "¿POR QUÉ COMPRARNOS?",
               "Somos una ferretería con más de 70 años en el rubro. Producto nuevo"
               + (f" y original{original}" if original else "") + " con 6 MESES DE GARANTÍA y atención personalizada "
               "antes y después de la compra.",
               "", "¡Consultanos antes de comprar, respondemos rápido!"]
    return "\n".join(lineas)


def descripcion(f):
    if f.get("proveedor") in ("EXTRAPOWER", "JYA"):
        return descripcion_extrapower(f)
    uso, rubros = USOS.get(f["familia"], ("", ""))
    if not uso:
        uso = f.get("uso_pdf") or f"{f.get('titulo') or f['nombre']}, producto original {f.get('marca') or 'FEMA'} de calidad profesional."
        rubros = f.get("rubros") or RUBROS_FAM.get(f["familia"], "talleres, industria, construcción y uso profesional")
    lineas = [f["nombre"].upper(), "", uso, f"Ideal para: {rubros}.", "", "CARACTERÍSTICAS TÉCNICAS"]
    for k, v in f["specs"].items():
        m = re.match(r"^(.*?)\s*\(([^)]+)\)$", k)
        if m and not re.search(r"[A-Za-zµ°]", str(v)):
            etiqueta, u = m.group(1), m.group(2)
            lineas.append(f"• {etiqueta}: {fmt_valor(v)} {u}")
        else:
            lineas.append(f"• {k}: {fmt_valor(v)}")
    for n in f.get("notas") or []:
        lineas.append(f"• {n}")
    marca = f.get("marca") or "FEMA"
    gancho = GANCHOS[sum(map(ord, f["sku"])) % len(GANCHOS)].replace("Producto original FEMA", f"Producto original {marca}")
    lineas += ["", "¿POR QUÉ COMPRARNOS?",
               "Somos distribuidores oficiales FEMA, con más de 70 años en el rubro. "
               f"Producto original {marca} con 6 MESES DE GARANTÍA y atención personalizada antes y después de la compra.",
               gancho,
               "", "¡Consultanos antes de comprar, respondemos rápido!"]
    return "\n".join(lineas)
