"""Small local DeepSeek-OCR smoke test for invoice PDFs."""

from pathlib import Path
import tempfile

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
            _attn_implementation="flash_attention_2",
        )
    except (ImportError, ValueError):
        model = AutoModel.from_pretrained(
            MODEL_NAME,
            trust_remote_code=True,
            use_safetensors=True,
            _attn_implementation="eager",
        )
    return tokenizer, model.eval().cuda().to(torch.bfloat16)


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


def main():
    st.set_page_config(page_title="DeepSeek-OCR prueba", layout="wide")
    st.title("Prueba local de DeepSeek-OCR")
    st.caption("Carga una factura, ejecuta OCR y revisa el Markdown. No guarda nada en SQLite.")
    uploaded = st.file_uploader("Factura PDF", type=["pdf"])
    max_pages = st.number_input("Máximo de páginas", min_value=1, max_value=20, value=3)
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
            for index, image_path in enumerate(image_paths, start=1):
                st.subheader(f"Página {index}")
                st.image(Image.open(image_path), use_container_width=True)
                with st.spinner("Ejecutando OCR..."):
                    result = model.infer(
                        tokenizer,
                        prompt="<image>\\n<|grounding|>Convert the document to markdown.",
                        image_file=str(image_path),
                        output_path=temporary,
                        base_size=1024,
                        image_size=640,
                        crop_mode=True,
                        save_results=False,
                        test_compress=True,
                    )
                st.markdown(result if isinstance(result, str) else str(result))
        except Exception as error:
            st.exception(error)


if __name__ == "__main__":
    main()
