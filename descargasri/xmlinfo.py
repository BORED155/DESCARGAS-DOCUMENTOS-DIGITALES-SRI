"""Lectura mínima de un XML <autorizacion> para completar el índice."""

from __future__ import annotations

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
