"""Servicio web público de autorización del SRI (no requiere iniciar sesión).

Con la clave de acceso devuelve el XML autorizado. En la práctica solo
responde por comprobantes recientes (aprox. el último mes y algo); para
fechas anteriores contesta vacío o «fecha de emision esta fuera del rango
permitido».
"""

from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from xml.sax.saxutils import escape

URL_AUTORIZACION = (
    "https://cel.sri.gob.ec/comprobantes-electronicos-ws/AutorizacionComprobantesOffline"
)

_SOBRE = (
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:ec="http://ec.gob.sri.ws.autorizacion"><soapenv:Header/><soapenv:Body>'
    "<ec:autorizacionComprobante><claveAccesoComprobante>{clave}</claveAccesoComprobante>"
    "</ec:autorizacionComprobante></soapenv:Body></soapenv:Envelope>"
)


@dataclass
class Respuesta:
    estado: str  # AUTORIZADO, NO AUTORIZADO, RECHAZADA, SIN_RESULTADO
    xml: bytes | None  # documento <autorizacion> listo para guardar
    mensaje: str = ""

    @property
    def autorizado(self) -> bool:
        return self.estado == "AUTORIZADO" and self.xml is not None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _hijo(nodo: ET.Element, nombre: str) -> ET.Element | None:
    for h in nodo:
        if _local(h.tag) == nombre:
            return h
    return None


def _texto(nodo: ET.Element, nombre: str) -> str:
    h = _hijo(nodo, nombre)
    return (h.text or "").strip() if h is not None else ""


def armar_autorizacion(aut: ET.Element) -> bytes:
    """Arma un <autorizacion> como el que entrega el portal (comprobante en CDATA)."""
    partes = ['<?xml version="1.0" encoding="UTF-8"?>', "<autorizacion>"]
    for nombre in ("estado", "numeroAutorizacion", "fechaAutorizacion", "ambiente"):
        h = _hijo(aut, nombre)
        if h is not None:
            partes.append(f"<{nombre}>{escape(h.text or '')}</{nombre}>")
    comp = _texto(aut, "comprobante")
    comp_cdata = comp.replace("]]>", "]]]]><![CDATA[>")
    partes.append(f"<comprobante><![CDATA[{comp_cdata}]]></comprobante>")
    partes.append("<mensajes/>")
    partes.append("</autorizacion>")
    return "".join(partes).encode("utf-8")


def interpretar(respuesta: bytes) -> Respuesta:
    raiz = ET.fromstring(respuesta)
    auts = [n for n in raiz.iter() if _local(n.tag) == "autorizacion"]
    if not auts:
        return Respuesta("SIN_RESULTADO", None, "El SRI no devolvió el comprobante")
    aut = auts[0]
    estado = _texto(aut, "estado")
    if estado == "AUTORIZADO" and _texto(aut, "comprobante"):
        return Respuesta(estado, armar_autorizacion(aut))
    msgs = []
    for n in aut.iter():
        if _local(n.tag) in ("mensaje", "informacionAdicional") and n.text and n.text.strip():
            msgs.append(n.text.strip())
    return Respuesta(estado or "SIN_RESULTADO", None, " · ".join(msgs))


def consultar(clave: str, timeout: float = 30.0, url: str = URL_AUTORIZACION) -> Respuesta:
    cuerpo = _SOBRE.format(clave=clave).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=cuerpo,
        headers={"Content-Type": "text/xml;charset=UTF-8", "User-Agent": "DescargasSRI/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return interpretar(r.read())


def fuera_de_rango(resp: Respuesta) -> bool:
    return resp.estado == "SIN_RESULTADO" or "fuera del rango" in resp.mensaje.lower()
