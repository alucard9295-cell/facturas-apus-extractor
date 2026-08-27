"""Extrae metadata + ítems de facturas de construcción en PDF.

Cada proveedor tiene su propio layout, así que se identifica el proveedor
por su NIT (que siempre aparece en el texto) y se aplica un parser dedicado.
Para un proveedor nuevo, se agrega una función `_parse_<proveedor>` y una
entrada en PROVEEDORES.
"""
import re
import io
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pdfplumber
from numeros import parse_number
from fechas import parse_fecha


def extraer_texto_ocr(pdf_path: str) -> str:
    """Intenta leer PDFs escaneados con RapidOCR y PyMuPDF."""
    try:
        import fitz
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return ""

    ocr = RapidOCR()
    pages = []
    with fitz.open(pdf_path) as document:
        for page in document:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
            result, _ = ocr(image)
            if result:
                pages.extend(str(item[1]) for item in result if len(item) > 1 and item[1])
    return "\n".join(pages)


def extraer_texto(pdf_path: str) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        texto = "\n".join(p.extract_text() or "" for p in pdf.pages)
    return texto if texto.strip() else extraer_texto_ocr(pdf_path)


# ---------------------------------------------------------------------------
# Parser: ELEQUIPOS SAS
# ---------------------------------------------------------------------------
def _parse_elequipos(texto: str, archivo: str) -> dict:
    factura = {
        "proveedor_nombre": "ELEQUIPOS SAS",
        "proveedor_nit": "901464983-4",
        "archivo_origen": archivo,
    }
    factura["numero_factura"] = re.search(r"Factura Electrónica De Venta No\s+(\S+\s*\S*\d)", texto).group(1).strip()
    factura["cliente_nombre"] = re.search(r"CLIENTE\s+(.+)", texto).group(1).strip()
    factura["cliente_nit"] = re.search(r"NIT\s+([\d.\- ]+\d)\s+FORMA", texto).group(1).strip()
    factura["forma_pago"] = re.search(r"FORMA DE PAGO\s+(\S+)", texto).group(1)
    m = re.search(r"FECHA FACTURA\s*\n?.*?(\d{2}/\d{2}/\d{4})", texto)
    factura["fecha_factura"] = m.group(1) if m else None
    m = re.search(r"FECHA VENCIMIENTO\s*\n?.*?(\d{2}/\d{2}/\d{4})", texto)
    factura["fecha_vencimiento"] = m.group(1) if m else None
    m = re.search(r"PROYECTO\s+(.+)", texto)
    factura["proyecto"] = m.group(1).strip() if m else None
    factura["subtotal"] = parse_number(re.search(r"SUBTOTAL\s+([\d.,]+)", texto).group(1))
    factura["descuento"] = parse_number(re.search(r"DESCUENTO\s+([\d.,]+)", texto).group(1))
    factura["iva"] = parse_number(re.search(r"\bIVA\s+([\d.,]+)", texto).group(1))
    factura["retefuente"] = parse_number(re.search(r"RETEFUENTE\s+([\d.,]+)", texto).group(1))
    factura["reteiva"] = parse_number(re.search(r"RETEIVA\s+([\d.,]+)", texto).group(1))
    factura["reteica"] = parse_number(re.search(r"RETEICA\s+([\d.,]+)", texto).group(1))
    factura["total_pagar"] = parse_number(re.search(r"TOTAL A PAGAR\s+([\d.,]+)", texto).group(1))
    factura["medio_pago"] = None
    m = re.search(r"CUFE:\s*([a-f0-9]+)", texto)
    factura["cufe"] = m.group(1) if m else None

    items = []
    # patrón: item codigo descripcion cantidad unidad valor_unit valor_total
    for m in re.finditer(
        r"^\d+\s+(\S+)\s+(.+?)\s+(\d+)\s+(\S+\.?)\s+([\d.,]+)\s+([\d.,]+)\s*$",
        texto, re.MULTILINE,
    ):
        codigo, desc, cant, unidad, vunit, vtotal = m.groups()
        items.append({
            "codigo_proveedor": codigo,
            "descripcion_cruda": desc.strip(),
            "unidad_medida": unidad.rstrip("."),
            "cantidad": parse_number(cant),
            "valor_unitario": parse_number(vunit),
            "valor_total": parse_number(vtotal),
        })
    return factura, items


