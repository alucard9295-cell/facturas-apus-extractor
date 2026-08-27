"""Procesa una carpeta de facturas PDF: extrae, guarda en SQLite y normaliza
insumos contra el diccionario maestro.

Modo interactivo (uso real):   python procesar.py facturas/*.pdf
Modo automático (demo/tests):  python procesar.py --auto facturas/*.pdf
  En modo auto: matches >=90 se aceptan solos, matches 80-89 se crean como
  insumo nuevo (para no asignar mal), y sin match se crea insumo nuevo con
  categoría adivinada por palabras clave. Esto es solo para probar el flujo
  sin intervención humana -- en uso real siempre se confirma con la persona.
"""
import sys
import argparse
from pathlib import Path

import db
from extractor import parsear_factura
from normalize import sugerir_match, UMBRAL_SUGERENCIA

PALABRAS_SERVICIO = ("SERVICIO", "MANO DE OBRA", "INSTALACION", "INSTALACIÓN")


def adivinar_categoria(descripcion: str) -> str:
    desc = descripcion.upper()
    if any(p in desc for p in PALABRAS_SERVICIO):
        return "servicio_terceros"
    return "material"


def diagnosticar_extraccion(factura: dict, items: list[dict]) -> dict:
    """Resume campos ausentes para que cada documento deje una prueba legible."""
    required = ("proveedor_nombre", "proveedor_nit", "numero_factura", "fecha_factura", "subtotal", "iva", "total_pagar")
    missing = [field for field in required if factura.get(field) in (None, "")]
    incomplete_items = sum(
        1 for item in items
        if not item.get("descripcion_cruda") or item.get("cantidad") is None or item.get("valor_total") is None
    )
    return {
        "status": "ok" if not missing and incomplete_items == 0 else "warning",
        "missing_fields": missing,
        "items_extracted": len(items),
        "items_incomplete": incomplete_items,
    }


def procesar_item(conn, factura_id, item, proveedor_nit, auto=False):
    insumos = db.listar_insumos(conn)
    insumo_id, nombre_sugerido, score = sugerir_match(item["descripcion_cruda"], insumos)

    if insumo_id and score >= 90:
        decision = "auto-aceptado"
    elif insumo_id and score >= UMBRAL_SUGERENCIA:
        if auto:
            insumo_id, decision = None, "creado-nuevo(auto, score medio)"
        else:
            resp = input(
                f"  ¿'{item['descripcion_cruda']}' es lo mismo que "
                f"'{nombre_sugerido}'? (score {score:.0f}) [s/n]: "
            ).strip().lower()
            decision = "confirmado-usuario" if resp == "s" else "rechazado-usuario"
            if resp != "s":
                insumo_id = None
    else:
        insumo_id, decision = None, "sin-match"

    if insumo_id is None:
        nombre = " ".join(item["descripcion_cruda"].split())
        categoria = adivinar_categoria(nombre)
        unidad = item.get("unidad_medida")
        insumo_id = db.crear_insumo(conn, nombre, categoria, unidad)
        decision += " -> insumo nuevo creado"

    db.agregar_alias(conn, insumo_id, item["descripcion_cruda"], proveedor_nit)
    item_id = db.insertar_item(conn, factura_id, item)
    db.asignar_insumo_item(conn, item_id, insumo_id)
    return decision, score


def procesar_pdf(conn, pdf_path: str, auto=False):
    print(f"\n=== {Path(pdf_path).name} ===")
    factura, items = parsear_factura(pdf_path)

    if db.factura_ya_existe(conn, factura.get("cufe")):
        print("  (ya estaba en la base de datos, se omite)")
        return {
            "status": "skipped",
            "file": Path(pdf_path).name,
            "invoice": factura.get("numero_factura"),
            "provider": factura.get("proveedor_nombre"),
            "items": 0,
            "diagnostics": {"status": "skipped", "reason": "Factura ya existente en SQLite."},
        }

    factura_id = db.insertar_factura(conn, factura)
    print(f"  Proveedor: {factura['proveedor_nombre']} | Factura: {factura['numero_factura']} "
          f"| Total: {factura['total_pagar']:,.0f}")

    suma_items = sum(i["valor_total"] for i in items)
    if factura.get("subtotal") and abs(suma_items - factura["subtotal"]) > 5:
        print(f"  [ALERTA] suma de items ({suma_items:,.0f}) no cuadra con el "
              f"subtotal declarado ({factura['subtotal']:,.0f}) -- revisar extracción manualmente")

    for item in items:
        decision, score = procesar_item(conn, factura_id, item, factura["proveedor_nit"], auto=auto)
        print(f"  - {item['descripcion_cruda'][:45]:45s} qty={item['cantidad']:>6} "
              f"$/u={item['valor_unitario']:>10,.0f}  [{decision}]")

    conn.commit()
    return {
        "status": "processed",
        "file": Path(pdf_path).name,
        "invoice": factura.get("numero_factura"),
        "provider": factura.get("proveedor_nombre"),
        "items": len(items),
        "total": factura.get("total_pagar"),
        "diagnostics": diagnosticar_extraccion(factura, items),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--auto", action="store_true")
    args = ap.parse_args()

    db.inicializar_db()
    conn = db.get_conn()
    for pdf_path in args.pdfs:
        try:
            procesar_pdf(conn, pdf_path, auto=args.auto)
        except Exception as e:
            print(f"  ERROR procesando {pdf_path}: {e}")
    conn.close()


if __name__ == "__main__":
    main()
