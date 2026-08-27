"""API local para operar el harness del atlas HTML."""

from __future__ import annotations

import tempfile
import secrets
import os
import logging
import time
import asyncio
import json
import math
from datetime import date, timedelta
from uuid import uuid4
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
from excel_dashboard import generar_excel
from extractor import parsear_factura
from procesar import diagnosticar_extraccion, procesar_pdf
from rag import DEFAULT_DIMENSION, DEFAULT_GOOGLE_MODEL, DEFAULT_OPENCODE_GO_MODEL, _opencode_go_key, advisory_chat, architecture_chat, build_architecture_plan, build_index, index_status, query_index


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ATLAS_PATH = PROJECT_ROOT / "index.html"
PANEL_PATH = PROJECT_ROOT / "panel.html"
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

app = FastAPI(title="Workspace Atlas Control Plane", version="0.1.0")
DEBUG_MODE = os.getenv("ATLAS_DEBUG", "0").lower() in {"1", "true", "yes", "on"}
VERBOSE_MODE = os.getenv("ATLAS_VERBOSE", "0").lower() in {"1", "true", "yes", "on"}
logger = logging.getLogger("atlas.control_plane")
if not logger.handlers:
    logging.basicConfig(
        level=logging.DEBUG if DEBUG_MODE or VERBOSE_MODE else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
for noisy_logger in ("openai", "httpx", "httpcore"):
    logging.getLogger(noisy_logger).setLevel(logging.INFO if VERBOSE_MODE else logging.WARNING)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8767",
        "http://localhost:8767",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)
if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")


class IndexRequest(BaseModel):
    provider: str = Field(default="local", pattern="^(local|google)$")
    model: str = DEFAULT_GOOGLE_MODEL
    dimension: int = Field(default=DEFAULT_DIMENSION, ge=128, le=3072)
    reset: bool = True


class QueryRequest(BaseModel):
    query: str = Field(min_length=2)
    provider: str = Field(default="local", pattern="^(local|google)$")
    model: str = DEFAULT_GOOGLE_MODEL
    dimension: int = Field(default=DEFAULT_DIMENSION, ge=128, le=3072)
    n_results: int = Field(default=5, ge=1, le=20)
    answer: bool = False


class LoginRequest(BaseModel):
    username: str
    password: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=2)
    provider: str = Field(default="local", pattern="^(local|google)$")
    model: str = DEFAULT_GOOGLE_MODEL
    dimension: int = Field(default=DEFAULT_DIMENSION, ge=128, le=3072)
    n_results: int = Field(default=5, ge=1, le=20)
    history: list[dict[str, str]] = Field(default_factory=list)
    generation_provider: str = Field(default="auto", pattern="^(auto|opencode-go|gemini|local)$")


class SupplyUpdate(BaseModel):
    nombre_normalizado: str = Field(min_length=1, max_length=240)
    categoria: str = Field(pattern="^(material|mano_obra|equipo|transporte|servicio_terceros)$")
    unidad_estandar: str | None = Field(default=None, max_length=40)


class ApuDetailInput(BaseModel):
    insumo_id: int = Field(gt=0)
    categoria: str = Field(pattern="^(material|mano_obra|equipo|transporte|servicio_terceros)$")
    rendimiento: float = Field(gt=0)
    desperdicio_pct: float = Field(default=0, ge=0, le=100)
    precio_unitario: float | None = Field(default=None, ge=0)


class ApuCreate(BaseModel):
    nombre_partida: str = Field(min_length=1, max_length=240)
    unidad: str = Field(min_length=1, max_length=40)
    descripcion: str | None = Field(default=None, max_length=1000)
    categoria: str = Field(default="obra_gris", pattern="^(excavaciones|obra_gris|acabados|instalaciones)$")
    detalles: list[ApuDetailInput] = Field(min_length=1)
    administracion_pct: float = Field(default=0, ge=0, le=100)
    imprevistos_pct: float = Field(default=0, ge=0, le=100)
    utilidad_pct: float = Field(default=0, ge=0, le=100)
    iva_pct: float = Field(default=0, ge=0, le=100)
    iva_base: str = Field(default="utilidad", pattern="^(directo|subtotal|utilidad)$")


