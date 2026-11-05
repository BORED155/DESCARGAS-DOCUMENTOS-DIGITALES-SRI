"""Lectura mínima de un XML <autorizacion> para completar el índice."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass
class Info:
    estado: str = ""
    clave: str = ""
    fecha_autorizacion: str = ""
    tipo_doc: str = ""
    serie: str = ""
    fecha_emision: str = ""
    ruc_emisor: str = ""
    razon_emisor: str = ""
    id_receptor: str = ""
    razon_receptor: str = ""
    sin_impuestos: str = ""
    total: str = ""


# raíz del comprobante → (nodo info, id receptor, razón receptor, sin impuestos, total)
_CAMPOS = {
    "factura": ("infoFactura", "identificacionComprador", "razonSocialComprador", "totalSinImpuestos", "importeTotal"),
    "notaCredito": ("infoNotaCredito", "identificacionComprador", "razonSocialComprador", "totalSinImpuestos", "valorModificacion"),
    "notaDebito": ("infoNotaDebito", "identificacionComprador", "razonSocialComprador", "totalSinImpuestos", "valorTotal"),
    "liquidacionCompra": ("infoLiquidacionCompra", "identificacionProveedor", "razonSocialProveedor", "totalSinImpuestos", "importeTotal"),
    "comprobanteRetencion": ("infoCompRetencion", "identificacionSujetoRetenido", "razonSocialSujetoRetenido", "", ""),
    "guiaRemision": ("infoGuiaRemision", "", "", "", ""),
}


def es_autorizacion(contenido: bytes) -> bool:
    """True si el contenido es un XML con <autorizacion> (y no una página HTML del portal)."""
    cabeza = contenido[:4000].lstrip()
    if not cabeza.startswith(b"<"):
        return False
    bajo = cabeza.lower()
    if b"<html" in bajo or b"<!doctype html" in bajo:
        return False
    return b"<autorizacion" in bajo


def leer(contenido: bytes) -> Info:
    info = Info()
    raiz = ET.fromstring(contenido)
    if raiz.tag.rsplit("}", 1)[-1] != "autorizacion":
        raiz = next((n for n in raiz.iter() if n.tag.rsplit("}", 1)[-1] == "autorizacion"), raiz)
    info.estado = (raiz.findtext("estado") or "").strip()
    info.clave = (raiz.findtext("numeroAutorizacion") or "").strip()
    info.fecha_autorizacion = (raiz.findtext("fechaAutorizacion") or "").strip()
    texto = raiz.findtext("comprobante") or ""
    if not texto.strip():
        return info
    comp = ET.fromstring(texto.strip().encode("utf-8"))
    info.tipo_doc = comp.tag
    it = comp.find("infoTributaria")
    if it is not None:
        info.clave = info.clave or (it.findtext("claveAcceso") or "")
        info.ruc_emisor = it.findtext("ruc") or ""
        info.razon_emisor = it.findtext("razonSocial") or ""
        info.serie = f"{it.findtext('estab') or ''}-{it.findtext('ptoEmi') or ''}-{it.findtext('secuencial') or ''}"
    campos = _CAMPOS.get(comp.tag)
    if campos:
        nodo = comp.find(campos[0])
        if nodo is not None:
            info.fecha_emision = nodo.findtext("fechaEmision") or ""
            if campos[1]:
                info.id_receptor = nodo.findtext(campos[1]) or ""
                info.razon_receptor = nodo.findtext(campos[2]) or ""
            if campos[3]:
                info.sin_impuestos = nodo.findtext(campos[3]) or ""
            if campos[4]:
                info.total = nodo.findtext(campos[4]) or ""
    return info


# --- RIDE (PDF) ------------------------------------------------------------------
# El texto del RIDE del SRI trae primero las etiquetas y luego los valores:
#   Razón Social / Nombres y Apellidos:
#   Identificación
#   Fecha Guía
#   CLIENTE S.A.
#   1790000000001
#   02/03/2026

_ETIQUETAS_RIDE = re.compile(
    r"^(identificaci[oó]n|fecha|gu[ií]a|fecha gu[ií]a|fecha emisi[oó]n|fecha de emisi[oó]n|"
    r"ejercicio fiscal|placa|direcci[oó]n|r\.?u\.?c\.?|tel[eé]fono|email)\b.*$", re.IGNORECASE)


def leer_ride(contenido: bytes) -> Info:
    """Datos básicos del receptor desde el PDF (RIDE) que entrega el portal."""
    from io import BytesIO

    from pypdf import PdfReader

    info = Info()
    texto = "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(contenido)).pages)
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    for i, l in enumerate(lineas):
        if l.lower().startswith("razón social / nombres y apellidos") or \
                l.lower().startswith("razon social / nombres y apellidos"):
            resto = l.split(":", 1)[1].strip() if ":" in l else ""
            candidatos = ([resto] if resto else []) + lineas[i + 1:i + 8]
            for c in candidatos:
                if _ETIQUETAS_RIDE.match(c) or c.endswith(":"):
                    continue
                if re.fullmatch(r"[\d/\-\s.]+", c):
                    continue
                info.razon_receptor = c
                j = lineas.index(c, i) if c in lineas[i:] else i
                for d in lineas[j + 1:j + 3]:
                    if re.fullmatch(r"\d{10,13}|[A-Z0-9]{5,20}", d):
                        info.id_receptor = d
                        break
                break
            break
    m = re.search(r"([\d.,]+)\s*VALOR TOTAL\b", texto)
    if m:
        info.total = m.group(1)
    return info
