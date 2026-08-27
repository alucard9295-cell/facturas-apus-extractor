"""Parseo de números que vienen en distintos formatos según el proveedor:
- '1.010.000'      -> punto como separador de miles (formato colombiano típico)
- '67,563.02'       -> coma miles, punto decimal (Creaciones Mundo Yesos)
- '1.172.623,45'    -> punto miles, coma decimal (Homecenter/Sodimac)
- '5.882'           -> ambiguo: si el último grupo tiene 3 dígitos, se asume separador de miles
"""
import re


def parse_number(raw: str) -> float:
    if raw is None:
        return 0.0
    s = raw.strip().replace(" ", "")
    if s in ("", "-"):
        return 0.0
    s = re.sub(r"[^\d.,-]", "", s)
    neg = s.startswith("-")
    s = s.lstrip("-")

    if "," in s and "." in s:
        # el separador que aparece más a la derecha es el decimal
        if s.rfind(",") > s.rfind("."):
            # coma decimal, punto miles: '1.172.623,45'
            s = s.replace(".", "").replace(",", ".")
        else:
            # punto decimal, coma miles: '67,563.02'
            s = s.replace(",", "")
        value = float(s)
    elif "." in s:
        parts = s.split(".")
        if len(parts[-1]) == 3 and len(parts) > 1:
            # punto como separador de miles
            value = float(s.replace(".", ""))
        else:
            # punto decimal genuino
            value = float(s)
    elif "," in s:
        parts = s.split(",")
        if len(parts[-1]) == 2:
            # coma decimal: '10,00'
            value = float(s.replace(",", "."))
        else:
            # coma como separador de miles
            value = float(s.replace(",", ""))
    else:
        value = float(s)

    return -value if neg else value

