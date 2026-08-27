-- Esquema para el extractor de facturas de construcción -> APUs

CREATE TABLE IF NOT EXISTS facturas (
    factura_id INTEGER PRIMARY KEY AUTOINCREMENT,
    proveedor_nombre TEXT NOT NULL,
    proveedor_nit TEXT NOT NULL,
    numero_factura TEXT NOT NULL,
    fecha_factura TEXT,
    fecha_vencimiento TEXT,
    forma_pago TEXT,
    medio_pago TEXT,
    cliente_nombre TEXT,
    cliente_nit TEXT,
    proyecto TEXT,
    subtotal REAL,
    iva REAL,
    descuento REAL,
    retefuente REAL,
    reteiva REAL,
    reteica REAL,
    total_pagar REAL,
    cufe TEXT UNIQUE,
    archivo_origen TEXT,
    fecha_procesado TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS factura_items (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    factura_id INTEGER NOT NULL REFERENCES facturas(factura_id),
    codigo_proveedor TEXT,
    descripcion_cruda TEXT NOT NULL,
    unidad_medida TEXT,
    cantidad REAL,
    valor_unitario REAL,
    valor_total REAL,
    descuento REAL DEFAULT 0,
    insumo_id INTEGER REFERENCES insumos_maestros(insumo_id)  -- NULL hasta que se normalice
);

CREATE TABLE IF NOT EXISTS insumos_maestros (
    insumo_id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_normalizado TEXT NOT NULL UNIQUE,
    categoria TEXT NOT NULL CHECK(categoria IN ('material','mano_obra','equipo','transporte','servicio_terceros')),
    unidad_estandar TEXT,
    fecha_creado TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS insumo_aliases (
    alias_id INTEGER PRIMARY KEY AUTOINCREMENT,
    insumo_id INTEGER NOT NULL REFERENCES insumos_maestros(insumo_id),
    texto_original TEXT NOT NULL,
    proveedor_nit TEXT,
    UNIQUE(texto_original, proveedor_nit)
);

-- APUs (se llenan después, manualmente o con ayuda del sistema)
CREATE TABLE IF NOT EXISTS apus (
    apu_id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_partida TEXT NOT NULL,
    unidad TEXT NOT NULL,
    descripcion TEXT,
    categoria TEXT NOT NULL DEFAULT 'obra_gris',
    administracion_pct REAL NOT NULL DEFAULT 0,
    imprevistos_pct REAL NOT NULL DEFAULT 0,
    utilidad_pct REAL NOT NULL DEFAULT 0,
    iva_pct REAL NOT NULL DEFAULT 0,
    iva_base TEXT NOT NULL DEFAULT 'utilidad'
);

CREATE TABLE IF NOT EXISTS apu_detalle (
    detalle_id INTEGER PRIMARY KEY AUTOINCREMENT,
    apu_id INTEGER NOT NULL REFERENCES apus(apu_id),
    insumo_id INTEGER NOT NULL REFERENCES insumos_maestros(insumo_id),
    categoria TEXT NOT NULL,
    rendimiento REAL NOT NULL,
    desperdicio_pct REAL DEFAULT 0,
    precio_unitario REAL
);

CREATE TABLE IF NOT EXISTS proyectos (
    proyecto_id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    cliente TEXT,
    ubicacion TEXT,
    fecha_inicio TEXT NOT NULL,
    fecha_creado TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS proyecto_partidas (
    partida_id INTEGER PRIMARY KEY AUTOINCREMENT,
    proyecto_id INTEGER NOT NULL REFERENCES proyectos(proyecto_id) ON DELETE CASCADE,
    fase TEXT NOT NULL,
    apu_id INTEGER NOT NULL REFERENCES apus(apu_id),
    cantidad REAL NOT NULL CHECK(cantidad > 0),
    rendimiento_diario REAL NOT NULL CHECK(rendimiento_diario > 0),
    orden INTEGER NOT NULL DEFAULT 1,
    costo_unitario REAL NOT NULL,
    costo_total REAL NOT NULL,
    duracion_dias INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS proyecto_dependencias (
    dependencia_id INTEGER PRIMARY KEY AUTOINCREMENT,
    proyecto_id INTEGER NOT NULL REFERENCES proyectos(proyecto_id) ON DELETE CASCADE,
    partida_id INTEGER NOT NULL REFERENCES proyecto_partidas(partida_id) ON DELETE CASCADE,
    depende_de_partida_id INTEGER NOT NULL REFERENCES proyecto_partidas(partida_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS proyecto_resultados (
    resultado_id INTEGER PRIMARY KEY AUTOINCREMENT,
    proyecto_id INTEGER NOT NULL REFERENCES proyectos(proyecto_id) ON DELETE CASCADE,
    costo_total REAL NOT NULL,
    duracion_dias INTEGER NOT NULL,
    fecha_fin TEXT
);

-- Gasto mensual por proveedor y proyecto (fecha_factura ya viene normalizada a ISO YYYY-MM-DD)
CREATE VIEW IF NOT EXISTS v_gasto_mensual AS
SELECT
    substr(fecha_factura, 1, 7) AS mes,
    proveedor_nombre,
    proyecto,
    COUNT(*) AS num_facturas,
    SUM(subtotal) AS gasto_materiales,
    SUM(iva) AS iva_total,
    SUM(total_pagar) AS total_pagado
FROM facturas
WHERE fecha_factura IS NOT NULL
GROUP BY mes, proveedor_nombre, proyecto
ORDER BY mes;

-- Gasto mensual por categoría de insumo (material / mano_obra / equipo / etc.)
CREATE VIEW IF NOT EXISTS v_gasto_mensual_categoria AS
SELECT
    substr(f.fecha_factura, 1, 7) AS mes,
    im.categoria,
    SUM(fi.valor_total) AS gasto
FROM factura_items fi
JOIN facturas f ON f.factura_id = fi.factura_id
JOIN insumos_maestros im ON im.insumo_id = fi.insumo_id
WHERE f.fecha_factura IS NOT NULL
GROUP BY mes, im.categoria
ORDER BY mes;

-- Vista de histórico de precios resumido por insumo + proveedor
CREATE VIEW IF NOT EXISTS v_insumo_precio_historico AS
SELECT
    fi.insumo_id,
    f.proveedor_nit,
    f.proveedor_nombre,
    AVG(fi.valor_unitario) AS precio_promedio,
    MIN(fi.valor_unitario) AS precio_min,
    MAX(fi.valor_unitario) AS precio_max,
    COUNT(*) AS num_facturas,
    MAX(f.fecha_factura) AS fecha_ultima_compra
FROM factura_items fi
JOIN facturas f ON f.factura_id = fi.factura_id
WHERE fi.insumo_id IS NOT NULL
GROUP BY fi.insumo_id, f.proveedor_nit;
