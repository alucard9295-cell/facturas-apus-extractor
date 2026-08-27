import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "apu.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def inicializar_db():
    schema = (Path(__file__).parent / "schema.sql").read_text()
    conn = get_conn()
    conn.executescript(schema)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(apus)").fetchall()}
    detail_columns = {row[1] for row in conn.execute("PRAGMA table_info(apu_detalle)").fetchall()}
    migrations = {
        "categoria": "ALTER TABLE apus ADD COLUMN categoria TEXT NOT NULL DEFAULT 'obra_gris'",
        "precio_unitario": "ALTER TABLE apu_detalle ADD COLUMN precio_unitario REAL",
        "administracion_pct": "ALTER TABLE apus ADD COLUMN administracion_pct REAL NOT NULL DEFAULT 0",
        "imprevistos_pct": "ALTER TABLE apus ADD COLUMN imprevistos_pct REAL NOT NULL DEFAULT 0",
        "utilidad_pct": "ALTER TABLE apus ADD COLUMN utilidad_pct REAL NOT NULL DEFAULT 0",
        "iva_pct": "ALTER TABLE apus ADD COLUMN iva_pct REAL NOT NULL DEFAULT 0",
        "iva_base": "ALTER TABLE apus ADD COLUMN iva_base TEXT NOT NULL DEFAULT 'utilidad'",
    }
    for column, statement in migrations.items():
        available_columns = detail_columns if column == "precio_unitario" else columns
        if column not in available_columns:
            conn.execute(statement)
    conn.execute(
        "INSERT OR IGNORE INTO insumos_maestros (nombre_normalizado, categoria, unidad_estandar) VALUES (?,?,?)",
        ("Mano de obra cuadrilla muro", "mano_obra", "jornada"),
    )
    conn.commit()
    conn.close()


def factura_ya_existe(conn, cufe: str) -> bool:
    if not cufe:
        return False
    row = conn.execute("SELECT 1 FROM facturas WHERE cufe = ?", (cufe,)).fetchone()
    return row is not None


def insertar_factura(conn, factura: dict) -> int:
    campos = [
        "proveedor_nombre", "proveedor_nit", "numero_factura", "fecha_factura",
        "fecha_vencimiento", "forma_pago", "medio_pago", "cliente_nombre",
        "cliente_nit", "proyecto", "subtotal", "iva", "descuento",
        "retefuente", "reteiva", "reteica", "total_pagar", "cufe", "archivo_origen",
    ]
    valores = [factura.get(c) for c in campos]
    placeholders = ",".join("?" * len(campos))
    cur = conn.execute(
        f"INSERT INTO facturas ({','.join(campos)}) VALUES ({placeholders})",
        valores,
    )
    return cur.lastrowid


def insertar_item(conn, factura_id: int, item: dict) -> int:
    cur = conn.execute(
        """INSERT INTO factura_items
           (factura_id, codigo_proveedor, descripcion_cruda, unidad_medida,
            cantidad, valor_unitario, valor_total, descuento, insumo_id)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            factura_id,
            item.get("codigo_proveedor"),
            item["descripcion_cruda"],
            item.get("unidad_medida"),
            item.get("cantidad"),
            item.get("valor_unitario"),
            item.get("valor_total"),
            item.get("descuento", 0),
            item.get("insumo_id"),
        ),
    )
    return cur.lastrowid


def listar_insumos(conn) -> dict:
    rows = conn.execute("SELECT insumo_id, nombre_normalizado FROM insumos_maestros").fetchall()
    return {r["insumo_id"]: r["nombre_normalizado"] for r in rows}


def crear_insumo(conn, nombre: str, categoria: str, unidad: str) -> int:
    existente = conn.execute(
        "SELECT insumo_id FROM insumos_maestros WHERE nombre_normalizado = ?", (nombre,)
    ).fetchone()
    if existente:
        return existente["insumo_id"]
    cur = conn.execute(
        "INSERT INTO insumos_maestros (nombre_normalizado, categoria, unidad_estandar) VALUES (?,?,?)",
        (nombre, categoria, unidad),
    )
    return cur.lastrowid


def agregar_alias(conn, insumo_id: int, texto_original: str, proveedor_nit: str):
    conn.execute(
        "INSERT OR IGNORE INTO insumo_aliases (insumo_id, texto_original, proveedor_nit) VALUES (?,?,?)",
        (insumo_id, texto_original, proveedor_nit),
    )


def asignar_insumo_item(conn, item_id: int, insumo_id: int):
    conn.execute("UPDATE factura_items SET insumo_id = ? WHERE item_id = ?", (insumo_id, item_id))
