# Extractor de facturas para APUs

Aplicacion local para leer facturas de construccion, normalizar insumos,
guardar precios en SQLite y generar reportes para APUs.

## Componentes

- `apu_extractor/`: parser por proveedor, normalizacion, base SQLite, Streamlit,
  control plane local, RAG y reportes Excel.
- `tools/deepseek_ocr_test/`: prueba aislada de DeepSeek-OCR con GPU. No forma
  parte del flujo productivo ni modifica la base de datos.

## Ejecucion

Desde `apu_extractor/`:

```powershell
uv run --python .venv/Scripts/python.exe streamlit run app.py
```

Control plane local:

```powershell
uv run --python .venv/Scripts/python.exe uvicorn control_plane:app --reload --port 8767
```

La base `apu.db`, las facturas, reportes, indices y credenciales son locales y
no deben versionarse.
