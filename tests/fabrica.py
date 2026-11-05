"""Comprobantes ficticios para las pruebas (RUC y nombres inventados)."""

from __future__ import annotations

from datetime import date

from descargasri.clave import digito_modulo11

RUC_EMPRESA = "0990000001001"      # ficticio
RUC_PROVEEDOR = "1790000002001"    # ficticio
RUC_CLIENTE = "1700000003001"      # ficticio

CODIGOS = {"factura": "01", "comprobanteRetencion": "07", "notaCredito": "04"}


def clave(fecha: date, tipo: str, ruc: str, estab="001", pto="001", sec=1) -> str:
    base = f"{fecha:%d%m%Y}{tipo}{ruc}2{estab}{pto}{sec:09d}12345678" + "1"
    return base + str(digito_modulo11(base))


def factura_xml(cl: str, ruc_emisor: str, razon_emisor: str, comprador: str, razon_comprador: str,
                fecha: date, total: str = "115.00") -> str:
    from xml.sax.saxutils import escape
    razon_emisor, razon_comprador = escape(razon_emisor), escape(razon_comprador)
    return (
        '<?xml version="1.0" encoding="UTF-8"?><factura id="comprobante" version="1.1.0">'
        f"<infoTributaria><ambiente>2</ambiente><tipoEmision>1</tipoEmision><razonSocial>{razon_emisor}</razonSocial>"
        f"<ruc>{ruc_emisor}</ruc><claveAcceso>{cl}</claveAcceso><codDoc>01</codDoc>"
        f"<estab>{cl[24:27]}</estab><ptoEmi>{cl[27:30]}</ptoEmi><secuencial>{cl[30:39]}</secuencial></infoTributaria>"
        f"<infoFactura><fechaEmision>{fecha:%d/%m/%Y}</fechaEmision><tipoIdentificacionComprador>04</tipoIdentificacionComprador>"
        f"<razonSocialComprador>{razon_comprador}</razonSocialComprador><identificacionComprador>{comprador}</identificacionComprador>"
        f"<totalSinImpuestos>100.00</totalSinImpuestos><importeTotal>{total}</importeTotal></infoFactura></factura>"
    )


def autorizacion(cl: str, comprobante: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><autorizacion><estado>AUTORIZADO</estado>'
        f"<numeroAutorizacion>{cl}</numeroAutorizacion><fechaAutorizacion>2026-03-02T10:00:00-05:00</fechaAutorizacion>"
        f"<ambiente>PRODUCCIÓN</ambiente><comprobante><![CDATA[{comprobante}]]></comprobante><mensajes/></autorizacion>"
    ).encode("utf-8")


def respuesta_soap(cl: str, comprobante: str | None, estado="AUTORIZADO", mensaje="") -> bytes:
    from xml.sax.saxutils import escape
    if comprobante is None:
        cuerpo = "<numeroComprobantes>0</numeroComprobantes><autorizaciones/>"
        if mensaje:
            cuerpo = (f"<autorizaciones><autorizacion><estado>{estado}</estado><mensajes><mensaje>"
                      f"<identificador>80</identificador><mensaje>ERROR</mensaje>"
                      f"<informacionAdicional>{mensaje}</informacionAdicional></mensaje></mensajes>"
                      f"</autorizacion></autorizaciones>")
    else:
        cuerpo = (f"<numeroComprobantes>1</numeroComprobantes><autorizaciones><autorizacion>"
                  f"<estado>{estado}</estado><numeroAutorizacion>{cl}</numeroAutorizacion>"
                  f"<fechaAutorizacion>2026-10-02T10:00:00-05:00</fechaAutorizacion><ambiente>PRODUCCIÓN</ambiente>"
                  f"<comprobante>{escape(comprobante)}</comprobante><mensajes/></autorizacion></autorizaciones>")
    return (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
        '<ns2:autorizacionComprobanteResponse xmlns:ns2="http://ec.gob.sri.ws.autorizacion">'
        f"<RespuestaAutorizacionComprobante><claveAccesoConsultada>{cl}</claveAccesoConsultada>{cuerpo}"
        "</RespuestaAutorizacionComprobante></ns2:autorizacionComprobanteResponse></soap:Body></soap:Envelope>"
    ).encode("utf-8")


def pdf_con_texto(*lineas: str) -> bytes:
    """PDF mínimo y válido con líneas de texto (para simular el RIDE)."""
    flujo = "BT /F1 9 Tf 40 800 Td " + " ".join(
        f"({l.replace('(', '[').replace(')', ']')}) Tj 0 -14 Td" for l in lineas) + " ET"
    objetos = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(flujo)} >>\nstream\n{flujo}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    salida = b"%PDF-1.4\n"
    pos = []
    for i, o in enumerate(objetos, 1):
        pos.append(len(salida))
        salida += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(salida)
    salida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode()
    for p in pos:
        salida += f"{p:010d} 00000 n \n".encode()
    salida += f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return salida


def ride_pdf(clave: str, cliente: str, identificacion: str = "1700000003001") -> bytes:
    return pdf_con_texto("FACTURA", "CLAVE DE ACCESO", clave, "Razon Social / Nombres y Apellidos:",
                         "Identificacion", "Fecha Guia", cliente, identificacion, "02/03/2026",
                         "115.00 VALOR TOTAL")
