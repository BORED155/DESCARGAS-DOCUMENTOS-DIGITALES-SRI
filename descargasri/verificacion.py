"""Revisión de lo guardado: cada archivo es el comprobante que dice ser.

Para cada comprobante del registro del mes:
  * XML: es un <autorizacion> AUTORIZADO cuya clave (numeroAutorizacion o
    claveAcceso) es la del registro, y la clave tiene el dígito verificador correcto.
  * PDF (RIDE): es un PDF y su texto contiene la clave de acceso.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from . import xmlinfo
from .almacen import Registro, RegistroMes
from .clave import leer_clave


@dataclass
class Diferencia:
    clave: str
    seccion: str
    tipo: str
    serie: str
    fecha: str
    contraparte: str
    problema: str
    fuente: str  # «Archivo» o «Reporte del SRI»

    def como_dict(self) -> dict:
        return asdict(self)


def _dif(r: Registro, problema: str, fuente: str = "Archivo") -> Diferencia:
    return Diferencia(r.clave, r.seccion, r.tipo, r.serie, r.fecha_emision, r.razon_contraparte, problema, fuente)


def revisar_registro(base_empresa: Path, r: Registro) -> str | None:
    """Problema con los archivos de un comprobante, o None si están bien."""
    clave = leer_clave(r.clave)
    if clave is None or not clave.verificador_ok:
        return "la clave de acceso no es válida (dígito verificador)"
    presentes = [Path(base_empresa) / a for a in r.archivos if (Path(base_empresa) / a).is_file()]
    if not presentes:
        return "no hay archivo en la carpeta"
    for ruta in presentes:
        try:
            cont = ruta.read_bytes()
        except OSError as e:
            return f"no se puede leer {ruta.name}: {e}"
        if ruta.suffix.lower() == ".xml":
            if not xmlinfo.es_autorizacion(cont):
                return f"{ruta.name} no es un XML autorizado del SRI"
            try:
                info = xmlinfo.leer(cont)
            except Exception:
                return f"{ruta.name} está dañado (no se puede leer el XML)"
            if info.estado and info.estado.upper() != "AUTORIZADO":
                return f"{ruta.name} tiene estado {info.estado}"
            claves = {c for c in (info.clave, ) if c}
            if claves and r.clave not in claves:
                return f"{ruta.name} contiene otra clave de acceso"
        elif ruta.suffix.lower() == ".pdf":
            if not cont.startswith(b"%PDF"):
                return f"{ruta.name} no es un PDF"
            try:
                from io import BytesIO

                from pypdf import PdfReader
                texto = "".join(p.extract_text() or "" for p in PdfReader(BytesIO(cont)).pages)
            except Exception:
                return f"{ruta.name} está dañado (no se puede leer el PDF)"
            if r.clave not in re.sub(r"\D", "", texto):
                return f"{ruta.name} no contiene la clave de acceso del comprobante"
    return None


def archivo_valido(ruta: Path, clave: str) -> bool:
    """El archivo es el comprobante de esa clave (XML autorizado o RIDE con la clave)."""
    r = Registro(clave=clave, seccion="", archivos=[ruta.name])
    return revisar_registro(ruta.parent, r) is None


def revisar_archivos(base_empresa: Path, rmes: RegistroMes) -> tuple[int, list[Diferencia]]:
    """(comprobantes revisados, diferencias). Los anulados no se exigen."""
    revisados = 0
    difs: list[Diferencia] = []
    for r in rmes.datos.values():
        if r.anulado:
            continue
        revisados += 1
        problema = revisar_registro(base_empresa, r)
        if problema:
            difs.append(_dif(r, problema))
    return revisados, difs