# ---------------------------------------------------------------------------
# Parser: EL TRIANGULO COLOMBIA SAS
# ---------------------------------------------------------------------------
def _parse_triangulo(texto: str, archivo: str) -> dict:
    factura = {
        "proveedor_nombre": "EL TRIANGULO COLOMBIA SAS",
        "proveedor_nit": "900409224-5",
        "archivo_origen": archivo,
    }
    factura["numero_factura"] = re.search(r"\.?(B\d\s*-\s*\d+)", texto).group(1).replace(" ", "")
    factura["cliente_nombre"] = re.search(r"CLIENTE\s+(.+?)\s+FECHA FACTURA", texto).group(1).strip()
    factura["cliente_nit"] = re.search(r"NIT ó C\.C\.\s+([\d.\- ]+\d)", texto).group(1).strip()
    factura["forma_pago"] = re.search(r"FORMA DE PAGO\s+(\S+)", texto).group(1)
    factura["fecha_factura"] = re.search(r"FECHA FACTURA\s+([\d\-A-Za-z]+)", texto).group(1)
    factura["fecha_vencimiento"] = re.search(r"FECHA VCTTO\s+([\d\-A-Za-z]+)", texto).group(1)
    factura["proyecto"] = None
    factura["subtotal"] = parse_number(re.search(r"SUB TOTAL\s+([\d.,]+)", texto).group(1))
    factura["iva"] = parse_number(re.search(r"\bIVA\s+([\d.,]+)", texto).group(1))
    factura["descuento"] = parse_number(re.search(r"DESCUENTO\s+([\d.,]+)", texto).group(1))
    factura["retefuente"] = parse_number(re.search(r"RETEFUENTE\s+([\d.,]+)", texto).group(1))
    factura["reteiva"] = parse_number(re.search(r"RETE\s*-\s*IVA\s+([\d.,]+)", texto).group(1))
    factura["reteica"] = parse_number(re.search(r"RETE\s*-\s*ICA\s+([\d.,]+)", texto).group(1))
    factura["total_pagar"] = parse_number(re.search(r"TOTAL FACTURA\s+([\d.,]+)", texto).group(1))
    m = re.search(r"Medios de Pago:\s*(.+)", texto)
    factura["medio_pago"] = m.group(1).strip() if m else None
    m = re.search(r"CUFE:\s*([a-f0-9]+)", texto)
    factura["cufe"] = m.group(1) if m else None

    items = []
    # patrón: item cantidad descripcion dcto valor_unit valor_total
    for m in re.finditer(
        r"^(\d+)\s+(\d+)\s+(.+?)\s+(\d+)\s+([\d.,]+)\s+([\d.,]+)\s*$",
        texto, re.MULTILINE,
    ):
        _, cant, desc, dcto, vunit, vtotal = m.groups()
        items.append({
            "codigo_proveedor": None,
            "descripcion_cruda": desc.strip(),
            "unidad_medida": None,
            "cantidad": parse_number(cant),
            "valor_unitario": parse_number(vunit),
            "valor_total": parse_number(vtotal),
            "descuento": parse_number(dcto),
        })
    return factura, items


# ---------------------------------------------------------------------------
# Parser: CREACIONES MUNDO YESOS LTDA
# ---------------------------------------------------------------------------
def _parse_yesos(texto: str, archivo: str) -> dict:
    factura = {
        "proveedor_nombre": "CREACIONES MUNDO YESOS LTDA",
        "proveedor_nit": "900344425-8",
        "archivo_origen": archivo,
    }
    factura["numero_factura"] = re.search(r"DE VENTA No\.\s*(\S+)", texto).group(1)
    factura["cliente_nombre"] = re.search(r"Cliente:\s+(.+?)\s+Fecha:", texto).group(1).strip()
    factura["cliente_nit"] = re.search(r"NIT O C\.C:\s+([\d\- ]+\d)", texto).group(1).strip()
    factura["forma_pago"] = re.search(r"Forma de pago:\s+(\S+)", texto).group(1)
    factura["medio_pago"] = re.search(r"Medio de Pago:\s+(\S+)", texto).group(1)
    factura["fecha_factura"] = re.search(r"Fecha:\s+(\d{2}/\d{2}/\d{4})", texto).group(1)
    factura["fecha_vencimiento"] = re.search(r"Vencimiento:\s+(\d{2}/\d{2}/\d{4})", texto).group(1)
    factura["proyecto"] = None
    factura["subtotal"] = parse_number(re.search(r"VALOR EN LETRAS TOTAL\s+([\d.,]+)", texto).group(1))
    factura["iva"] = parse_number(re.search(r"I\.V\.A\s+([\d.,]+)", texto).group(1))
    factura["descuento"] = 0.0
    factura["retefuente"] = parse_number(re.search(r"RETEFUENTE\s+([\d.,]+)", texto).group(1))
    factura["reteiva"] = parse_number(re.search(r"RETEIVA\s+([\d.,]+)", texto).group(1))
    factura["reteica"] = parse_number(re.search(r"RETEICA\s+([\d.,]+)", texto).group(1))
    factura["total_pagar"] = parse_number(re.search(r"([\d.,]+)\s*\nTOTAL A PAGAR", texto).group(1))
    m = re.search(r"CUFE:\s*([a-f0-9]+)", texto)
    factura["cufe"] = m.group(1) if m else None

    items = []
    # patrón: referencia descripcion cantidad precio_unit %iva valor_total
    for m in re.finditer(
        r"^(\S+)\s+(.+?)\s+(\d+)\s+([\d,]+\.\d{2})\s+([\d.]+)\s+([\d,]+\.\d{2})\s*$",
        texto, re.MULTILINE,
    ):
        codigo, desc, cant, vunit, _iva_pct, vtotal = m.groups()
        items.append({
            "codigo_proveedor": codigo,
            "descripcion_cruda": desc.strip(),
            "unidad_medida": None,
            "cantidad": parse_number(cant),
            "valor_unitario": parse_number(vunit),
            "valor_total": parse_number(vtotal),
        })
    return factura, items


