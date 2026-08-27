"""Normaliza fechas de factura, que vienen en formatos distintos según el
proveedor, a formato ISO (YYYY-MM-DD) para que se puedan ordenar y agrupar
correctamente en reportes temporales.

Formatos vistos hasta ahora:
  - '19/03/2026'   (DD/MM/YYYY)
  - '13-Mar-26'    (DD-Mon-YY, mes abreviado en inglés/español)
"""
import re

MESES = {
    "ene": 1, "jan": 1, "feb": 2, "mar": 3, "abr": 4, "apr": 4,
    "may": 5, "jun": 6, "jul": 7, "ago": 8, "aug": 8, "sep": 9,
    "oct": 10, "nov": 11, "dic": 12, "dec": 12,
}


def parse_fecha(raw: str):
    """Retorna 'YYYY-MM-DD' o None si no se reconoce el formato."""
    if not raw:
        return None
    raw = raw.strip()

    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", raw)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"

    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", raw)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"

    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{2,4})$", raw)
    if m:
        d, mon, y = m.groups()
        mo = MESES.get(mon.lower())
        if mo is None:
            return None
        year = int(y)
        if year < 100:
            year += 2000
        return f"{year:04d}-{mo:02d}-{int(d):02d}"

    # ya viene en ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw

    return None