class ApuUpdate(ApuCreate):
    pass


class ProjectCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=240)
    cliente: str | None = Field(default=None, max_length=240)
    ubicacion: str | None = Field(default=None, max_length=240)
    fecha_inicio: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class ProjectPartidaCreate(BaseModel):
    fase: str = Field(min_length=1, max_length=120)
    apu_id: int = Field(gt=0)
    cantidad: float = Field(gt=0)
    rendimiento_diario: float = Field(gt=0)
    orden: int = Field(default=1, ge=1)


ADMIN_USER = os.getenv("ADMIN_USER", "ADMON")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "1234")
_sessions: set[str] = set()
_login_attempts: dict[str, list[float]] = {}
LOGIN_WINDOW_SECONDS = 60
MAX_LOGIN_ATTEMPTS = 5


def _has_valid_session(authorization: str | None) -> bool:
    return bool(authorization and authorization.startswith("Bearer ") and authorization.removeprefix("Bearer ") in _sessions)


def _require_admin(authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        logger.warning("auth.reject reason=missing_bearer")
        raise HTTPException(status_code=401, detail="Inicia sesión como administrador.")
    if not _has_valid_session(authorization):
        logger.warning("auth.reject reason=invalid_token")
        raise HTTPException(status_code=401, detail="Sesión administrativa inválida o expirada.")


@app.middleware("http")
async def request_trace(request, call_next):
    request_id = request.headers.get("X-Request-ID", uuid4().hex[:12])
    started = time.perf_counter()
    if DEBUG_MODE or VERBOSE_MODE:
        logger.debug(
            "http.start id=%s method=%s path=%s client=%s auth=%s",
            request_id,
            request.method,
            request.url.path,
            request.client.host if request.client else "unknown",
            bool(request.headers.get("Authorization")),
        )
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("http.error id=%s method=%s path=%s", request_id, request.method, request.url.path)
        raise
    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    if VERBOSE_MODE or response.status_code >= 400:
        logger.info(
            "http.end id=%s method=%s path=%s status=%s duration_ms=%.2f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
    return response


@app.get("/api/status")
def status(authorization: str | None = Header(default=None)) -> dict:
    _require_admin(authorization)
    db.inicializar_db()
    conn = db.get_conn()
    try:
        counts = {}
        for table in ("facturas", "factura_items", "insumos_maestros"):
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()
    return {"ok": True, "database": str(db.DB_PATH), "counts": counts}


@app.post("/api/admin/login")
def admin_login(request: LoginRequest, http_request: Request) -> dict:
    client_id = http_request.client.host if http_request.client else "unknown"
    now = time.time()
    recent_attempts = [stamp for stamp in _login_attempts.get(client_id, []) if now - stamp < LOGIN_WINDOW_SECONDS]
    _login_attempts[client_id] = recent_attempts
    if len(recent_attempts) >= MAX_LOGIN_ATTEMPTS:
        logger.warning("auth.rate_limited client=%s", client_id)
        raise HTTPException(status_code=429, detail="Demasiados intentos. Espera un minuto antes de volver a intentar.", headers={"Retry-After": str(LOGIN_WINDOW_SECONDS)})
    logger.info("auth.login user=%s", request.username)
    if request.username != ADMIN_USER or request.password != ADMIN_PASSWORD:
        recent_attempts.append(now)
        logger.warning("auth.login_failed user=%s", request.username)
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")
    _login_attempts.pop(client_id, None)
    token = secrets.token_urlsafe(32)
    _sessions.add(token)
    logger.info("auth.login_ok user=%s active_sessions=%s", ADMIN_USER, len(_sessions))
    return {"ok": True, "username": ADMIN_USER, "token": token}


@app.get("/api/admin/summary")
def admin_summary(authorization: str | None = Header(default=None)) -> dict:
    _require_admin(authorization)
    logger.info("finance.summary requested")
    db.inicializar_db()
    conn = db.get_conn()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS facturas,
                   COALESCE(SUM(total_pagar), 0) AS total_pagado,
                   COALESCE(SUM(subtotal), 0) AS subtotal,
                   COALESCE(SUM(iva), 0) AS iva_total,
                   COUNT(DISTINCT proveedor_nit) AS proveedores
            FROM facturas
            """
        ).fetchone()
        items = conn.execute("SELECT COUNT(*) FROM factura_items").fetchone()[0]
        alerts = conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT f.factura_id
                FROM facturas f LEFT JOIN factura_items fi ON fi.factura_id = f.factura_id
                GROUP BY f.factura_id
                HAVING f.subtotal IS NOT NULL
                   AND ABS(COALESCE(SUM(fi.valor_total), 0) - f.subtotal) > 5
            )
            """
        ).fetchone()[0]
        monthly = conn.execute(
            """
            SELECT COALESCE(NULLIF(substr(fecha_factura, 1, 7), ''), 'Sin fecha') AS mes,
                   COUNT(*) AS facturas, COALESCE(SUM(total_pagar), 0) AS total
            FROM facturas GROUP BY mes ORDER BY mes
            """
        ).fetchall()
        providers = conn.execute(
            """
            SELECT proveedor_nombre AS proveedor, COUNT(*) AS facturas,
                   COALESCE(SUM(total_pagar), 0) AS total
            FROM facturas GROUP BY proveedor_nombre ORDER BY total DESC
            """
        ).fetchall()
        quality = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN total_pagar IS NOT NULL THEN 1 ELSE 0 END), 0) AS con_total,
                   COALESCE(SUM(CASE WHEN fecha_factura IS NOT NULL THEN 1 ELSE 0 END), 0) AS con_fecha
            FROM facturas
            """
        ).fetchone()
    finally:
        conn.close()
    return {
        "facturas": row["facturas"],
        "total_pagado": row["total_pagado"],
        "subtotal": row["subtotal"],
        "iva_total": row["iva_total"],
        "proveedores": row["proveedores"],
        "items": items,
        "alertas_calidad": alerts,
        "monthly": [dict(item) for item in monthly],
        "providers_chart": [dict(item) for item in providers],
        "quality": dict(quality),
        "chroma_count": index_status().get("count", 0),
    }


@app.get("/api/admin/supplies")
def list_supplies(
    authorization: str | None = Header(default=None),
    search: str = Query(default="", max_length=100),
    category: str | None = Query(default=None),
) -> dict:
    """Lista insumos maestros con precios historicos y alias para normalizacion."""
    _require_admin(authorization)
    db.inicializar_db()
    conn = db.get_conn()
    try:
        clauses = []
        params: list[str] = []
        if search.strip():
            clauses.append("(im.nombre_normalizado LIKE ? OR EXISTS (SELECT 1 FROM insumo_aliases sa WHERE sa.insumo_id = im.insumo_id AND sa.texto_original LIKE ?))")
            term = f"%{search.strip()}%"
            params.extend([term, term])
        if category:
            if category not in {"material", "mano_obra", "equipo", "transporte", "servicio_terceros"}:
                raise HTTPException(status_code=400, detail="Categoría inválida.")
            clauses.append("im.categoria = ?")
            params.append(category)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"""
            SELECT im.insumo_id, im.nombre_normalizado, im.categoria, im.unidad_estandar,
                   COUNT(DISTINCT ia.alias_id) AS alias_count,
                   COUNT(DISTINCT fi.item_id) AS purchase_count,
                   AVG(fi.valor_unitario) AS price_average,
                   MIN(fi.valor_unitario) AS price_min,
                   MAX(fi.valor_unitario) AS price_max,
                   MAX(f.fecha_factura) AS last_purchase
              FROM insumos_maestros im
              LEFT JOIN insumo_aliases ia ON ia.insumo_id = im.insumo_id
              LEFT JOIN factura_items fi ON fi.insumo_id = im.insumo_id
              LEFT JOIN facturas f ON f.factura_id = fi.factura_id
              {where}
             GROUP BY im.insumo_id
             ORDER BY im.categoria, im.nombre_normalizado
            """,
            params,
        ).fetchall()
        result = []
        for row in rows:
            aliases = conn.execute(
                "SELECT texto_original, proveedor_nit FROM insumo_aliases WHERE insumo_id=? ORDER BY alias_id LIMIT 12",
                (row["insumo_id"],),
            ).fetchall()
            result.append({**dict(row), "aliases": [dict(alias) for alias in aliases]})
        return {"items": result, "count": len(result)}
    finally:
        conn.close()


@app.put("/api/admin/supplies/{supply_id}")
def update_supply(supply_id: int, request: SupplyUpdate, authorization: str | None = Header(default=None)) -> dict:
    """Actualiza el nombre canónico, categoría o unidad de un insumo."""
    _require_admin(authorization)
    db.inicializar_db()
    conn = db.get_conn()
    try:
        current = conn.execute("SELECT insumo_id FROM insumos_maestros WHERE insumo_id=?", (supply_id,)).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="Insumo no encontrado.")
        duplicate = conn.execute(
            "SELECT insumo_id FROM insumos_maestros WHERE nombre_normalizado=? AND insumo_id<>?",
            (request.nombre_normalizado.strip(), supply_id),
        ).fetchone()
        if duplicate:
            raise HTTPException(status_code=409, detail="Ya existe otro insumo con ese nombre normalizado.")
        conn.execute(
            "UPDATE insumos_maestros SET nombre_normalizado=?, categoria=?, unidad_estandar=? WHERE insumo_id=?",
            (request.nombre_normalizado.strip(), request.categoria, request.unidad_estandar.strip() if request.unidad_estandar else None, supply_id),
        )
        conn.commit()
        return {"ok": True, "insumo_id": supply_id}
    finally:
        conn.close()


def _apu_response(conn, apu_id: int | None = None) -> list[dict]:
    where = "WHERE a.apu_id = ?" if apu_id is not None else ""
    params = (apu_id,) if apu_id is not None else ()
    apus = conn.execute(
        f"""SELECT a.apu_id, a.nombre_partida, a.unidad, a.descripcion, a.categoria,
                          a.administracion_pct, a.imprevistos_pct, a.utilidad_pct,
                          a.iva_pct, a.iva_base
                     FROM apus a {where} ORDER BY a.apu_id DESC""",
        params,
    ).fetchall()
    result = []
    for apu in apus:
        details = conn.execute(
            """
            SELECT ad.detalle_id, ad.insumo_id, ad.categoria, ad.rendimiento,
                   ad.desperdicio_pct, ad.precio_unitario AS precio_manual,
                   im.nombre_normalizado, im.unidad_estandar,
                   COALESCE(AVG(fi.valor_unitario), 0) AS precio_unitario
              FROM apu_detalle ad
              JOIN insumos_maestros im ON im.insumo_id = ad.insumo_id
              LEFT JOIN factura_items fi ON fi.insumo_id = im.insumo_id
             WHERE ad.apu_id = ?
             GROUP BY ad.detalle_id
             ORDER BY ad.detalle_id
            """,
            (apu["apu_id"],),
        ).fetchall()
        detail_rows = []
        for detail in details:
            row = dict(detail)
            row["precio_aplicado"] = row["precio_manual"] if row["precio_manual"] is not None else row["precio_unitario"]
            row["costo"] = row["rendimiento"] * row["precio_aplicado"] * (1 + row["desperdicio_pct"] / 100)
            detail_rows.append(row)
        category_totals = {}
        for row in detail_rows:
            category_totals[row["categoria"]] = category_totals.get(row["categoria"], 0) + row["costo"]
        direct_cost = sum(category_totals.values())
        administration = direct_cost * (apu["administracion_pct"] or 0) / 100
        contingencies = direct_cost * (apu["imprevistos_pct"] or 0) / 100
        utility = direct_cost * (apu["utilidad_pct"] or 0) / 100
        subtotal = direct_cost + administration + contingencies + utility
        iva_base = {"directo": direct_cost, "subtotal": subtotal, "utilidad": utility}[apu["iva_base"]]
        iva = iva_base * (apu["iva_pct"] or 0) / 100
        item = dict(apu)
        item["detalles"] = detail_rows
        item["category_totals"] = category_totals
        item["costo_directo"] = direct_cost
        item["administracion"] = administration
        item["imprevistos"] = contingencies
        item["utilidad"] = utility
        item["subtotal"] = subtotal
        item["base_iva"] = iva_base
        item["iva"] = iva
        item["precio_venta"] = subtotal + iva
        result.append(item)
    return result


@app.get("/api/admin/apus")
def list_apus(authorization: str | None = Header(default=None)) -> dict:
    _require_admin(authorization)
    db.inicializar_db()
    conn = db.get_conn()
    try:
        items = _apu_response(conn)
        return {"items": items, "count": len(items)}
    finally:
        conn.close()


@app.post("/api/admin/apus")
def create_apu(request: ApuCreate, authorization: str | None = Header(default=None)) -> dict:
    _require_admin(authorization)
    db.inicializar_db()
    conn = db.get_conn()
    try:
        supply_ids = [detail.insumo_id for detail in request.detalles]
        placeholders = ",".join("?" for _ in supply_ids)
        supplies = conn.execute(
            f"SELECT insumo_id, categoria FROM insumos_maestros WHERE insumo_id IN ({placeholders})",
            supply_ids,
        ).fetchall()
        supply_map = {row["insumo_id"]: row["categoria"] for row in supplies}
        missing = [supply_id for supply_id in supply_ids if supply_id not in supply_map]
        if missing:
            raise HTTPException(status_code=400, detail=f"Insumos no encontrados: {missing}")
        cursor = conn.execute(
            """INSERT INTO apus
               (nombre_partida, unidad, descripcion, categoria, administracion_pct,
                imprevistos_pct, utilidad_pct, iva_pct, iva_base)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (request.nombre_partida.strip(), request.unidad.strip(), request.descripcion.strip() if request.descripcion else None,
             request.categoria, request.administracion_pct, request.imprevistos_pct, request.utilidad_pct, request.iva_pct, request.iva_base),
        )
        apu_id = cursor.lastrowid
        for detail in request.detalles:
            conn.execute(
                "INSERT INTO apu_detalle (apu_id, insumo_id, categoria, rendimiento, desperdicio_pct, precio_unitario) VALUES (?,?,?,?,?,?)",
                (apu_id, detail.insumo_id, supply_map[detail.insumo_id], detail.rendimiento, detail.desperdicio_pct, detail.precio_unitario),
            )
        conn.commit()
        return {"ok": True, "apu": _apu_response(conn, apu_id)[0]}
    finally:
        conn.close()