def _parse_homecenter(texto: str, archivo: str) -> dict:
    factura = {
        "proveedor_nombre": "SODIMAC COLOMBIA S.A. (HOMECENTER)",
        "proveedor_nit": "800242106-2",
        "archivo_origen": archivo,
    }
    factura["numero_factura"] = re.search(r"N°\s*(\d+)", texto).group(1)
    factura["cliente_nombre"] = re.search(r"RAZ[ÓO]N SOCIAL\s*:(.+?)\s+FECHA DE EXPEDICI[ÓO]N", texto).group(1).strip()
    factura["cliente_nit"] = re.search(r"NIT\s*:([\d.\-]+)\s+FECHA DE VENCIMIENTO", texto).group(1)
    factura["fecha_factura"] = re.search(r"FECHA DE EXPEDICI[ÓO]N\s*:(\d{4}/\d{2}/\d{2})", texto).group(1)
    factura["fecha_vencimiento"] = re.search(r"FECHA DE VENCIMIENTO\s*:(\d{4}/\d{2}/\d{2})", texto).group(1)
    factura["forma_pago"] = re.search(r"FORMA DE PAGO\s*:(\S+)", texto).group(1)
    factura["medio_pago"] = re.search(r"MEDIO DE PAGO\s*:(.+)", texto).group(1).strip()
    factura["proyecto"] = None
    m = re.search(r"^SUB\.TOTAL\s+\$\s*([\d.,]+)\s*$", texto, re.MULTILINE)
    factura["subtotal"] = parse_number(m.group(1)) if m else None
    m = re.search(r"^IVA\s+\$\s*([\d.,]+)\s*$", texto, re.MULTILINE)
    factura["iva"] = parse_number(m.group(1)) if m else 0.0
    m = re.search(r"^DESCUENTO\s+\$\s*([\d.,]+)\s*$", texto, re.MULTILINE)
    factura["descuento"] = parse_number(m.group(1)) if m else 0.0
    factura["retefuente"] = 0.0
    factura["reteiva"] = 0.0
    factura["reteica"] = 0.0
    m = re.search(r"TOTAL A PAGAR\s+\$\s*([\d.,]+)", texto)
    factura["total_pagar"] = parse_number(m.group(1)) if m else None
    m = re.search(r"CUFE\s*:\s*([a-f0-9]+)", texto)
    factura["cufe"] = m.group(1) if m else None

    items = []
    # patrón: item cantidad sku[espacio-opcional]descripcion vr_unit vr_bruto %desc vr_desc subtotal %iva vr_iva subtotal_con_impto
    patron = re.compile(
        r"^(\d+)\s+(\d+)\s+(\d+)\s*(.+?)\s+"
        r"([\d.]+,\d{2})\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s+"
        r"([\d.]+,\d{2})\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s*$",
        re.MULTILINE,
    )
    for m in patron.finditer(texto):
        _, cant, sku, desc, vr_unit, _vr_bruto, _pct_desc, vr_desc, subtotal, _pct_iva, _vr_iva, _subtotal_impto = m.groups()
        items.append({
            "codigo_proveedor": sku,
            "descripcion_cruda": desc.strip(),
            "unidad_medida": None,
            "cantidad": parse_number(cant),
            "valor_unitario": parse_number(vr_unit),
            "valor_total": parse_number(subtotal),
            "descuento": parse_number(vr_desc),
        })
    return factura, items


# ---------------------------------------------------------------------------
# Parser: FERRETERIA CONSTRUCTIVA Y DEPOSITO DE MATERIALES SAS
# ---------------------------------------------------------------------------
def _parse_ferreteria_constructiva(texto: str, archivo: str) -> dict:
    factura = {
        "proveedor_nombre": "FERRETERIA CONSTRUCTIVA Y DEPOSITO DE MATERIALES SAS",
        "proveedor_nit": "901649012",
        "archivo_origen": archivo,
    }
    m = re.search(r"^(FYD\s*-\s*\d+)\s*$", texto, re.MULTILINE)
    factura["numero_factura"] = m.group(1).replace(" ", "") if m else None
    factura["cliente_nombre"] = re.search(r"Razón Social\s+.+?\s+Razón Social\s+(.+)", texto).group(1).strip()
    factura["cliente_nit"] = re.search(r"NIT\s+\d+\s+NIT\s+(\d+)", texto).group(1)
    factura["forma_pago"] = re.search(r"Forma de Pago\s+(\S+)", texto).group(1)
    m = re.search(r"Medio de Pago\s+(.+)", texto)
    factura["medio_pago"] = m.group(1).strip() if m else None
    m = re.search(r"Fecha de Generación\s+(\d{2}/\d{2}/\d{4})", texto)
    factura["fecha_factura"] = m.group(1) if m else None
    m = re.search(r"Fecha de Vencimiento\s+(\d{2}/\d{2}/\d{4})", texto)
    factura["fecha_vencimiento"] = m.group(1) if m else None
    factura["proyecto"] = None
    m = re.search(r"Subtotal\s+\$([\d.,]+)", texto)
    factura["subtotal"] = parse_number(m.group(1)) if m else None
    m = re.search(r"^IVA\s+[\d.,]+%\s+\$([\d.,]+)\s*$", texto, re.MULTILINE)
    factura["iva"] = parse_number(m.group(1)) if m else 0.0
    factura["descuento"] = 0.0
    factura["retefuente"] = 0.0
    factura["reteiva"] = 0.0
    factura["reteica"] = 0.0
    m = re.search(r"Total a Pagar\s+\$([\d.,]+)", texto)
    factura["total_pagar"] = parse_number(m.group(1)) if m else None
    m = re.search(r"CUFE:\s*([a-f0-9]+)", texto)
    factura["cufe"] = m.group(1) if m else None

    items = []
    # patrón: no ref descripcion cant um $precio [IVA xx%] $subtotal $total_item
    patron = re.compile(
        r"^(\d+)\s+(\S+)\s+(.+?)\s+(\d+)\s+(\S+)\s+\$([\d.,]+)\s*"
        r"(?:IVA\s+[\d.,]+%)?\s*\$([\d.,]+)\s+\$([\d.,]+)\s*$",
        re.MULTILINE,
    )
    for m in patron.finditer(texto):
        _, ref, desc, cant, um, precio, subtotal, _total_item = m.groups()
        items.append({
            "codigo_proveedor": ref,
            "descripcion_cruda": desc.strip(),
            "unidad_medida": um,
            "cantidad": parse_number(cant),
            "valor_unitario": parse_number(precio),
            "valor_total": parse_number(subtotal),
        })
    return factura, items


