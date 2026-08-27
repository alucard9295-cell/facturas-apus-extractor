"""Indice semantico y RAG para facturas estructuradas.

El indice guarda una representacion textual de cada factura, no reemplaza a
SQLite para cifras exactas. Gemini Embedding 2 se usa como proveedor remoto
opcional; el proveedor local de Chroma permite probar el flujo sin API key.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import chromadb
from dotenv import load_dotenv

import db


CHROMA_PATH = Path(__file__).parent / "chroma_db"
COLLECTION_NAME = "facturas"
DEFAULT_GOOGLE_MODEL = "gemini-embedding-2-preview"
DEFAULT_DIMENSION = 768
DEFAULT_OPENCODE_GO_MODEL = "deepseek-v4-pro"
DEFAULT_OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"

load_dotenv()
logger = logging.getLogger("atlas.rag")


def _opencode_go_key() -> str | None:
    """Lee la clave de entorno o la credencial local creada por /connect."""
    key = os.getenv("OPENCODE_GO_API_KEY") or os.getenv("OPENCODE_API_KEY")
    if key:
        return key
    auth_path = Path.home() / ".local" / "share" / "opencode" / "auth.json"
    try:
        credentials = json.loads(auth_path.read_text(encoding="utf-8"))
        entry = credentials.get("opencode-go", {})
        return entry.get("key")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def _chunks(values: list[str], size: int = 32):
    for start in range(0, len(values), size):
        yield values[start:start + size]


class GoogleEmbedder:
    """Cliente pequeno para embeddings de documentos y consultas."""

    def __init__(self, model: str = DEFAULT_GOOGLE_MODEL, dimension: int = DEFAULT_DIMENSION):
        from google import genai
        from google.genai import types

        self._types = types
        self.model = model
        self.dimension = dimension
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("Configura GEMINI_API_KEY o GOOGLE_API_KEY para usar Gemini Embedding 2.")
        self.client = genai.Client(api_key=api_key)

    def embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        for batch in _chunks(texts):
            response = self.client.models.embed_content(
                model=self.model,
                contents=batch,
                config=self._types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=self.dimension,
                ),
            )
            vectors.extend([list(item.values) for item in response.embeddings])
        return vectors


class LocalEmbedder:
    """Embedding local incluido por Chroma para pruebas sin credenciales."""

    def __init__(self):
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

        self._function = DefaultEmbeddingFunction()

    def embed(self, texts: list[str], task_type: str = "") -> list[list[float]]:
        return [[float(value) for value in vector] for vector in self._function(texts)]


def _get_embedder(provider: str, model: str, dimension: int):
    if provider == "google":
        return GoogleEmbedder(model, dimension)
    if provider == "local":
        return LocalEmbedder()
    raise ValueError(f"Proveedor de embeddings no soportado: {provider}")


def _documents_from_db() -> list[dict[str, Any]]:
    db.inicializar_db()
    conn = db.get_conn()
    try:
        invoices = conn.execute(
            """
            SELECT factura_id, proveedor_nombre, proveedor_nit, numero_factura,
                   fecha_factura, proyecto, subtotal, iva, total_pagar, cufe
            FROM facturas ORDER BY factura_id
            """
        ).fetchall()
        documents = []
        for invoice in invoices:
            items = conn.execute(
                """
                SELECT descripcion_cruda, unidad_medida, cantidad,
                       valor_unitario, valor_total, insumo_id
                FROM factura_items WHERE factura_id=? ORDER BY item_id
                """,
                (invoice["factura_id"],),
            ).fetchall()
            item_lines = [
                f"{item['descripcion_cruda']} | cantidad {item['cantidad'] or 0} "
                f"| unidad {item['unidad_medida'] or ''} | valor unitario {item['valor_unitario'] or 0} "
                f"| valor total {item['valor_total'] or 0}"
                for item in items
            ]
            text = "\n".join(
                [
                    f"Factura {invoice['numero_factura'] or ''}",
                    f"Proveedor: {invoice['proveedor_nombre']} NIT {invoice['proveedor_nit']}",
                    f"Fecha: {invoice['fecha_factura'] or ''} Proyecto: {invoice['proyecto'] or ''}",
                    f"Subtotal: {invoice['subtotal'] or 0} IVA: {invoice['iva'] or 0} Total: {invoice['total_pagar'] or 0}",
                    "Items:",
                    *item_lines,
                ]
            )
            documents.append(
                {
                    "id": f"factura:{invoice['factura_id']}",
                    "text": text,
                    "metadata": {
                        "factura_id": int(invoice["factura_id"]),
                        "numero_factura": str(invoice["numero_factura"] or ""),
                        "proveedor": str(invoice["proveedor_nombre"] or ""),
                        "fecha": str(invoice["fecha_factura"] or ""),
                        "proyecto": str(invoice["proyecto"] or ""),
                        "total_pagar": float(invoice["total_pagar"] or 0),
                    },
                }
            )
        return documents
    finally:
        conn.close()


def _client(path: str | Path = CHROMA_PATH):
    Path(path).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path))


def build_index(
    provider: str = "local",
    model: str = DEFAULT_GOOGLE_MODEL,
    dimension: int = DEFAULT_DIMENSION,
    reset: bool = True,
    path: str | Path = CHROMA_PATH,
) -> dict[str, Any]:
    """Construye o actualiza el indice semantico de facturas."""
    documents = _documents_from_db()
    client = _client(path)
    effective_model = model if provider == "google" else "all-MiniLM-L6-v2"
    if reset:
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine", "embedding_provider": provider, "embedding_model": effective_model},
    )
    if not documents:
        return {"indexed": 0, "provider": provider, "model": effective_model, "path": str(path)}

    embedder = _get_embedder(provider, model, dimension)
    embeddings = embedder.embed([document["text"] for document in documents], "RETRIEVAL_DOCUMENT")
    collection.upsert(
        ids=[document["id"] for document in documents],
        documents=[document["text"] for document in documents],
        metadatas=[document["metadata"] for document in documents],
        embeddings=embeddings,
    )
    return {
        "indexed": len(documents),
        "provider": provider,
        "model": effective_model,
        "dimension": len(embeddings[0]),
        "path": str(path),
    }


def query_index(
    query: str,
    provider: str = "local",
    model: str = DEFAULT_GOOGLE_MODEL,
    dimension: int = DEFAULT_DIMENSION,
    n_results: int = 5,
    answer: bool = False,
    path: str | Path = CHROMA_PATH,
) -> dict[str, Any]:
    """Recupera facturas similares y, opcionalmente, redacta una respuesta RAG."""
    client = _client(path)
    try:
        collection = client.get_collection(COLLECTION_NAME)
    except Exception as error:
        raise RuntimeError("El indice no existe. Ejecuta primero la construccion del indice.") from error
    if collection.count() == 0:
        return {"query": query, "results": [], "answer": None}

    embedder = _get_embedder(provider, model, dimension)
    query_embedding = embedder.embed([query], "RETRIEVAL_QUERY")[0]
    found = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(max(n_results, 1), collection.count()),
        include=["documents", "metadatas", "distances"],
    )
    results = []
    for index, document in enumerate(found.get("documents", [[]])[0]):
        results.append(
            {
                "document": document,
                "metadata": found.get("metadatas", [[]])[0][index],
                "distance": found.get("distances", [[]])[0][index],
            }
        )

    generated_answer = None
    if answer:
        generated_answer = _answer_with_gemini(query, results)
    return {"query": query, "results": results, "answer": generated_answer}


def index_status(path: str | Path = CHROMA_PATH) -> dict[str, Any]:
    """Devuelve el estado del índice sin cargar embeddings ni exponer secretos."""
    client = _client(path)
    try:
        collection = client.get_collection(COLLECTION_NAME)
    except Exception:
        return {"path": str(path), "collection": COLLECTION_NAME, "exists": False, "count": 0, "metadata": {}}
    return {
        "path": str(path),
        "collection": COLLECTION_NAME,
        "exists": True,
        "count": collection.count(),
        "metadata": collection.metadata or {},
    }


def advisory_chat(
    message: str,
    history: list[dict[str, str]] | None = None,
    provider: str = "local",
    model: str = DEFAULT_GOOGLE_MODEL,
    dimension: int = DEFAULT_DIMENSION,
    n_results: int = 5,
    generation_provider: str = "auto",
) -> dict[str, Any]:
    """Responde preguntas de asesoría usando recuperación y contexto financiero."""
    retrieved = query_index(message, provider, model, dimension, n_results, answer=False)
    results = retrieved["results"]
    has_opencode_key = bool(_opencode_go_key())
    has_gemini_key = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    if (generation_provider == "opencode-go" or generation_provider == "auto") and has_opencode_key:
        answer = _answer_with_opencode_go(message, results, history or [])
        mode = "opencode_go_rag"
    elif generation_provider in {"gemini", "auto"} and has_gemini_key:
        answer = _answer_with_gemini(message, results, history or [])
        mode = "gemini_rag"
    elif results:
        sources = "\n".join(
            f"- Factura {item['metadata'].get('numero_factura', '?')} | "
            f"{item['metadata'].get('proveedor', 'Proveedor')} | "
            f"total ${item['metadata'].get('total_pagar', 0):,.0f}"
            for item in results
        )
        answer = (
            "Modo consulta local: no hay GEMINI_API_KEY para redactar una respuesta. "
            "Estas son las facturas recuperadas para revisar:\n" + sources
        )
        mode = "local_retrieval"
    else:
        answer = "No hay facturas indexadas. Procesa facturas y construye el índice antes de consultar."
        mode = "empty_index"
    return {"message": message, "answer": answer, "mode": mode, "results": results}


def architecture_chat(
    message: str,
    history: list[dict[str, str]] | None = None,
    n_results: int = 5,
    include_rag: bool = True,
) -> dict[str, Any]:
    """Asesora sobre arquitectura; las facturas solo agregan contexto opcional."""
    results = []
    if include_rag:
        try:
            retrieved = query_index(message, provider="local", n_results=n_results, answer=False)
            results = retrieved["results"]
        except Exception:
            results = []

    normalized = message.lower()
    if include_rag and results and any(term in normalized for term in ("proveedor", "gasto", "factura", "facturas")):
        totals: dict[str, float] = {}
        for item in results:
            provider = item["metadata"].get("proveedor", "Proveedor sin nombre")
            totals[provider] = totals.get(provider, 0) + float(item["metadata"].get("total_pagar") or 0)
        ranking = sorted(totals.items(), key=lambda pair: pair[1], reverse=True)
        answer = "\n".join(
            [
                "Lectura directa del índice RAG:",
                *[f"{index}. {provider}: ${total:,.0f} COP" for index, (provider, total) in enumerate(ranking, 1)],
                f"\nSe recuperaron {len(results)} factura(s). Para una cifra contable definitiva, revisa SQLite y el Excel.",
            ]
        )
        return {"message": message, "answer": answer, "mode": "local_rag_summary", "results": results}

    if _opencode_go_key():
        try:
            answer = _answer_with_opencode_go(message, results, history or [], domain="architecture")
            mode = "opencode_go_architecture_rag" if results else "opencode_go_architecture"
        except Exception:
            logger.exception("architecture.opencode_failed")
            answer = (
                "DeepSeek no respondió a tiempo. Te dejo una ruta preliminar: valida la norma urbana, "
                "levanta estructura y redes, confirma accesos independientes y compara el costo de "
                "adecuación contra la renta posible antes de comprar."
            )
            mode = "architecture_fallback_timeout"
    else:
        answer = (
            "Puedo orientarte con una primera lectura arquitectónica, pero OpenCode Go no está configurado. "
            "Indica ciudad, área, número de unidades y objetivo del inmueble."
        )
        mode = "architecture_fallback"
    return {"message": message, "answer": answer, "mode": mode, "results": results}


def build_architecture_plan(message: str) -> dict[str, Any]:
    """Construye una primera estructura de proyecto para renderizarla en la UI."""
    area_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:m2|m²|metros cuadrados)", message.lower())
    units_match = re.search(r"(\d+)\s*(?:apartamentos?|unidades|viviendas?)", message.lower())
    budget_match = re.search(r"(?:presupuesto|inversi[oó]n)[^\d]{0,20}(\d[\d.,]*)", message.lower())
    area = area_match.group(1).replace(",", ".") if area_match else "Por confirmar"
    units = units_match.group(1) if units_match else "2–4"
    budget = budget_match.group(1) if budget_match else "Por definir con diagnóstico"
    return {
        "type": "architecture_plan",
        "title": "Plan inicial de transformación",
        "summary": f"Ruta preliminar para estudiar una casa de {area} m² y convertirla en una vivienda multifamiliar de {units} unidades.",
        "inputs": {"area_m2": area, "units": units, "budget": budget},
        "phases": [
            {"name": "Diagnóstico y viabilidad", "duration": "1–2 semanas", "share": "5–8%", "deliverable": "Levantamiento, norma, redes, estructura y matriz de riesgos."},
            {"name": "Anteproyecto arquitectónico", "duration": "2–3 semanas", "share": "8–12%", "deliverable": "Distribución de unidades, accesos, iluminación, ventilación y áreas comunes."},
            {"name": "Presupuesto y licenciamiento", "duration": "2–4 semanas", "share": "10–15%", "deliverable": "Presupuesto por capítulos, cronograma, permisos y escenario de inversión."},
            {"name": "Adecuación y puesta en operación", "duration": "Según alcance", "share": "65–80%", "deliverable": "Obra, inspecciones, equipamiento y estrategia de comercialización o renta."},
        ],
        "budget": {"value": budget, "currency": "COP", "note": "No es una cotización: requiere visita, levantamiento y precios locales."},
        "next_steps": ["Confirmar ciudad, barrio y área del inmueble", "Compartir fotografías, planos o dirección aproximada", "Definir si el objetivo es vivir, rentar o vender"],
        "risks": ["Norma urbana y licencia aplicable", "Capacidad de redes y accesos independientes", "Costos reales de estructura, baños y cocinas"],
    }


def _build_prompt(
    query: str,
    results: list[dict[str, Any]],
    history: list[dict[str, str]],
    domain: str = "finance",
) -> str:
    context = "\n\n---\n\n".join(item["document"] for item in results)
    context = context or "No hay facturas indexadas para esta consulta."
    previous = "\n".join(
        f"{item.get('role', 'user')}: {item.get('content', '')}"
        for item in history[-6:]
    )
    if domain == "architecture":
        instructions = (
            "Eres el asesor arquitectónico de SOMA. Responde en español con una orientación preliminar, "
            "práctica y clara sobre transformación de casas en vivienda multifamiliar. Puedes hablar de "
            "diagnóstico, distribución, accesos, estructura, redes, licencias, fases, presupuesto y riesgos. "
            "No prometas licencias, costos exactos ni rentabilidad sin visita y validación local. "
            "Si usas datos de facturas, identifica que provienen del contexto; si no hay datos, trabaja con "
            "supuestos explícitos y pide los datos que falten."
        )
    else:
        instructions = (
            "Responde en español usando exclusivamente el contexto de facturas. "
            "Si una cifra no aparece, dilo. No inventes totales."
        )
    return (
        f"{instructions}\n\n"
        f"Historial reciente:\n{previous}\n\nPregunta: {query}\n\nContexto:\n{context}"
    )


def _answer_with_gemini(
    query: str,
    results: list[dict[str, Any]],
    history: list[dict[str, str]] | None = None,
) -> str:
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Configura GEMINI_API_KEY para generar respuestas RAG.")
    prompt = _build_prompt(query, results, history or [])
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=os.getenv("GEMINI_GENERATION_MODEL", "gemini-2.5-flash"),
        contents=prompt,
    )
    return response.text or "No se obtuvo una respuesta del modelo."


def _answer_with_opencode_go(
    query: str,
    results: list[dict[str, Any]],
    history: list[dict[str, str]],
    domain: str = "finance",
) -> str:
    from openai import OpenAI

    api_key = _opencode_go_key()
    if not api_key:
        raise RuntimeError("Configura OPENCODE_GO_API_KEY para usar OpenCode Go.")
    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("OPENCODE_GO_BASE_URL", DEFAULT_OPENCODE_GO_BASE_URL),
        timeout=15.0,
        max_retries=0,
    )
    system_prompt = (
        "Eres un asesor arquitectónico de SOMA. Sé preciso, transparente y útil."
        if domain == "architecture"
        else "Eres un asesor financiero de facturas de arquitectura. Responde con precisión y transparencia."
    )
    response = client.chat.completions.create(
        model=os.getenv("OPENCODE_GO_MODEL", DEFAULT_OPENCODE_GO_MODEL),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _build_prompt(query, results, history, domain)},
        ],
        stream=False,
    )
    return response.choices[0].message.content or "No se obtuvo una respuesta del modelo."


def main() -> None:
    parser = argparse.ArgumentParser(description="Construye y consulta el RAG de facturas")
    parser.add_argument("--provider", choices=["local", "google"], default="local")
    parser.add_argument("--model", default=DEFAULT_GOOGLE_MODEL)
    parser.add_argument("--dimension", type=int, default=DEFAULT_DIMENSION)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--query")
    parser.add_argument("--answer", action="store_true")
    args = parser.parse_args()

    if args.rebuild or args.query:
        print(build_index(args.provider, args.model, args.dimension, reset=args.rebuild))
    if args.query:
        print(query_index(args.query, args.provider, args.model, args.dimension, answer=args.answer))


if __name__ == "__main__":
    main()