@app.put("/api/admin/apus/{apu_id}")
def update_apu(apu_id: int, request: ApuUpdate, authorization: str | None = Header(default=None)) -> dict:
    _require_admin(authorization)
    db.inicializar_db()
    conn = db.get_conn()
    try:
        exists = conn.execute("SELECT 1 FROM apus WHERE apu_id=?", (apu_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="APU no encontrado.")
        supply_ids = [detail.insumo_id for detail in request.detalles]
        placeholders = ",".join("?" for _ in supply_ids)
        supplies = conn.execute(
            f"SELECT insumo_id, categoria FROM insumos_maestros WHERE insumo_id IN ({placeholders})",
            supply_ids,
        ).fetchall()
        supply_map = {row["insumo_id"]: row["categoria"] for row in supplies}
        missing = [supply_id for supply_id in supply_ids if supply_id not in supply_map]
        if missing:
            raise HTTPException(status_code=400, detail=f"Insumos no encontrados: {missing}")
        conn.execute(
            """UPDATE apus SET nombre_partida=?, unidad=?, descripcion=?, categoria=?,
               administracion_pct=?, imprevistos_pct=?, utilidad_pct=?, iva_pct=?, iva_base=?
               WHERE apu_id=?""",
            (request.nombre_partida.strip(), request.unidad.strip(), request.descripcion.strip() if request.descripcion else None,
             request.categoria, request.administracion_pct, request.imprevistos_pct,
             request.utilidad_pct, request.iva_pct, request.iva_base, apu_id),
        )
        conn.execute("DELETE FROM apu_detalle WHERE apu_id=?", (apu_id,))
        for detail in request.detalles:
            conn.execute(
                "INSERT INTO apu_detalle (apu_id, insumo_id, categoria, rendimiento, desperdicio_pct, precio_unitario) VALUES (?,?,?,?,?,?)",
                (apu_id, detail.insumo_id, supply_map[detail.insumo_id], detail.rendimiento, detail.desperdicio_pct, detail.precio_unitario),
            )
        conn.commit()
        return {"ok": True, "apu": _apu_response(conn, apu_id)[0]}
    finally:
        conn.close()


def _project_response(conn, project_id: int) -> dict:
    project = conn.execute("SELECT * FROM proyectos WHERE proyecto_id=?", (project_id,)).fetchone()
    if project is None:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
    rows = conn.execute(
        """SELECT pp.*, a.nombre_partida, a.unidad, a.categoria AS apu_categoria
           FROM proyecto_partidas pp JOIN apus a ON a.apu_id=pp.apu_id
          WHERE pp.proyecto_id=? ORDER BY pp.orden, pp.partida_id""",
        (project_id,),
    ).fetchall()
    start = date.fromisoformat(project["fecha_inicio"])
    current = start
    items = []
    phases = {}
    for row in rows:
        duration = max(1, int(row["duracion_dias"]))
        item_start = current
        item_end = item_start + timedelta(days=duration - 1)
        apu = _apu_response(conn, row["apu_id"])[0]
        item = dict(row)
        item.update({"fecha_inicio": item_start.isoformat(), "fecha_fin": item_end.isoformat(), "apu": apu})
        items.append(item)
        phase = phases.setdefault(row["fase"], {"fase": row["fase"], "costo_total": 0, "duracion_dias": 0, "partidas": 0})
        phase["costo_total"] += row["costo_total"]
        phase["duracion_dias"] += duration
        phase["partidas"] += 1
        current = item_end + timedelta(days=1)
    total_cost = sum(item["costo_total"] for item in items)
    total_days = sum(item["duracion_dias"] for item in items)
    result = dict(project)
    result.update({"partidas": items, "fases": list(phases.values()), "costo_total": total_cost, "duracion_dias": total_days, "fecha_fin": (current - timedelta(days=1)).isoformat() if items else project["fecha_inicio"]})
    return result


@app.post("/api/admin/proyectos")
def create_project(request: ProjectCreate, authorization: str | None = Header(default=None)) -> dict:
    _require_admin(authorization)
    db.inicializar_db()
    conn = db.get_conn()
    try:
        cursor = conn.execute("INSERT INTO proyectos (nombre, cliente, ubicacion, fecha_inicio) VALUES (?,?,?,?)", (request.nombre.strip(), request.cliente.strip() if request.cliente else None, request.ubicacion.strip() if request.ubicacion else None, request.fecha_inicio))
        conn.commit()
        return {"ok": True, "proyecto": _project_response(conn, cursor.lastrowid)}
    finally:
        conn.close()


@app.get("/api/admin/proyectos")
def list_projects(authorization: str | None = Header(default=None)) -> dict:
    _require_admin(authorization)
    db.inicializar_db()
    conn = db.get_conn()
    try:
        projects = [_project_response(conn, row["proyecto_id"]) for row in conn.execute("SELECT proyecto_id FROM proyectos ORDER BY proyecto_id DESC").fetchall()]
        return {"items": projects, "count": len(projects)}
    finally:
        conn.close()


@app.post("/api/admin/proyectos/{project_id}/partidas")
def add_project_partida(project_id: int, request: ProjectPartidaCreate, authorization: str | None = Header(default=None)) -> dict:
    _require_admin(authorization)
    db.inicializar_db()
    conn = db.get_conn()
    try:
        if conn.execute("SELECT 1 FROM proyectos WHERE proyecto_id=?", (project_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Proyecto no encontrado.")
        apu = _apu_response(conn, request.apu_id)
        if not apu:
            raise HTTPException(status_code=404, detail="APU no encontrado.")
        apu = apu[0]
        duration = max(1, math.ceil(request.cantidad / request.rendimiento_diario))
        cost = request.cantidad * apu["precio_venta"]
        conn.execute("INSERT INTO proyecto_partidas (proyecto_id, fase, apu_id, cantidad, rendimiento_diario, orden, costo_unitario, costo_total, duracion_dias) VALUES (?,?,?,?,?,?,?,?,?)", (project_id, request.fase.strip(), request.apu_id, request.cantidad, request.rendimiento_diario, request.orden, apu["precio_venta"], cost, duration))
        conn.commit()
        return {"ok": True, "proyecto": _project_response(conn, project_id)}
    finally:
        conn.close()


@app.post("/api/pipeline/process")
async def process_pipeline(
    mode: str = Form(default="preview"),
    files: list[UploadFile] = File(...),
    authorization: str | None = Header(default=None),
) -> dict:
    """Previsualiza o procesa PDFs cargados desde el harness."""
    _require_admin(authorization)
    if mode not in {"preview", "auto"}:
        raise HTTPException(status_code=400, detail="mode debe ser preview o auto")
    db.inicializar_db()
    logger.info("pipeline.start mode=%s file_count=%s", mode, len(files))
    conn = db.get_conn()
    results = []
    try:
        for uploaded in files:
            if not uploaded.filename or Path(uploaded.filename).suffix.lower() not in {".pdf", ".zip", ".xml"}:
                results.append({"status": "error", "file": uploaded.filename or "sin-nombre", "error": "Solo se aceptan archivos PDF, ZIP o XML DIAN"})
                continue
            suffix = Path(uploaded.filename).suffix.lower()
            temporary = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            temporary.write(await uploaded.read())
            temporary.close()
            temporary_path = Path(temporary.name)
            try:
                if mode == "preview":
                    invoice, items = parsear_factura(str(temporary_path))
                    results.append(
                        {
                            "status": "preview",
                            "file": uploaded.filename,
                            "invoice": invoice.get("numero_factura"),
                            "provider": invoice.get("proveedor_nombre"),
                            "items": len(items),
                            "total": invoice.get("total_pagar"),
                            "diagnostics": diagnosticar_extraccion(invoice, items),
                        }
                    )
                else:
                    results.append(procesar_pdf(conn, str(temporary_path), auto=True))
            except Exception as error:
                results.append(
                    {
                        "status": "error",
                        "file": uploaded.filename,
                        "error": str(error),
                        "diagnostics": {
                            "status": "failed",
                            "missing_fields": ["proveedor reconocido o parser compatible"],
                            "next_step": "Revisar el texto detectado, usar OCR o agregar parser por NIT.",
                        },
                    }
                )
            finally:
                temporary_path.unlink(missing_ok=True)
    finally:
        conn.close()
    logger.info("pipeline.end mode=%s processed=%s", mode, len(results))
    rag_index = None
    if mode == "auto":
        try:
            rag_index = build_index(provider="local", reset=True)
        except Exception as error:
            logger.exception("rag.auto_index_failed")
            rag_index = {"error": str(error)}
    return {"mode": mode, "processed": len(results), "results": results, "rag_index": rag_index}


@app.post("/api/rag/index")
def rag_index(request: IndexRequest, authorization: str | None = Header(default=None)) -> dict:
    _require_admin(authorization)
    logger.info("rag.index provider=%s model=%s reset=%s", request.provider, request.model, request.reset)
    try:
        return build_index(request.provider, request.model, request.dimension, request.reset)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/rag/query")
def rag_query(request: QueryRequest, authorization: str | None = Header(default=None)) -> dict:
    _require_admin(authorization)
    logger.info("rag.query provider=%s n_results=%s answer=%s", request.provider, request.n_results, request.answer)
    try:
        return query_index(
            request.query,
            request.provider,
            request.model,
            request.dimension,
            request.n_results,
            request.answer,
        )
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/chat")
def chat(request: ChatRequest, authorization: str | None = Header(default=None)) -> dict:
    _require_admin(authorization)
    logger.info("chat.request provider=%s history=%s", request.provider, len(request.history))
    try:
        return advisory_chat(
            request.message,
            request.history,
            request.provider,
            request.model,
            request.dimension,
            request.n_results,
            request.generation_provider,
        )
    except Exception as error:
        logger.exception("chat.error")
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/agui/architect")
async def agui_architect(request: Request, authorization: str | None = Header(default=None)) -> StreamingResponse:
    """Endpoint AG-UI mínimo: texto + plan estructurado por Server-Sent Events."""
    if authorization and not _has_valid_session(authorization):
        _require_admin(authorization)
    authenticated = _has_valid_session(authorization)
    payload = await request.json()
    messages = payload.get("messages", [])
    user_message = next(
        (item.get("content", "") for item in reversed(messages) if item.get("role") == "user"),
        "Quiero estudiar una casa para convertirla en vivienda multifamiliar.",
    )
    thread_id = payload.get("threadId", uuid4().hex)
    run_id = payload.get("runId", uuid4().hex)
    message_id = uuid4().hex

    async def event_stream():
        yield f"data: {json.dumps({'type': 'RUN_STARTED', 'threadId': thread_id, 'runId': run_id}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'TEXT_MESSAGE_START', 'messageId': message_id, 'role': 'assistant'}, ensure_ascii=False)}\n\n"
        try:
            response = await asyncio.to_thread(architecture_chat, user_message, messages, include_rag=authenticated)
            plan = build_architecture_plan(user_message)
            events = [
                {"type": "TEXT_MESSAGE_CONTENT", "messageId": message_id, "delta": response["answer"]},
                {"type": "TEXT_MESSAGE_END", "messageId": message_id},
                {"type": "CUSTOM", "name": "architecture_plan", "value": plan},
                {"type": "RUN_FINISHED", "threadId": thread_id, "runId": run_id},
            ]
            for event in events:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0)
        except Exception as error:
            logger.exception("agui.error")
            event = {"type": "RUN_ERROR", "message": str(error)}
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/report/excel")
def report_excel(authorization: str | None = Header(default=None)) -> FileResponse:
    _require_admin(authorization)
    logger.info("finance.excel requested")
    try:
        output = generar_excel()
        return FileResponse(output, filename=output.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/")
def atlas() -> FileResponse:
    frontend = FRONTEND_DIST / "index.html"
    return FileResponse(frontend if frontend.exists() else ATLAS_PATH)


@app.get("/panel")
@app.get("/app")
def panel() -> FileResponse:
    frontend = FRONTEND_DIST / "index.html"
    return FileResponse(frontend if frontend.exists() else PANEL_PATH)


@app.get("/ventas")
@app.get("/soma")
def sales() -> FileResponse:
    frontend = FRONTEND_DIST / "index.html"
    return FileResponse(frontend if frontend.exists() else ATLAS_PATH)


@app.get("/api/debug/state")
def debug_state(authorization: str | None = Header(default=None)) -> dict:
    if not DEBUG_MODE:
        raise HTTPException(status_code=404, detail="Debug desactivado")
    _require_admin(authorization)
    return {
        "debug": DEBUG_MODE,
        "verbose": VERBOSE_MODE,
        "active_sessions": len(_sessions),
        "admin_user": ADMIN_USER,
        "database": str(db.DB_PATH),
        "opencode_go": {
            "configured": bool(_opencode_go_key()),
            "base_url": os.getenv("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1"),
            "model": os.getenv("OPENCODE_GO_MODEL", DEFAULT_OPENCODE_GO_MODEL),
        },
        "chroma": index_status(),
    }