# ---------------------------------------------------------------------------
# Parser generico DIAN (UBL XML)
# ---------------------------------------------------------------------------
def _xml_local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_text(element, name: str, default=None):
    for child in element.iter():
        if _xml_local(child.tag) == name and (child.text or "").strip():
            return child.text.strip()
    return default


def _xml_children_text(element, name: str) -> list[str]:
    return [
        child.text.strip()
        for child in list(element)
        if _xml_local(child.tag) == name and (child.text or "").strip()
    ]


def _xml_find_child(element, name: str):
    for child in list(element):
        if _xml_local(child.tag) == name:
            return child
    return None


def _xml_find_invoice(root):
    if _xml_local(root.tag) in {"Invoice", "CreditNote"}:
        return root
    for element in root.iter():
        if _xml_local(element.tag) in {"Invoice", "CreditNote"}:
            return element
        text = (element.text or "").lstrip()
        if text.startswith("<?xml") and ("<Invoice" in text or "<CreditNote" in text):
            try:
                embedded = ET.fromstring(text.encode("utf-8"))
            except ET.ParseError:
                continue
            if _xml_local(embedded.tag) in {"Invoice", "CreditNote"}:
                return embedded
    return None


def _xml_party(invoice, party_name: str) -> tuple[str | None, str | None]:
    for element in invoice.iter():
        if _xml_local(element.tag) != party_name:
            continue
        party = _xml_find_child(element, "Party") or element
        name = _xml_text(party, "RegistrationName") or _xml_text(party, "Name")
        nit = _xml_text(party, "CompanyID") or _xml_text(party, "ID")
        return name, nit
    return None, None


def _xml_parse_document(xml_bytes: bytes, archivo: str):
    root = ET.fromstring(xml_bytes)
    invoice = _xml_find_invoice(root)
    if invoice is None:
        raise ValueError("El XML no contiene una factura UBL DIAN reconocible.")

    proveedor_nombre, proveedor_nit = _xml_party(invoice, "AccountingSupplierParty")
    cliente_nombre, cliente_nit = _xml_party(invoice, "AccountingCustomerParty")
    invoice_id = _xml_children_text(invoice, "ID")
    uuid = _xml_text(invoice, "UUID")
    issue_date = _xml_text(invoice, "IssueDate")
    monetary = _xml_find_child(invoice, "LegalMonetaryTotal") or invoice
    subtotal = _xml_text(monetary, "TaxExclusiveAmount")
    total = _xml_text(monetary, "PayableAmount") or _xml_text(monetary, "TaxInclusiveAmount")

    taxes = []
    for element in invoice.iter():
        if _xml_local(element.tag) == "TaxTotal":
            amount = _xml_text(element, "TaxAmount")
            if amount:
                taxes.append(parse_number(amount))

    factura = {
        "proveedor_nombre": proveedor_nombre or "Proveedor no identificado",
        "proveedor_nit": proveedor_nit or "",
        "archivo_origen": archivo,
        "numero_factura": invoice_id[0] if invoice_id else None,
        "cliente_nombre": cliente_nombre,
        "cliente_nit": cliente_nit,
        "forma_pago": _xml_text(invoice, "PaymentMeansCode"),
        "medio_pago": _xml_text(invoice, "InstructionID") or _xml_text(invoice, "InstructionNote"),
        "fecha_factura": parse_fecha(issue_date),
        "fecha_vencimiento": parse_fecha(_xml_text(invoice, "DueDate")),
        "proyecto": None,
        "subtotal": parse_number(subtotal) if subtotal else None,
        "iva": sum(taxes),
        "descuento": 0.0,
        "retefuente": 0.0,
        "reteiva": 0.0,
        "reteica": 0.0,
        "total_pagar": parse_number(total) if total else None,
        "cufe": uuid,
    }

    items = []
    line_tag = "CreditNoteLine" if _xml_local(invoice.tag) == "CreditNote" else "InvoiceLine"
    quantity_tag = "CreditedQuantity" if line_tag == "CreditNoteLine" else "InvoicedQuantity"
    for line in invoice.iter():
        if _xml_local(line.tag) != line_tag:
            continue
        quantity_element = next((x for x in line.iter() if _xml_local(x.tag) == quantity_tag), None)
        quantity = (quantity_element.text or "").strip() if quantity_element is not None else None
        description = _xml_text(line, "Description")
        line_total = _xml_text(line, "LineExtensionAmount")
        price = _xml_text(line, "PriceAmount")
        code = _xml_text(line, "ID")
        items.append({
            "codigo_proveedor": code,
            "descripcion_cruda": description or code or "Ítem sin descripción",
            "unidad_medida": quantity_element.attrib.get("unitCode") if quantity_element is not None else None,
            "cantidad": parse_number(quantity) if quantity else None,
            "valor_unitario": parse_number(price) if price else None,
            "valor_total": parse_number(line_total) if line_total else None,
        })
    return factura, items


