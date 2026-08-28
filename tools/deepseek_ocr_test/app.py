"""Small local DeepSeek-OCR smoke test for invoice PDFs."""

from pathlib import Path
import tempfile
import json
import re

import streamlit as st


MODEL_NAME = "deepseek-ai/DeepSeek-OCR"


@st.cache_resource
def load_model():
    import torch
    from transformers import AutoModel, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)
    try:
        model = AutoModel.from_pretrained(
            MODEL_NAME,
            trust_remote_code=True,
            use_safetensors=True,
            torch_dtype=torch.bfloat16,
            low_cpu_mem_usage=True,
            device_map="auto",
            _attn_implementation="flash_attention_2",
        )
    except (ImportError, ValueError):
        model = AutoModel.from_pretrained(
            MODEL_NAME,
            trust_remote_code=True,
            use_safetensors=True,
            torch_dtype=torch.bfloat16,
            low_cpu_mem_usage=True,
            device_map="auto",
            _attn_implementation="eager",
        )
    return tokenizer, model.eval()


def pdf_pages(pdf_bytes: bytes, output_dir: Path) -> list[Path]:
    import fitz

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    paths = []
    for index, page in enumerate(document):
        image = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        path = output_dir / f"page-{index + 1}.png"
        image.save(path)
        paths.append(path)
    document.close()
    return paths


def parse_json_output(raw: str) -> dict | list | None:
    cleaned = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    candidate = fenced.group(1).strip() if fenced else cleaned
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def result_text(result, output_dir: Path, image_path: Path) -> str:
    if isinstance(result, str) and result.strip():
        return result
    candidates = sorted(
        path for path in output_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".md", ".txt"}
        and path != image_path
    )
    if candidates:
        return candidates[-1].read_text(encoding="utf-8", errors="replace")
    return ""


def main():
    st.set_page_config(page_title="DeepSeek-OCR prueba", layout="wide")
    st.title("Prueba local de DeepSeek-OCR")
    st.caption("Carga una factura, ejecuta OCR y revisa Markdown o JSON. No guarda nada en SQLite.")
    uploaded = st.file_uploader("Factura PDF", type=["pdf"])
    max_pages = st.number_input("Máximo de páginas", min_value=1, max_value=20, value=3)
    output_mode = st.radio("Salida", ["Markdown", "JSON de factura"], horizontal=True)
    if not uploaded or not st.button("Ejecutar OCR", type="primary"):
        return

    import torch
    from PIL import Image

    if not torch.cuda.is_available():
        st.error("CUDA no está disponible. Esta prueba requiere una GPU NVIDIA.")
        return

    with tempfile.TemporaryDirectory(prefix="deepseek-ocr-") as temporary:
        image_paths = pdf_pages(uploaded.getvalue(), Path(temporary))[: int(max_pages)]
        try:
            with st.spinner("Descargando/cargando DeepSeek-OCR en la GPU. La primera vez puede tardar varios minutos..."):
                tokenizer, model = load_model()
            outputs = []
            for index, image_path in enumerate(image_paths, start=1):
                st.subheader(f"Página {index}")
                st.image(Image.open(image_path), use_container_width=True)
                with st.spinner("Ejecutando OCR..."):
                    prompt = "<image>\\n<|grounding|>Convert the document to markdown." if output_mode == "Markdown" else "<image>\\nReturn ONLY valid JSON with this schema: {proveedor_nombre, numero_factura, fecha_factura, subtotal, iva, total_pagar, items:[{descripcion_cruda, cantidad, valor_unitario, valor_total}]}. Use null when unknown. Do not add markdown or explanations."
                    result = model.infer(
                        tokenizer,
                        prompt=prompt,
                        image_file=str(image_path),
                        output_path=temporary,
                        base_size=1024,
                        image_size=640,
                        crop_mode=True,
                        save_results=True,
                        test_compress=True,
                    )
                raw_result = result_text(result, Path(temporary), image_path)
                outputs.append(raw_result)
                if output_mode == "Markdown":
                    st.markdown(raw_result or "El modelo no genero un archivo de salida.")
                else:
                    parsed = parse_json_output(raw_result)
                    if parsed is None:
                        st.warning("El modelo no devolvió JSON válido. Se muestra la salida original.")
                        st.code(raw_result or "Sin salida de texto", language="text")
                    else:
                        st.json(parsed)
                    st.download_button("Descargar JSON de esta página", json.dumps(parsed if parsed is not None else {"raw": raw_result}, ensure_ascii=False, indent=2), file_name=f"{Path(uploaded.name).stem}-pagina-{index}.json", mime="application/json", key=f"download-{index}")
            if output_mode == "JSON de factura" and outputs:
                st.download_button("Descargar todas las salidas JSON", json.dumps([parse_json_output(item) or {"raw": item} for item in outputs], ensure_ascii=False, indent=2), file_name=f"{Path(uploaded.name).stem}-ocr.json", mime="application/json", key="download-all")
        except OSError as error:
            if "1455" in str(error):
                st.error("Windows se quedo sin memoria virtual al cargar el modelo. Amplia el archivo de paginacion y cierra otras aplicaciones.")
            else:
                st.exception(error)
        except Exception as error:
            st.exception(error)


if __name__ == "__main__":
    main()
