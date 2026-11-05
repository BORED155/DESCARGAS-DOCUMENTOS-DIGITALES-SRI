"""Clave de acceso de 49 dígitos de los comprobantes electrónicos del SRI.

Estructura (ficha técnica de comprobantes electrónicos):
    fecha ddmmaaaa (8) · tipo (2) · RUC emisor (13) · ambiente (1) ·
    serie: establecimiento (3) + punto de emisión (3) · secuencial (9) ·
    código numérico (8) · tipo de emisión (1) · dígito verificador (1)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# Código de la clave → (nombre, carpeta)
TIPOS_CLAVE: dict[str, tuple[str, str]] = {
    "01": ("Factura", "facturas"),
    "03": ("Liquidación de compra", "liquidaciones"),
    "04": ("Nota de crédito", "notas_credito"),
    "05": ("Nota de débito", "notas_debito"),
    "06": ("Guía de remisión", "guias_remision"),
    "07": ("Comprobante de retención", "retenciones"),
}

_SOLO_49 = re.compile(r"^\d{49}$")


def digito_modulo11(base: str) -> int:
    """Dígito verificador módulo 11 con pesos 2..7 de derecha a izquierda."""
    total = 0
    peso = 2
    for c in reversed(base):
        total += int(c) * peso
        peso = 2 if peso == 7 else peso + 1
    d = 11 - total % 11
    if d == 11:
        return 0
    if d == 10:
        return 1
    return d


@dataclass(frozen=True)
class Clave:
    texto: str

    @property
    def fecha(self) -> date:
        t = self.texto
        return date(int(t[4:8]), int(t[2:4]), int(t[0:2]))

    @property
    def tipo(self) -> str:
        return self.texto[8:10]

    @property
    def ruc_emisor(self) -> str:
        return self.texto[10:23]

    @property
    def ambiente(self) -> str:
        return self.texto[23]

    @property
    def establecimiento(self) -> str:
        return self.texto[24:27]

    @property
    def punto_emision(self) -> str:
        return self.texto[27:30]

    @property
    def secuencial(self) -> str:
        return self.texto[30:39]

    @property
    def serie(self) -> str:
        return f"{self.establecimiento}-{self.punto_emision}-{self.secuencial}"

    @property
    def nombre_tipo(self) -> str:
        return TIPOS_CLAVE.get(self.tipo, (f"Tipo {self.tipo}", ""))[0]

    @property
    def carpeta_tipo(self) -> str:
        return TIPOS_CLAVE.get(self.tipo, ("", f"tipo_{self.tipo}"))[1]

    @property
    def verificador_ok(self) -> bool:
        return digito_modulo11(self.texto[:48]) == int(self.texto[48])


def leer_clave(texto: str) -> Clave | None:
    """Devuelve la clave si `texto` tiene exactamente 49 dígitos (ignora espacios)."""
    t = re.sub(r"\s", "", texto or "")
    if not _SOLO_49.match(t):
        return None
    try:
        Clave(t).fecha
    except ValueError:
        return None
    return Clave(t)


def buscar_clave(texto: str) -> Clave | None:
    """Primera secuencia de 49 dígitos dentro de un texto."""
    m = re.search(r"(?<!\d)\d{49}(?!\d)", texto or "")
    return leer_clave(m.group(0)) if m else None