def _xml_from_zip(data: bytes) -> bytes | None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml_names = [name for name in archive.namelist() if name.lower().endswith(".xml")]
        for name in xml_names:
            candidate = archive.read(name)
            try:
                root = ET.fromstring(candidate)
            except ET.ParseError:
                continue
            if _xml_find_invoice(root) is not None:
                return candidate
    return None


def _parse_generico_pdf(texto: str, archivo: str):
    """Fallback para PDFs nuevos cuando no existe un parser de proveedor."""
    compact = " ".join(texto.split())
    nit_match = re.search(r"\bNIT\.?\s*[:.]?\s*([\d. -]{6,}\d)", compact, re.IGNORECASE)
    proveedor_nit = nit_match.group(1).strip() if nit_match else ""
    provider_match = re.search(r"(?:FACTURA[^\n]{0,80})\s+(.+?)\s+NIT\b", texto, re.IGNORECASE)
    proveedor_nombre = provider_match.group(1).strip(" :-") if provider_match else "Proveedor no identificado"

    def field(pattern):
        match = re.search(pattern, texto, re.IGNORECASE | re.MULTILINE)
        return parse_number(match.group(1)) if match else None

    number_match = re.search(r"(?:FACTURA|FACTURA ELECTR[ÓO]NICA|NOTA CR[ÉE]DITO)[^\n]*?(?:No\.?|N[°º])\s*([A-Z0-9-]+)", texto, re.IGNORECASE)
    date_match = re.search(r"(?:FECHA(?: DE EXPEDICI[ÓO]N| FACTURA)?|FECHA:)\s*[: ]\s*(\d{2}[/-]\d{2}[/-]\d{4}|\d{4}-\d{2}-\d{2})", texto, re.IGNORECASE)
    subtotal = field(r"(?:SUBTOTAL|SUB TOTAL|VALOR\s+ANTES\s+DE\s+IVA)\s*[:$ ]+([\d.,]+)")
    iva = field(r"\bIVA\b\s*[:$ ]+([\d.,]+)") or 0.0
    total = field(r"(?:TOTAL\s+A\s+PAGAR|TOTAL\s+FACTURA)\s*[:$ ]+([\d.,]+)")
    factura = {
        "proveedor_nombre": proveedor_nombre,
        "proveedor_nit": proveedor_nit,
        "archivo_origen": archivo,
        "numero_factura": number_match.group(1) if number_match else None,
        "cliente_nombre": None,
        "cliente_nit": None,
        "forma_pago": None,
        "medio_pago": None,
        "fecha_factura": parse_fecha(date_match.group(1)) if date_match else None,
        "fecha_vencimiento": None,
        "proyecto": None,
        "subtotal": subtotal,
        "iva": iva,
        "descuento": 0.0,
        "retefuente": 0.0,
        "reteiva": 0.0,
        "reteica": 0.0,
        "total_pagar": total,
        "cufe": (re.search(r"CUFE\s*:?\s*([a-f0-9]{20,})", texto, re.IGNORECASE) or [None, None])[1],
    }
    return factura, []


def _profile_invoice(texto: str, archivo: str, proveedor: str, nit: str, number_pattern: str,
                     date_pattern: str, subtotal_pattern: str, iva_pattern: str,
                     total_pattern: str, item_parser):
    def find(pattern, default=None):
        match = re.search(pattern, texto, re.IGNORECASE | re.MULTILINE)
        return match.group(1).strip() if match else default

    factura = {
        "proveedor_nombre": proveedor,
        "proveedor_nit": nit,
        "archivo_origen": archivo,
        "numero_factura": find(number_pattern),
        "cliente_nombre": None,
        "cliente_nit": None,
        "forma_pago": None,
        "medio_pago": None,
        "fecha_factura": parse_fecha(find(date_pattern)),
        "fecha_vencimiento": None,
        "proyecto": None,
        "subtotal": parse_number(find(subtotal_pattern, "0")),
        "iva": parse_number(find(iva_pattern, "0")),
        "descuento": 0.0,
        "retefuente": 0.0,
        "reteiva": 0.0,
        "reteica": 0.0,
        "total_pagar": parse_number(find(total_pattern, "0")),
        "cufe": find(r"(?:CUFE|CUDE)\s*:?\s*([a-f0-9]{20,})"),
    }
    return factura, item_parser(texto)


