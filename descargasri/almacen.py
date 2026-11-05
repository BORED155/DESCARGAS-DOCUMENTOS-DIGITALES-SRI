"""Dónde y cómo se guardan los comprobantes descargados.

    <base>/<RUC> - <RAZÓN SOCIAL>/<AAAA-MM>/
        recibidos/<tipo>/<clave>.xml   (y .pdf si se pidió el RIDE)
        emitidos/<tipo>/<clave>.xml    (o .pdf cuando el SRI ya no da el XML)
        registro.json                  (lo descargado en ese mes)
        indice AAAA-MM.xlsx            (resumen para revisar)
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .clave import Clave

SECCIONES = ("recibidos", "emitidos")


def nombre_seguro(texto: str, largo: int = 80, puntos_al_final: bool = False) -> str:
    """Texto válido como nombre de archivo en Linux y Windows.

    Windows no admite nombres que terminen en punto; dentro de un nombre más
    largo (puntos_al_final=True) el punto de «S.A.» se conserva."""
    t = unicodedata.normalize("NFC", texto or "").strip()
    t = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", t)
    t = re.sub(r"\s+", " ", t).strip(" " if puntos_al_final else " .")
    return t[:largo].strip() or "SIN NOMBRE"


def carpeta_empresa(base: Path, ruc: str, razon: str) -> Path:
    return Path(base) / nombre_seguro(f"{ruc} - {razon}" if razon else ruc)


def carpeta_mes(base_empresa: Path, anio: int, mes: int) -> Path:
    return Path(base_empresa) / f"{anio:04d}-{mes:02d}"


TIPO_EN_NOMBRE = {
    "01": "Factura", "03": "Liquidación de compra", "04": "Nota de crédito",
    "05": "Nota de débito", "06": "Guía de remisión", "07": "Retención",
}


def carpeta_comprobante(base_empresa: Path, seccion: str, clave: Clave) -> Path:
    f = clave.fecha
    return carpeta_mes(base_empresa, f.year, f.month) / seccion / clave.carpeta_tipo


def ruta_comprobante(base_empresa: Path, seccion: str, clave: Clave, extension: str) -> Path:
    """Nombre antiguo (versiones anteriores): la clave de acceso."""
    return carpeta_comprobante(base_empresa, seccion, clave) / f"{clave.texto}{extension}"


def nombre_archivo(clave: Clave, contraparte: str, extension: str) -> str:
    """«AAAA-MM-DD PROVEEDOR Tipo 001-001-000000123.xml» (en emitidos, el cliente o retenido)."""
    tipo = TIPO_EN_NOMBRE.get(clave.tipo, clave.nombre_tipo)
    quien = nombre_seguro(contraparte or "SIN NOMBRE", 70, puntos_al_final=True)
    return f"{clave.fecha:%Y-%m-%d} {quien} {tipo} {clave.serie}{extension}"


def ruta_legible(base_empresa: Path, seccion: str, clave: Clave, contraparte: str, extension: str) -> Path:
    return carpeta_comprobante(base_empresa, seccion, clave) / nombre_archivo(clave, contraparte, extension)


@dataclass
class Registro:
    clave: str
    seccion: str
    tipo: str = ""
    serie: str = ""
    ruc_contraparte: str = ""
    razon_contraparte: str = ""
    fecha_emision: str = ""
    fecha_autorizacion: str = ""
    sin_impuestos: str = ""
    iva: str = ""
    total: str = ""
    archivos: list[str] = field(default_factory=list)
    origen: str = ""
    estado: str = ""
    anulado: bool = False


class RegistroMes:
    """registro.json de un mes: clave → Registro; se fusiona entre ejecuciones."""

    def __init__(self, carpeta: Path):
        self.carpeta = Path(carpeta)
        self.ruta = self.carpeta / "registro.json"
        self.datos: dict[str, Registro] = {}
        self.anulados: dict[str, dict] = {}
        self.comprobacion: dict[str, dict] = {}
        # días de emitidos ya consultados y completos: "tipo|aaaa-mm-dd" -> cantidad en el SRI
        self.dias_verificados: dict[str, int] = {}
        self.diferencias: list[dict] = []  # de la última revisión
        if self.ruta.is_file():
            try:
                crudo = json.loads(self.ruta.read_text(encoding="utf-8"))
                for k, v in crudo.get("comprobantes", {}).items():
                    self.datos[k] = Registro(**{c: v[c] for c in Registro.__dataclass_fields__ if c in v})
                self.anulados = crudo.get("anulados", {})
                self.comprobacion = crudo.get("comprobacion", {})
                self.dias_verificados = crudo.get("dias_verificados", {})
                self.diferencias = crudo.get("diferencias", [])
            except (ValueError, TypeError, KeyError):
                pass

    def poner(self, r: Registro) -> None:
        previo = self.datos.get(r.clave)
        if previo:
            # conservar archivos ya bajados (que sigan existiendo) y la marca de anulado
            base = self.carpeta.parent
            viejos = [x for x in previo.archivos if (base / x).is_file() or not base.is_dir()]
            r.archivos = sorted(set(viejos) | set(r.archivos))
            r.anulado = r.anulado or previo.anulado
        r.anulado = r.anulado or r.clave in self.anulados
        self.datos[r.clave] = r

    def poner_anulado(self, clave: str, info: dict) -> None:
        self.anulados[clave] = info
        if clave in self.datos:
            self.datos[clave].anulado = True

    def archivos_presentes(self, base_empresa: Path, reg: Registro) -> bool:
        return any((Path(base_empresa) / a).is_file() for a in reg.archivos)

    def inventario(self, base_empresa: Path) -> dict[tuple[str, str], int]:
        """(sección, tipo) -> comprobantes del mes cuyo archivo sigue en la carpeta."""
        out: dict[tuple[str, str], int] = {}
        for r in self.datos.values():
            if self.archivos_presentes(base_empresa, r):
                k = (r.seccion, r.tipo)
                out[k] = out.get(k, 0) + 1
        return out

    def guardar(self) -> None:
        self.carpeta.mkdir(parents=True, exist_ok=True)
        crudo = {
            "comprobantes": {k: asdict(v) for k, v in sorted(self.datos.items())},
            "anulados": dict(sorted(self.anulados.items())),
            "comprobacion": self.comprobacion,
            "dias_verificados": dict(sorted(self.dias_verificados.items())),
            "diferencias": self.diferencias,
        }
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(crudo, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.ruta)


def guardar_archivo(ruta: Path, contenido: bytes) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_name(ruta.name + ".parcial")
    tmp.write_bytes(contenido)
    tmp.replace(ruta)


def _numero(texto: str):
    t = (texto or "").strip().replace(" ", "")
    if not t:
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return texto


def escribir_indice(reg: RegistroMes, anio: int, mes: int) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    negrita = Font(bold=True)
    fondo = PatternFill("solid", fgColor="DDEBF7")
    rojo = PatternFill("solid", fgColor="F8D7DA")

    def hoja(titulo: str, cabecera: list[str], filas: list[list], anulado_col: int | None = None):
        ws = wb.create_sheet(titulo)
        ws.append(cabecera)
        for c in ws[1]:
            c.font = negrita
            c.fill = fondo
        for f in filas:
            ws.append(f)
            if anulado_col is not None and f[anulado_col] == "Sí":
                for c in ws[ws.max_row]:
                    c.fill = rojo
        for i, _ in enumerate(cabecera, 1):
            largo = max((len(str(c.value or "")) for c in ws[get_column_letter(i)]), default=8)
            ws.column_dimensions[get_column_letter(i)].width = min(max(10, largo + 2), 60)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

    cab = ["Tipo", "Serie", "Fecha emisión", "RUC", "Razón social", "Sin impuestos", "IVA", "Total",
           "Clave de acceso", "Origen", "Archivo", "Estado", "Anulado"]
    for seccion, titulo, quien in (("recibidos", "Recibidos", "emisor"), ("emitidos", "Emitidos", "receptor")):
        filas = []
        for r in sorted((x for x in reg.datos.values() if x.seccion == seccion),
                        key=lambda x: (x.tipo, x.fecha_emision[6:10] + x.fecha_emision[3:5] + x.fecha_emision[:2], x.serie)):
            filas.append([r.tipo, r.serie, r.fecha_emision, r.ruc_contraparte, r.razon_contraparte,
                          _numero(r.sin_impuestos), _numero(r.iva), _numero(r.total), r.clave, r.origen,
                          ", ".join(r.archivos), r.estado, "Sí" if r.anulado else ""])
        c = list(cab)
        c[3] = f"RUC {quien}" if seccion == "recibidos" else "Identificación receptor"
        c[4] = f"Razón social {quien}"
        hoja(titulo, c, filas, anulado_col=12)

    hoja("Anulados", ["Lado", "Identificación", "Razón social", "Tipo", "Serie", "Clave de acceso"],
         [[v.get("lado", ""), v.get("identificacion", ""), v.get("razon", ""), v.get("tipo", ""),
           v.get("serie", ""), k] for k, v in reg.anulados.items()])

    comp = wb.create_sheet("Comprobación")
    comp.append(["Sección", "Tipo", "Según el SRI", "Leídos", "En la carpeta", "Pasos sin consultar", "Estado"])
    for c in comp[1]:
        c.font = negrita
        c.fill = fondo
    verde = PatternFill("solid", fgColor="D4EDDA")
    for k, v in reg.comprobacion.items():
        sec, tipo = k.split("|", 1)
        comp.append([sec.capitalize(), tipo, v.get("sri", 0), v.get("leidos", 0), v.get("guardados", 0),
                     ", ".join(v.get("fallidos", [])), "Completo" if v.get("completo") else "INCOMPLETO"])
        for c in comp[comp.max_row]:
            c.fill = verde if v.get("completo") else rojo
    for col, ancho in zip("ABCDEFG", (12, 28, 14, 10, 14, 24, 12)):
        comp.column_dimensions[col].width = ancho

    hoja("Diferencias", ["Fuente", "Sección", "Tipo", "Serie", "Fecha emisión", "Proveedor / cliente",
                         "Problema", "Clave de acceso"],
         [[d.get("fuente", ""), d.get("seccion", "").capitalize(), d.get("tipo", ""), d.get("serie", ""),
           d.get("fecha", ""), d.get("contraparte", ""), d.get("problema", ""), d.get("clave", "")]
          for d in reg.diferencias] or [["", "", "", "", "", "", "Sin diferencias: todo coincide", ""]])

    resumen = wb.create_sheet("Resumen", 0)
    resumen.append(["Periodo", f"{anio:04d}-{mes:02d}"])
    resumen.append([])
    resumen.append(["Sección", "Tipo", "Cantidad", "Total"])
    for c in resumen[3]:
        c.font = negrita
        c.fill = fondo
    grupos: dict[tuple[str, str], list[float]] = {}
    for r in reg.datos.values():
        if r.anulado:
            continue
        g = grupos.setdefault((r.seccion, r.tipo), [0, 0.0])
        g[0] += 1
        n = _numero(r.total)
        g[1] += n if isinstance(n, float) else 0.0
    for (s, t), (n, tot) in sorted(grupos.items()):
        resumen.append([s.capitalize(), t, n, round(tot, 2)])
    resumen.append([])
    resumen.append(["Anulados (no sumados)", "", len(reg.anulados)])
    for col, ancho in zip("ABCD", (22, 30, 10, 14)):
        resumen.column_dimensions[col].width = ancho

    reg.carpeta.mkdir(parents=True, exist_ok=True)
    ruta = reg.carpeta / f"indice {anio:04d}-{mes:02d}.xlsx"
    wb.save(ruta)
    return ruta
