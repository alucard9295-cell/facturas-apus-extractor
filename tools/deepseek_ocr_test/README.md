# DeepSeek-OCR smoke test

Aplicacion local y aislada para comparar visualmente la lectura de una factura.
No usa SQLite, no modifica APUs y no envia archivos a un servicio externo.

## Instalacion

DeepSeek-OCR requiere CUDA, PyTorch 2.6 y una instalacion compatible de
`flash-attn`. La documentacion oficial recomienda Linux/WSL2; en Windows puro
la instalacion puede fallar por esas dependencias.

```powershell
cd tools/deepseek_ocr_test
uv venv --python 3.12
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
uv pip install --python .venv/Scripts/python.exe torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu118
uv pip install --python .venv/Scripts/python.exe flash-attn --no-build-isolation
```

En Windows, `flash-attn` puede no instalarse. La aplicacion intenta usarlo y
cae automaticamente a atencion `eager` para esta prueba.

## Ejecutar

```powershell
uv run --python .venv/Scripts/python.exe streamlit run app.py
```

La primera ejecucion descarga el modelo `deepseek-ai/DeepSeek-OCR` desde
Hugging Face. El resultado es Markdown por pagina y debe revisarse antes de
convertirlo a JSON financiero.