def _profile_disenos_items(texto: str):
    items = []
    pattern = re.compile(r"^(\d+)\s+(.+?)\s+([\d.,]+)\s+\d+%\s+([\d.,]+)$", re.MULTILINE)
    for match in pattern.finditer(texto):
        _, description, unit_price, total = match.groups()
        items.append({"codigo_proveedor": None, "descripcion_cruda": description.strip(), "unidad_medida": None,
                      "cantidad": 1.0, "valor_unitario": parse_number(unit_price), "valor_total": parse_number(total)})
    return items


def _profile_simetrica_items(texto: str):
    items = []
    pattern = re.compile(r"^(\d+)\s+(\S+)\s+(\S+)\s+([\d.,]+)\s+\$\s*([\d.,]+).*?\$\s*([\d.,]+)\s*$", re.MULTILINE)
    for match in pattern.finditer(texto):
        section_start = texto.lower().rfind("detalles de productos", 0, match.start())
        lines = texto[section_start:match.start()].splitlines() if section_start >= 0 else texto[:match.start()].splitlines()
        candidates = [line.strip() for line in lines[-6:] if line.strip() and not re.match(r"^\d+\s", line.strip())]
        candidates = [line for line in candidates if not re.search(r"Nro\.?|C[óo]digo|Descripci[óo]n|Cantidad|Precio|Impuestos|detalle venta", line, re.IGNORECASE)]
        description = " ".join(candidates[-3:])
        after = texto[match.end():].splitlines()
        if after and after[0].strip():
            description += " " + after[0].strip()
        description = re.sub(r".*(?:detalles de productos|IMPUESTOS)\s*", "", description, flags=re.IGNORECASE).strip()
        description = re.sub(r"(?:Precio|Descuento|unitario|Nro\.?|C[óo]digo|Descripci[óo]n|U/M|Cantidad).*", "", description, flags=re.IGNORECASE).strip()
        _, code, unit, quantity, unit_price, total = match.groups()
        if not description or description == code:
            fallback = re.search(r"detalle venta\s*\n(.+?)\n(.+?)\n\d+\s+" + re.escape(code) + r".*?\n([^\n]+)", texto, re.IGNORECASE | re.DOTALL)
            if fallback:
                description = " ".join(fallback.groups())
        items.append({"codigo_proveedor": code, "descripcion_cruda": description or code, "unidad_medida": unit,
                      "cantidad": parse_number(quantity), "valor_unitario": parse_number(unit_price), "valor_total": parse_number(total)})
    return items


def _profile_cadena_items(texto: str):
    items = []
    pattern = re.compile(r"^(\d+)\s+(\S+)\s+([\d.,]+)\s+(\S+)\s+(.+?)\s+([\d.,]+)\s+IVA\s+[\d.,]+\s+[\d.,]+\s+([\d.,]+)$", re.MULTILINE)
    for match in pattern.finditer(texto):
        _, code, quantity, unit, description, unit_price, _total = match.groups()
        items.append({"codigo_proveedor": code, "descripcion_cruda": description.strip(), "unidad_medida": unit,
                      "cantidad": parse_number(quantity), "valor_unitario": parse_number(unit_price), "valor_total": parse_number(unit_price)})
    return items


def _profile_madecentro_items(texto: str):
    items = []
    pattern = re.compile(r"^(\d+)\s+(\S+)\s+(.+?)\s+([\d.,]+)\s+(\S+)\s+([\d.,]+)\s+[\d.,]+\s+[\d.,]+\s+([\d.,]+)$", re.MULTILINE)
    for match in pattern.finditer(texto):
        _, code, description, quantity, unit, unit_price, total = match.groups()
        items.append({"codigo_proveedor": code, "descripcion_cruda": description.strip(), "unidad_medida": unit,
                      "cantidad": parse_number(quantity), "valor_unitario": parse_number(unit_price), "valor_total": parse_number(total)})
    return items


