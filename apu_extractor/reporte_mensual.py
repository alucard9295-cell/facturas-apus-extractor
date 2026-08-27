"""Muestra gasto mensual y opcionalmente genera el dashboard Excel."""
import argparse

import db
from excel_dashboard import OUTPUT_DEFAULT, generar_excel


def imprimir_reporte() -> None:
    db.inicializar_db()
    conn = db.get_conn()
    try:
        print("=" * 100)
        print("GASTO MENSUAL POR PROVEEDOR / PROYECTO")
        print("=" * 100)
        filas = conn.execute("SELECT * FROM v_gasto_mensual").fetchall()
        mes_actual = None
        for r in filas:
            if r["mes"] != mes_actual:
                mes_actual = r["mes"]
                print(f"\n{mes_actual}")
            proyecto = f" | proyecto: {r['proyecto']}" if r["proyecto"] else ""
            print(f"  {r['proveedor_nombre']:32s} {r['num_facturas']} factura(s)  "
                  f"materiales=${r['gasto_materiales']:>12,.0f}  "
                  f"total_pagado=${r['total_pagado']:>12,.0f}{proyecto}")

        print("\n" + "=" * 100)
        print("GASTO MENSUAL POR CATEGORÍA DE INSUMO")
        print("=" * 100)
        filas = conn.execute("SELECT * FROM v_gasto_mensual_categoria").fetchall()
        mes_actual = None
        for r in filas:
            if r["mes"] != mes_actual:
                mes_actual = r["mes"]
                print(f"\n{mes_actual}")
            print(f"  {r['categoria']:20s} ${r['gasto']:>12,.0f}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reporte mensual y dashboard financiero")
    parser.add_argument("--excel", action="store_true", help="Genera el libro Excel analitico")
    parser.add_argument("--salida", default=str(OUTPUT_DEFAULT), help="Ruta del XLSX generado")
    args = parser.parse_args()
    imprimir_reporte()
    if args.excel:
        print(f"\nExcel generado: {generar_excel(output=args.salida)}")


if __name__ == "__main__":
    main()
