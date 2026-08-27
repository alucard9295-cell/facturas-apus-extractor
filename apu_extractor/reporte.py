"""Muestra el estado actual del diccionario maestro de insumos y su histórico
de precios -- lo que alimentará directamente tus APUs."""
import db

conn = db.get_conn()

print("=" * 100)
print("INSUMOS MAESTROS Y PRECIOS")
print("=" * 100)

insumos = conn.execute(
    "SELECT insumo_id, nombre_normalizado, categoria, unidad_estandar FROM insumos_maestros ORDER BY categoria, nombre_normalizado"
).fetchall()

for ins in insumos:
    hist = conn.execute(
        "SELECT * FROM v_insumo_precio_historico WHERE insumo_id=?", (ins["insumo_id"],)
    ).fetchall()
    aliases = conn.execute(
        "SELECT texto_original FROM insumo_aliases WHERE insumo_id=?", (ins["insumo_id"],)
    ).fetchall()

    print(f"\n[{ins['categoria']}] {ins['nombre_normalizado']}  (unidad: {ins['unidad_estandar'] or '?'})")
    for h in hist:
        print(f"    {h['proveedor_nombre']:32s} promedio=${h['precio_promedio']:>10,.0f} "
              f"(min ${h['precio_min']:,.0f} / max ${h['precio_max']:,.0f})  "
              f"[{h['num_facturas']} factura(s), última: {h['fecha_ultima_compra']}]")
    if len(aliases) > 1:
        print(f"    aliases: {[a['texto_original'] for a in aliases]}")

conn.close()
