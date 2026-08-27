"""Interfaz visual del extractor de facturas -> insumos para APUs.

Correr con:
    streamlit run app.py
"""
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

import db
from extractor import parsear_factura
from normalize import sugerir_match, UMBRAL_SUGERENCIA

st.set_page_config(page_title="Facturas -> APUs", layout="wide")
db.inicializar_db()

CATEGORIAS = ["material", "mano_obra", "equipo", "transporte", "servicio_terceros"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def guardar_temp(uploaded_file) -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.write(uploaded_file.getvalue())
    tmp.close()
    return tmp.name


def preparar_batch(uploaded_files):
    """Parsea los PDFs subidos y arma sugerencias de normalización para cada ítem.
    No toca la base de datos todavía -- eso solo pasa cuando el usuario confirma."""
    conn = db.get_conn()
    insumos = db.listar_insumos(conn)
    batch = []

    for uf in uploaded_files:
        path = guardar_temp(uf)
        try:
            factura, items = parsear_factura(path)
        except Exception as e:
            st.error(f"No se pudo leer '{uf.name}': {e}")
            continue

        ya_existe = db.factura_ya_existe(conn, factura.get("cufe"))
        suma_items = sum(i["valor_total"] for i in items)
        descuadre = None
        if factura.get("subtotal") and abs(suma_items - factura["subtotal"]) > 5:
            descuadre = suma_items - factura["subtotal"]

        items_prep = []
        for item in items:
            insumo_id, nombre_sug, score = sugerir_match(item["descripcion_cruda"], insumos)
            if insumo_id and score >= 90:
                accion_default = "usar_sugerencia"
            else:
                accion_default = "nuevo"
            items_prep.append({
                **item,
                "sugerencia_id": insumo_id,
                "sugerencia_nombre": nombre_sug,
                "score": score,
                "accion": accion_default,
                "nombre_final": nombre_sug if accion_default == "usar_sugerencia" else " ".join(item["descripcion_cruda"].split()),
                "categoria_final": "servicio_terceros" if "SERVICIO" in item["descripcion_cruda"].upper() else "material",
                "unidad_final": item.get("unidad_medida") or "",
            })

        batch.append({
            "archivo": uf.name,
            "factura": factura,
            "items": items_prep,
            "ya_existe": ya_existe,
            "descuadre": descuadre,
            "guardado": False,
        })
    conn.close()
    return batch


def guardar_en_bd(batch):
    conn = db.get_conn()
    guardadas = 0
    for factura_wrap in batch:
        if factura_wrap["guardado"] or factura_wrap["ya_existe"]:
            continue
        factura_id = db.insertar_factura(conn, factura_wrap["factura"])
        for item in factura_wrap["items"]:
            if item["accion"] == "usar_sugerencia" and item["sugerencia_id"]:
                insumo_id = item["sugerencia_id"]
            else:
                insumo_id = db.crear_insumo(
                    conn, item["nombre_final"], item["categoria_final"], item["unidad_final"] or None
                )
            db.agregar_alias(conn, insumo_id, item["descripcion_cruda"], factura_wrap["factura"]["proveedor_nit"])
            item_id = db.insertar_item(conn, factura_id, item)
            db.asignar_insumo_item(conn, item_id, insumo_id)
        factura_wrap["guardado"] = True
        guardadas += 1
    conn.commit()
    conn.close()
    return guardadas


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("Facturas de construcción -> insumos para APUs")

tab_cargar, tab_insumos, tab_mensual = st.tabs(["Cargar facturas", "Diccionario de insumos", "Gasto mensual"])

# --- TAB 1: cargar y confirmar --------------------------------------------
with tab_cargar:
    uploaded = st.file_uploader("Sube una o varias facturas en PDF", type="pdf", accept_multiple_files=True)

    if uploaded and st.button("Procesar facturas", type="primary"):
        st.session_state.batch = preparar_batch(uploaded)

    if "batch" in st.session_state and st.session_state.batch:
        for fi, factura_wrap in enumerate(st.session_state.batch):
            factura = factura_wrap["factura"]
            with st.container(border=True):
                st.subheader(f"{factura['proveedor_nombre']} — factura {factura['numero_factura']}")

                if factura_wrap["ya_existe"]:
                    st.info("Esta factura ya estaba guardada (mismo CUFE). Se omite.")
                    continue
                if factura_wrap["guardado"]:
                    st.success("Guardada en la base de datos.")
                    continue
                if factura_wrap["descuadre"] is not None:
                    st.warning(
                        f"⚠ La suma de ítems no cuadra con el subtotal declarado "
                        f"(diferencia: ${factura_wrap['descuadre']:,.0f}). Revisa la extracción."
                    )

                st.caption(
                    f"Fecha: {factura['fecha_factura']} | Total: ${factura['total_pagar']:,.0f} "
                    + (f"| Proyecto: {factura['proyecto']}" if factura.get("proyecto") else "")
                )

                for ii, item in enumerate(factura_wrap["items"]):
                    cols = st.columns([3, 1, 1.3, 2.2, 1.5])
                    cols[0].markdown(f"**{item['descripcion_cruda']}**")
                    cols[1].write(f"cant: {item['cantidad']:g}")
                    cols[2].write(f"$/u: {item['valor_unitario']:,.0f}")

                    key_base = f"f{fi}_i{ii}"
                    if item["sugerencia_id"]:
                        opciones = [f"Usar '{item['sugerencia_nombre']}' (score {item['score']:.0f})", "Es un insumo nuevo"]
                        eleccion = cols[3].radio(
                            "match", opciones, key=f"{key_base}_radio", label_visibility="collapsed"
                        )
                        item["accion"] = "usar_sugerencia" if eleccion == opciones[0] else "nuevo"
                    else:
                        cols[3].caption("Sin coincidencia -- se crea como insumo nuevo")
                        item["accion"] = "nuevo"

                    if item["accion"] == "nuevo":
                        item["nombre_final"] = cols[4].text_input(
                            "nombre", value=item["nombre_final"], key=f"{key_base}_nombre", label_visibility="collapsed"
                        )
                        item["categoria_final"] = st.selectbox(
                            "categoría", CATEGORIAS,
                            index=CATEGORIAS.index(item["categoria_final"]),
                            key=f"{key_base}_cat",
                        )

                st.divider()

        if st.button("Guardar todo en la base de datos", type="primary"):
            n = guardar_en_bd(st.session_state.batch)
            st.success(f"{n} factura(s) guardada(s).")
            st.rerun()

# --- TAB 2: diccionario de insumos -----------------------------------------
with tab_insumos:
    conn = db.get_conn()
    insumos = conn.execute(
        "SELECT insumo_id, nombre_normalizado, categoria, unidad_estandar FROM insumos_maestros ORDER BY categoria, nombre_normalizado"
    ).fetchall()

    if not insumos:
        st.info("Todavía no hay insumos guardados. Procesa alguna factura primero.")
    else:
        filas = []
        for ins in insumos:
            hist = conn.execute(
                "SELECT * FROM v_insumo_precio_historico WHERE insumo_id=?", (ins["insumo_id"],)
            ).fetchall()
            precio_prom = sum(h["precio_promedio"] for h in hist) / len(hist) if hist else None
            proveedores = ", ".join(h["proveedor_nombre"] for h in hist)
            filas.append({
                "Insumo": ins["nombre_normalizado"],
                "Categoría": ins["categoria"],
                "Unidad": ins["unidad_estandar"] or "-",
                "Precio promedio": f"${precio_prom:,.0f}" if precio_prom else "-",
                "Proveedores": proveedores or "-",
            })
        df = pd.DataFrame(filas)
        categoria_filtro = st.multiselect("Filtrar por categoría", CATEGORIAS)
        if categoria_filtro:
            df = df[df["Categoría"].isin(categoria_filtro)]
        st.dataframe(df, use_container_width=True, hide_index=True)
    conn.close()

# --- TAB 3: gasto mensual ---------------------------------------------------
with tab_mensual:
    conn = db.get_conn()
    filas = conn.execute("SELECT * FROM v_gasto_mensual").fetchall()
    if not filas:
        st.info("Todavía no hay facturas guardadas.")
    else:
        df = pd.DataFrame([dict(r) for r in filas])
        resumen_mes = df.groupby("mes")["total_pagado"].sum()
        st.bar_chart(resumen_mes)

        st.subheader("Detalle por proveedor y proyecto")
        st.dataframe(
            df.rename(columns={
                "mes": "Mes", "proveedor_nombre": "Proveedor", "proyecto": "Proyecto",
                "num_facturas": "# Facturas", "gasto_materiales": "Materiales",
                "iva_total": "IVA", "total_pagado": "Total pagado",
            }),
            use_container_width=True, hide_index=True,
        )

        st.subheader("Gasto por categoría de insumo")
        filas_cat = conn.execute("SELECT * FROM v_gasto_mensual_categoria").fetchall()
        if filas_cat:
            df_cat = pd.DataFrame([dict(r) for r in filas_cat])
            pivot = df_cat.pivot(index="mes", columns="categoria", values="gasto").fillna(0)
            st.bar_chart(pivot)
    conn.close()
