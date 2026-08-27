# Extractor de facturas -> insumos para APUs (prototipo)

## Instalación
```
pip install pdfplumber rapidfuzz
```

## Uso

**Interfaz visual (recomendado):**
```
streamlit run app.py
```
Se abre en el navegador. Tiene 3 pestañas:
- **Cargar facturas**: arrastras PDF, ZIP DIAN o XML, el sistema los procesa y te muestra cada ítem
  con su sugerencia de normalización. Si el score es medio (80-89), te deja elegir con un
  radio button "usar sugerencia" o "es un insumo nuevo". Nada se guarda hasta que le das
  a "Guardar todo en la base de datos".
- **Diccionario de insumos**: tabla de todos los insumos normalizados, su categoría,
  precio promedio y de qué proveedores viene.
 - **Gasto mensual**: gráfico de barras del gasto por mes, por proveedor/proyecto, y por
  categoría de insumo (material, servicio, etc.).

**Control plane y atlas visual:**
```bash
uv run --python .venv/Scripts/python.exe uvicorn control_plane:app --reload --port 8767
```
Luego abre `http://127.0.0.1:8767/`. SOMA muestra primero el login administrativo;
después del acceso aparecen el control room, la carga de PDFs, el índice RAG,
el asesor y la descarga del dashboard Excel. Los módulos operativos y financieros
no se sirven sin una sesión Bearer válida.

La página comercial pública está en `http://127.0.0.1:8767/ventas` y contiene el
asesor arquitectónico de SOMA sin exponer el contexto financiero de las facturas.

La pestaña **Administración** protege el resumen financiero y la descarga del Excel.
En desarrollo local usa `ADMON` / `1234`; cambia `ADMIN_USER` y `ADMIN_PASSWORD` en
`.env` antes de exponer el servicio fuera de localhost.

Para logs detallados y trazabilidad por request:
```powershell
$env:ATLAS_DEBUG="1"
$env:ATLAS_VERBOSE="1"
uv run --python .venv/Scripts/python.exe uvicorn control_plane:app --reload --port 8767 --log-level debug
```
Cada request incluye `X-Request-ID`, método, ruta, estado, duración y si recibió
autorización. El endpoint `/api/debug/state` solo responde cuando `ATLAS_DEBUG=1`
y con una sesión administrativa válida. El login limita a cinco intentos por
minuto por cliente.

**Por consola (alternativa, sin interfaz):**

Modo interactivo (uso real — te pregunta antes de fusionar insumos parecidos):
```
python procesar.py ruta/a/tus/facturas/*.pdf
```

Modo automático (para pruebas rápidas, sin confirmar nada por consola):
```
python procesar.py --auto ruta/a/tus/facturas/*.pdf
```

Ver el diccionario de insumos y sus precios históricos:
```
python reporte.py
```

Ver el gasto acumulado mes a mes:
```
python reporte_mensual.py
```

Generar dashboard financiero Excel:
```
uv run --python .venv/Scripts/python.exe python reporte_mensual.py --excel
```

El libro incluye `Dashboard`, `Facturas`, `Items`, `Mensual`, `Mensual_Cat`,
`Proveedores`, `Categorias`, `Insumos`, `Precios` y `Calidad`.

## RAG de facturas

SQLite es la fuente de verdad para cifras y Chroma es el indice de recuperacion
semantica. Para una prueba local sin credenciales:
```
uv run --python .venv/Scripts/python.exe python rag.py --provider local --rebuild
uv run --python .venv/Scripts/python.exe python rag.py --provider local --query "¿Qué proveedor tiene mayor gasto?"
```

El modo `auto` del control room reconstruye Chroma automáticamente después de
guardar facturas. La consulta RAG recupera documentos desde Chroma, pero SQLite
sigue siendo la fuente de verdad para cifras.

Para probar Google Gemini Embedding 2, define `GEMINI_API_KEY` y usa:
```
uv run --python .venv/Scripts/python.exe python rag.py --provider google --model gemini-embedding-2-preview --rebuild
```

El modelo es multimodal, pero para facturas se indexa el texto estructurado
extraído del PDF junto con los items y sus valores. Esto evita usar embeddings
como fuente de cifras exactas.

## OpenCode Go / DeepSeek

OpenCode Go usa un endpoint OpenAI-compatible y ofrece `deepseek-v4-pro` y
`deepseek-v4-flash`. La clave configurada dentro de OpenCode se guarda en
`%USERPROFILE%/.local/share/opencode/auth.json`, pero la aplicación no la lee
automáticamente. Para el chat del aplicativo, copia la clave en `.env`:
```
OPENCODE_GO_API_KEY=tu_clave_de_opencode_go
OPENCODE_GO_BASE_URL=https://opencode.ai/zen/go/v1
OPENCODE_GO_MODEL=deepseek-v4-pro
```

No uses `GEMINI_API_KEY` para Go: esa variable pertenece a Google Gemini.

## Frontend React

El frontend nuevo vive en `frontend/` y se compila con Vite:
```
cd frontend
npm install
npm run dev
```
También puede servirse compilado por FastAPI desde `http://127.0.0.1:8766/`.
El panel operativo queda en la misma SPA y el backend conserva `/docs` para la API.

## Estado actual

 - Parsers dedicados para formatos PDF de proveedores conocidos y extractor genérico UBL/XML DIAN
   para procesar nuevos proveedores sin crear un parser por cada NIT. Los ZIP con XML se prefieren
   porque contienen campos e ítems estructurados.
- Validación aritmética automática: si la suma de ítems no cuadra con el subtotal
  declarado (margen de $5), se muestra una alerta.
- Normalización de insumos con fuzzy matching (rapidfuzz, umbral 80/100). Score >=90 se
  acepta solo; 80-89 pide confirmación (en modo --auto se trata como insumo nuevo para
  no arriesgar una fusión incorrecta).
- Base de datos SQLite (`apu.db`) con las tablas: facturas, factura_items,
  insumos_maestros, insumo_aliases, apus, apu_detalle.

## Para agregar un proveedor nuevo

1. Guarda el texto extraído del PDF (`extraer_texto()` en `extractor.py`) para ver su formato.
2. Escribe una función `_parse_<proveedor>` siguiendo el patrón de las tres existentes.
3. Agrega una tupla `(regex_del_nit, tu_funcion)` a la lista `PROVEEDORES`.

## Pendiente / próximos pasos sugeridos

- Interfaz Streamlit para revisar y confirmar matches visualmente (en vez de consola).
- Módulo para cargar mano de obra y equipo manualmente (no vienen en facturas de materiales).
- Constructor de APU: UI para armar partidas combinando insumos + rendimientos.
- Exportar APU final a Excel/Word con el formato que uses en tus presupuestos.
- La vista `v_insumo_precio_historico` cuenta líneas de factura, no facturas distintas
  (un mismo insumo repetido dos veces en una factura cuenta 2) -- ajustar si te importa
  la distinción.