def _profile_calypso_items(texto: str):
    items = []
    pattern = re.compile(r"^(\d+)\s+(\S+)\s+(\S+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+[\d.,]+\s+([\d.,]+)$", re.MULTILINE)
    for match in pattern.finditer(texto):
        lines = texto[:match.start()].splitlines()
        candidates = [line.strip() for line in lines[-6:] if line.strip() and not re.match(r"^\d+\s", line.strip())]
        candidates = [line for line in candidates if not re.search(r"Nro\.?|C[óo]digo|Descripci[óo]n|Cantidad|Precio|Impuestos|VALOR|DIRECCI|CORREO|NOMBRE|TIPO|RESPONSABILIDAD|CARRERA|DATOS|EMISOR|RECEPTOR|MONEDA|TOTAL", line, re.IGNORECASE)]
        description = " ".join(candidates[-3:])
        after = texto[match.end():].splitlines()
        if after and after[0].strip() and not re.match(r"^\d+\s", after[0].strip()):
            description += " " + after[0].strip()
        description = re.sub(r".*(?:DESCRIPCI[ÓO]N|DATOS DEL RECEPTOR)\s*", "", description, flags=re.IGNORECASE).strip()
        description = re.sub(r"(?:IMPUESTOS|Precio|unitario|descuento|detalle|Nro\.?|C[óo]digo).*", "", description, flags=re.IGNORECASE).strip()
        _, code, unit, quantity, unit_price, _discount, total = match.groups()
        items.append({"codigo_proveedor": code, "descripcion_cruda": description or code, "unidad_medida": unit,
                      "cantidad": parse_number(quantity), "valor_unitario": parse_number(unit_price), "valor_total": parse_number(total)})
    extended = re.compile(r"^(\d+)\s+(\S+)\s+(.+?)\s+(MTR|NIU)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+[\d.,]+\s+([\d.,]+)$", re.MULTILINE)
    seen = {item["codigo_proveedor"] for item in items}
    for match in extended.finditer(texto):
        _, code, description, unit, quantity, unit_price, _discount, total = match.groups()
        if code in seen:
            continue
        items.append({"codigo_proveedor": code, "descripcion_cruda": description.strip(), "unidad_medida": unit,
                      "cantidad": parse_number(quantity), "valor_unitario": parse_number(unit_price), "valor_total": parse_number(total)})
    return items


def _profile_alfagas_items(texto: str):
    items = []
    pattern = re.compile(r"^(\d+)\s+(\S+)\s+(.+?)\s+(\S+)\s+([\d.,]+)\s+[\d.,]+\s+[\d.,]+\s+([\d.,]+)$", re.MULTILINE)
    for match in pattern.finditer(texto):
        _, code, description, unit, unit_price, total = match.groups()
        items.append({"codigo_proveedor": code, "descripcion_cruda": description.strip(), "unidad_medida": unit,
                      "cantidad": 1.0, "valor_unitario": parse_number(unit_price), "valor_total": parse_number(total)})
    return items


def _profile_ginna_items(texto: str):
    items = []
    pattern = re.compile(r"^(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+?)\s+([\d.,]+)\s+([\d.,]+)$", re.MULTILINE)
    for match in pattern.finditer(texto):
        _, quantity, unit, code, description, unit_price, total = match.groups()
        items.append({"codigo_proveedor": code, "descripcion_cruda": description.strip(), "unidad_medida": unit,
                      "cantidad": parse_number(quantity), "valor_unitario": parse_number(unit_price), "valor_total": parse_number(total)})
    return items


def _profile_sodimac_credit_items(texto: str):
    items = []
    pattern = re.compile(r"^(\d+)\s+(\d+)\s+(\S+)\s+(.+?)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)$", re.MULTILINE)
    for match in pattern.finditer(texto):
        _, quantity, code, description, unit_price, _gross, _discount, _discount_value, subtotal, _tax_rate, _tax, _total = match.groups()
        items.append({"codigo_proveedor": code, "descripcion_cruda": description.strip(), "unidad_medida": None,
                      "cantidad": parse_number(quantity), "valor_unitario": parse_number(unit_price), "valor_total": parse_number(subtotal)})
    return items


def _parse_profile_disenos(texto, archivo):
    return _profile_invoice(texto, archivo, "DISEÑOS Y ESPEJOS SAS", "900587636-8",
                            r"FACTURA[^\n]*?\b(FE\s*\d+)", r"(?:FECHA\s+FACTURA|Expedici[óo]n)[^\d]*(\d{2}/\d{2}/\d{4})",
                            r"SUBTOTAL\s+([\d.,]+)", r"\bIVA\s+([\d.,]+)", r"TOTAL\s+FACTURA\s+([\d.,]+)", _profile_disenos_items)


def _parse_profile_simetrica(texto, archivo):
    return _profile_invoice(texto, archivo, "SIMETRICA INGENIEROS CIVILES SAS", "900503901",
                            r"N[úu]mero de Factura:\s*([A-Z0-9-]+)", r"Fecha de Emisi[óo]n:\s*(\d{2}/\d{2}/\d{4})",
                            r"Subtotal\s+([\d.,]+)", r"\bIVA\s+([\d.,]+)", r"Total factura.*?\$\s*([\d.,]+)", _profile_simetrica_items)


def _parse_profile_cadena(texto, archivo):
    return _profile_invoice(texto, archivo, "CADENA TOBON ANA MARIA", "51657102-9",
                            r"Nro\. Doc\.:\s*(\S+)", r"Fecha y Hora de Generaci[óo]n:\s*(\d{4}-\d{2}-\d{2})",
                            r"SUBTOTAL:\s*([\d.,]+)", r"IVA\s*:\s*[\d.,]+%\s+[\d.,]+\s+([\d.,]+)", r"TOTAL:\s*([\d.,]+)", _profile_cadena_items)


def _parse_profile_madecentro(texto, archivo):
    return _profile_invoice(texto, archivo, "MADECENTRO COLOMBIA SAS", "811028650-1",
                            r"FACTURA[^\n]*?No\.\s*([A-Z0-9-]+)", r"Fecha Generaci[óo]n Erp:\s*(\d{4}-\d{2}-\d{2})",
                            r"SUB-?TOTAL\s+([\d.,]+)", r"IVA\s+\d+%\s+([\d.,]+)", r"VR\. NETO\s+([\d.,]+)", _profile_madecentro_items)


