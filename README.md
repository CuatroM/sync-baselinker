# Sincronización Proveedor → BaseLinker

**Configuración actual:** lista de precios FEMA del panel "Administra ML" (Utilidades → Info Sellers → FEMA → "Precios al…") → BaseLinker, inventario **INVENTARIO COMPLETO FEMA 2026 MATIAS**. Se actualizan solo **PRECIO MAYORISTA** (grupo "GRUPO DE PRECIO COMPLETO FEMA 2026 MATIAS") y **STOCK** (depósito "ALMACEN COMPLETO FEMA 2026 MATIAS"), vinculando por **SKU**. Corre cada 4 hs.

**Ver qué cambió:** rama [`reportes`](../../tree/reportes) → `ultima-ejecucion.log` y los CSV `cambios-*` / `sin-vincular-*`.

**Mercado Libre (cuenta ABASTOTOOLS):** los procesos "Mercado Libre - …" piden un código de autorización nuevo en cada corrida. Abrí este link (con la sesión de Mercado Libre iniciada), copiá la dirección completa a la que te redirige (`…?code=TG-…`) y pegala enseguida en *Run workflow*. Cada código vale ~10 minutos y un solo uso.

https://auth.mercadolibre.com.ar/authorization?response_type=code&client_id=2635478937867404&redirect_uri=https://github.com/CuatroM/sync-baselinker

**Diagnóstico:** el proceso "Diagnóstico del sitio del proveedor" (Actions → Run workflow) prueba el login y la descarga sin tocar BaseLinker; con `solo_baselinker = 1` lista catálogos, grupos de precio y depósitos.

Descarga la lista de precios y stock del proveedor (Excel o CSV) cada 4 horas y actualiza BaseLinker por API. Solo envía los productos que cambiaron.

## Puesta en marcha

### 1. Crear el repositorio
1. En GitHub: **New repository** → nombre `sync-baselinker` → **Private**.
2. Subí todos los archivos de esta carpeta, incluida la carpeta oculta `.github/workflows/`.
   - Desde la web: **Add file → Upload files** y arrastrá la carpeta completa.

### 2. Cargar los secretos
En el repositorio: **Settings → Secrets and variables → Actions → New repository secret**.

| Nombre | Valor |
|---|---|
| `BL_TOKEN` | Token de API de BaseLinker (*Mi cuenta → API*) |
| `PROVEEDOR_USER` | Usuario del sitio del proveedor (solo si pide login) |
| `PROVEEDOR_PASS` | Contraseña del sitio del proveedor (solo si pide login) |

### 3. Completar `config.yaml`
- `url_lista`: link de descarga del Excel.
- `columnas`: títulos exactos de las columnas de código, precio y stock.
- `baselinker`: `inventory_id`, `price_group_id` y `warehouse_id`. Se ven en BaseLinker, o se obtienen con los métodos `getInventories`, `getInventoryPriceGroups` y `getInventoryWarehouses` de la API.
- `reglas`: recargo de precio, redondeo y topes de stock.

### 4. Probar en modo simulación
`dry_run: true` viene activado: el proceso descarga, compara y arma el reporte, pero **no escribe nada** en BaseLinker.

1. Pestaña **Actions → Sincronizar precio y stock con BaseLinker → Run workflow**.
2. Al terminar, bajá el artefacto **reportes-N**:
   - `cambios-*.csv`: qué se modificaría (antes / ahora).
   - `sin-vincular-*.csv`: códigos del proveedor que no existen en BaseLinker.
3. Si todo está bien, cambiá `dry_run: false` en `config.yaml`.

Desde ese momento corre solo a las 00, 04, 08, 12, 16 y 20 hs (Argentina). La hora real puede demorarse unos minutos.

## Protecciones incluidas
- Si la lista viene vacía o con menos de `minimo_productos_lista` productos, **no actualiza nada**. Así no se pone el stock en cero por un error del proveedor.
- Si el proveedor devuelve una página web en vez del archivo (login caído, link cambiado), se detiene con error.
- Respeta el límite de 100 llamadas por minuto de BaseLinker y reintenta ante cortes.
- Nunca corren dos sincronizaciones a la vez.
- Si falla o hay advertencias, la ejecución queda en rojo y GitHub avisa por mail.

## Avisos
- Usá un repositorio **privado**. En los públicos, GitHub desactiva las tareas programadas después de 60 días sin actividad.
- El stock se escribe en el depósito propio de BaseLinker (`bl_...`). No se puede escribir en depósitos externos de tiendas o mayoristas.
