"""Sugiere coincidencias entre una descripción cruda de factura y los insumos
maestros ya existentes, usando similitud de texto (rapidfuzz). El usuario
confirma o rechaza; nunca se crea/asigna un insumo automáticamente sin pasar
por esa confirmación.
"""
from rapidfuzz import process, fuzz

UMBRAL_SUGERENCIA = 80  # score 0-100; por debajo de esto se trata como insumo nuevo


def _limpiar(texto: str) -> str:
    return " ".join(texto.upper().split())


def sugerir_match(descripcion_cruda: str, insumos_existentes: dict) -> tuple:
    """insumos_existentes: {insumo_id: nombre_normalizado}
    Retorna (insumo_id_sugerido, nombre_sugerido, score) o (None, None, 0) si no hay
    ninguna coincidencia por encima del umbral.
    """
    if not insumos_existentes:
        return None, None, 0

    objetivo = _limpiar(descripcion_cruda)
    opciones = {iid: _limpiar(nombre) for iid, nombre in insumos_existentes.items()}

    mejor = process.extractOne(
        objetivo, opciones, scorer=fuzz.WRatio
    )
    if mejor is None:
        return None, None, 0

    nombre_match, score, insumo_id = mejor
    if score >= UMBRAL_SUGERENCIA:
        return insumo_id, insumos_existentes[insumo_id], score
    return None, None, score