def _parse_profile_calypso(texto, archivo):
    return _profile_invoice(texto, archivo, "COMERCIALIZADORA CALYPSO SAS", "860075208-7",
                            r"No\.\s*([A-Z0-9 ]+\d+)", r"FECHA DE EMISI[ÓO]N\s*:\s*(\d{4}/\d{2}/\d{2})",
                            r"Total sin impuesto\s+([\d.,]+)", r"Total impuesto\s+([\d.,]+)", r"Total COP\s+([\d.,]+)", _profile_calypso_items)


def _parse_profile_alfagas(texto, archivo):
    return _profile_invoice(texto, archivo, "ALFAGAS SERVICIOS SAS", "900335232-5",
                            r"FACTURA[^\n]*?\b(FEAG\s*\d+)", r"Fecha Factura:\s*(\d{2}/\d{2}/\d{4})",
                            r"Subtotal\s+([\d.,]+)", r"IVA\s+([\d.,]+)", r"Total COP\s+([\d.,]+)", _profile_alfagas_items)


def _parse_profile_ginna(texto, archivo):
    return _profile_invoice(texto, archivo, "GINNA ESTEFANIA ALARCON CALIXTO", "1014275772-1",
                            r"No\.\s*(DEP\s*\d+)", r"Expedici[óo]n\s+(\d{4}-\d{2}-\d{2})",
                            r"Total Bruto\s+([\d.,]+)", r"IVA\s+([\d.,]+)", r"Total a Pagar\s+([\d.,]+)", _profile_ginna_items)


def _parse_profile_sodimac_credit(texto, archivo):
    return _profile_invoice(texto, archivo, "SODIMAC COLOMBIA S.A. (HOMECENTER)", "800242106-2",
                            r"N[°º]\s*(NC\S+)", r"Fecha de Expedici[óo]n\s*:\s*(\d{4}/\d{2}/\d{2})",
                            r"SUB\.TOTAL\s+\$?\s*([\d.,]+)", r"^IVA\s+\$?\s*([\d.,]+)", r"TOTAL A PAGAR\s+\$?\s*([\d.,]+)", _profile_sodimac_credit_items)


def _parse_sodimac(texto, archivo):
    if re.search(r"NOTA\s+CR[ÉE]DITO", texto, re.IGNORECASE):
        return _parse_profile_sodimac_credit(texto, archivo)
    return _parse_homecenter(texto, archivo)


PROVEEDORES = [
    (re.compile(r"900\.587\.636|900587636"), _parse_profile_disenos),
    (re.compile(r"900\.503\.901|900503901"), _parse_profile_simetrica),
    (re.compile(r"51\.657\.102|51657102"), _parse_profile_cadena),
    (re.compile(r"811\.028\.650|811028650"), _parse_profile_madecentro),
    (re.compile(r"860\.075\.208|860075208"), _parse_profile_calypso),
    (re.compile(r"900\.335\.232|900335232"), _parse_profile_alfagas),
    (re.compile(r"1014275772"), _parse_profile_ginna),
    (re.compile(r"800\.242\.106|800242106"), _parse_sodimac),
    (re.compile(r"901\.464\.983"), _parse_elequipos),
    (re.compile(r"900\.409\.224"), _parse_triangulo),
    (re.compile(r"900\.344\.425"), _parse_yesos),
    (re.compile(r"NIT\s+901649012"), lambda t, a: _parse_ferreteria_constructiva(t, a)),
]


def _buscar_parser(texto: str):
    for patron, parser_fn in PROVEEDORES:
        if patron.search(texto):
            return parser_fn
    return None


def parsear_factura(pdf_path: str):
    """Extrae una factura usando parser conocido o fallback generico.

    Los ZIP/XML DIAN se procesan con el esquema UBL estandar, evitando crear un
    parser por proveedor. Los parsers PDF dedicados se conservan para formatos
    historicos o documentos que no traen XML.
    """
    path = Path(pdf_path)
    suffix = path.suffix.lower()
    if suffix == ".zip":
        xml_bytes = _xml_from_zip(path.read_bytes())
        if xml_bytes:
            return _xml_parse_document(xml_bytes, pdf_path)
        raise ValueError("El ZIP no contiene un XML DIAN reconocible.")
    if suffix == ".xml":
        return _xml_parse_document(path.read_bytes(), pdf_path)

    texto = extraer_texto(pdf_path)
    parser_fn = _buscar_parser(texto)
    if not parser_fn:
        texto_ocr = extraer_texto_ocr(pdf_path)
        if texto_ocr.strip():
            texto = texto_ocr
            parser_fn = _buscar_parser(texto)
    if parser_fn:
        factura, items = parser_fn(texto, pdf_path)
        factura["fecha_factura"] = parse_fecha(factura.get("fecha_factura"))
        factura["fecha_vencimiento"] = parse_fecha(factura.get("fecha_vencimiento"))
        return factura, items
    if texto.strip():
        return _parse_generico_pdf(texto, pdf_path)
    raise ValueError(f"No se pudo extraer texto de {pdf_path}, ni siquiera con OCR.")
