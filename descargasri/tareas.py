"""Recorridos de descarga como generadores.

Cada tarea produce órdenes (Js, Navegar, Esperar, Python, Aviso, …) que el
motor ejecuta en el navegador incorporado y le devuelve el resultado. Así la
lógica se puede probar sin Qt ni internet.
"""

from __future__ import annotations

import base64
import calendar
import math
import random
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Generator

from . import almacen, portal, soap, xmlinfo
from .clave import Clave, buscar_clave


# --- Órdenes para el motor ------------------------------------------------

@dataclass
class Js:
    codigo: str


@dataclass
class JsAsync:
    codigo: str
    ident: str
    timeout_s: float = 90


@dataclass
class Navegar:
    url: str


@dataclass
class Esperar:
    ms: int


@dataclass
class Python:
    funcion: Callable
    args: tuple = ()


@dataclass
class Aviso:
    texto: str
    nivel: str = "info"  # info, ok, alerta, error


@dataclass
class Progreso:
    hecho: int
    total: int
    texto: str = ""


@dataclass
class Captcha:
    visible: bool


class Falla(Exception):
    """Error que detiene la tarea con un mensaje para el usuario."""


Tarea = Generator[Any, Any, Any]


# --- Opciones ----------------------------------------------------------------

@dataclass
class Opciones:
    base: Path
    ruc: str
    razon: str
    anio: int
    meses: list[int]
    recibidos: bool = True
    emitidos: bool = True
    anulados: bool = True
    tipos_recibidos: list[str] = field(default_factory=lambda: list(portal.TIPOS_RECIBIDOS))
    tipos_emitidos: list[str] = field(default_factory=lambda: ["1", "2", "3", "4", "6"])
    con_pdf: bool = False
    pausa_ms: tuple[int, int] = (400, 1000)
    hoy: date = field(default_factory=date.today)

    @property
    def carpeta(self) -> Path:
        return almacen.carpeta_empresa(self.base, self.ruc, self.razon)


@dataclass
class Resumen:
    nuevos: int = 0
    existentes: int = 0
    errores: int = 0
    xml_portal: int = 0
    xml_servicio: int = 0
    pdf: int = 0
    anulados: int = 0


# --- Utilidades ----------------------------------------------------------------

def pausa(op: Opciones) -> Tarea:
    a, b = op.pausa_ms
    if b > 0:
        yield Esperar(random.randint(a, b))


def esperar_condicion(codigo: str, cond: Callable[[Any], bool], timeout_s: float,
                      que: str, intervalo_ms: int = 500) -> Tarea:
    pasos = max(1, int(timeout_s * 1000 / intervalo_ms))
    for _ in range(pasos):
        v = yield Js(codigo)
        if cond(v):
            return v
        yield Esperar(intervalo_ms)
    raise Falla(f"El portal no respondió a tiempo ({que}).")


def comprobar_sesion(intentos: int = 15) -> Tarea:
    """Empresa de la sesión abierta (reintenta mientras la página termina de cargar)."""
    v = None
    for _ in range(intentos):
        v = yield Js(portal.empresa())
        if v and v.get("login"):
            break
        if v and v.get("emp"):
            return v["emp"]
        yield Esperar(1000)
    raise Falla("La sesión del SRI está cerrada. Vuelve a entrar con tu RUC y clave y pulsa Descargar otra vez.")


def esperar_consulta(sufijo: str, entera: bool = True, timeout_s: float = 90) -> Tarea:
    """Espera el resultado de una consulta o cambio de página.

    Si el SRI muestra el captcha de imágenes, avisa y espera sin límite a que
    el usuario lo resuelva (o pulse Detener)."""
    espera = 0
    avisado = False
    while True:
        e = yield Js(portal.estado(sufijo, entera))
        e = e or {}
        if e.get("captcha"):
            if not avisado:
                yield Captcha(True)
                yield Aviso("El SRI pide resolver el captcha: resuélvelo en la página para continuar.", "alerta")
                avisado = True
            yield Esperar(1000)
            continue
        if avisado:
            yield Captcha(False)
            avisado = False
        if e.get("libre") and (e.get("filas", -1) >= 0 or e.get("mensajes")):
            yield Esperar(300)
            return e
        espera += 500
        if espera > timeout_s * 1000:
            raise Falla("El portal no devolvió resultados a tiempo.")
        yield Esperar(500)


def columnas(cols: list[str]) -> dict[str, int]:
    """Ubica las columnas por su título (el orden cambia entre recibidos y emitidos)."""
    pos: dict[str, int] = {}
    for i, c in enumerate(cols):
        t = c.lower()
        if "clave" in t:
            pos["clave"] = i
        elif "razón social" in t or "razon social" in t:
            pos["emisor"] = i
        elif "tipo y serie" in t:
            pos["tipo_serie"] = i
        elif "autorización" in t or "autorizacion" in t:
            pos["fecha_aut"] = i
        elif "emisión" in t or "emision" in t:
            pos["fecha_emi"] = i
        elif "sin impuestos" in t:
            pos["sin_imp"] = i
        elif t == "iva":
            pos["iva"] = i
        elif "total" in t:
            pos["total"] = i
    return pos


def fila_a_registro(seccion: str, cols: dict[str, int], celdas: list[str]) -> tuple[Clave | None, almacen.Registro | None]:
    def c(k: str) -> str:
        i = cols.get(k)
        return celdas[i] if i is not None and i < len(celdas) else ""

    clave = buscar_clave(c("clave")) or buscar_clave(" ".join(celdas))
    if not clave:
        return None, None
    ruc, razon = "", ""
    if "emisor" in cols:
        partes = c("emisor").split(" ", 1)
        if partes and partes[0].isdigit():
            ruc, razon = partes[0], (partes[1] if len(partes) > 1 else "")
        else:
            razon = c("emisor")
    reg = almacen.Registro(
        clave=clave.texto,
        seccion=seccion,
        tipo=clave.nombre_tipo,
        serie=clave.serie,
        ruc_contraparte=ruc or (clave.ruc_emisor if seccion == "recibidos" else ""),
        razon_contraparte=razon,
        fecha_emision=c("fecha_emi") or clave.fecha.strftime("%d/%m/%Y"),
        fecha_autorizacion=c("fecha_aut"),
        sin_impuestos=c("sin_imp"),
        iva=c("iva"),
        total=c("total"),
    )
    return clave, reg


def completar_con_xml(reg: almacen.Registro, contenido: bytes) -> None:
    try:
        info = xmlinfo.leer(contenido)
    except Exception:
        return
    if reg.seccion == "emitidos":
        reg.ruc_contraparte = reg.ruc_contraparte or info.id_receptor
        reg.razon_contraparte = reg.razon_contraparte or info.razon_receptor
    else:
        reg.ruc_contraparte = reg.ruc_contraparte or info.ruc_emisor
        reg.razon_contraparte = reg.razon_contraparte or info.razon_emisor
    reg.total = reg.total or info.total
    reg.sin_impuestos = reg.sin_impuestos or info.sin_impuestos
    reg.fecha_autorizacion = reg.fecha_autorizacion or info.fecha_autorizacion


class Registros:
    """Registros por mes abiertos durante una ejecución."""

    def __init__(self, op: Opciones):
        self.op = op
        self.abiertos: dict[tuple[int, int], almacen.RegistroMes] = {}

    def de(self, anio: int, mes: int) -> almacen.RegistroMes:
        k = (anio, mes)
        if k not in self.abiertos:
            self.abiertos[k] = almacen.RegistroMes(almacen.carpeta_mes(self.op.carpeta, anio, mes))
        return self.abiertos[k]

    def guardar(self) -> list[Path]:
        rutas = []
        for (a, m), reg in sorted(self.abiertos.items()):
            if reg.datos or reg.anulados:
                reg.guardar()
                rutas.append(almacen.escribir_indice(reg, a, m))
        return rutas


def bajar_link(link_id: str, ident: str) -> Tarea:
    r = yield JsAsync(portal.bajar(link_id, ident), ident)
    if not r or not r.get("ok"):
        raise Falla(f"No se pudo bajar el archivo: {(r or {}).get('e', 'sin respuesta')}")
    v = r["v"]
    return int(v.get("status", 0)), v.get("tipo", ""), base64.b64decode(v.get("b64", ""))


def _rel(op: Opciones, ruta: Path) -> str:
    try:
        return str(ruta.relative_to(op.carpeta))
    except ValueError:
        return str(ruta)




class Cancelado(Exception):
    """El usuario pulsó Detener."""


# --- Recorrido por páginas ----------------------------------------------------

def iterar_paginas(sufijo: str, procesar: Callable[[list[str], list[dict]], Tarea]) -> Tarea:
    """Pone muchas filas por página, recorre las páginas y procesa cada una.

    El portal solo entrega el archivo de las filas visibles, por eso cada
    página se procesa antes de pedir la siguiente. Devuelve cuántas filas leyó."""
    t = (yield Js(portal.total(sufijo))) or {}
    if t.get("total", 0) > t.get("porPagina", 0):
        r = yield Js(portal.poner_filas_por_pagina(sufijo))
        if r and r.get("ok"):
            yield from esperar_consulta(sufijo, entera=False)
            t = (yield Js(portal.total(sufijo))) or t
    total = t.get("total", 0)
    paginas = max(1, math.ceil(total / max(1, t.get("porPagina") or 1)))
    cuenta = 0
    for p in range(paginas):
        if p > 0:
            yield Js(portal.ir_a_pagina(sufijo, p))
            yield from esperar_consulta(sufijo, entera=False)
        datos = (yield Js(portal.leer_tabla(sufijo))) or {"cols": [], "filas": []}
        cuenta += len(datos["filas"])
        yield from procesar(datos["cols"], datos["filas"])
    return cuenta


# --- Recibidos ----------------------------------------------------------------

def procesar_recibidos(op: Opciones, regs: Registros, res: Resumen, cols: list[str], filas: list[dict]) -> Tarea:
    pos = columnas(cols)
    seguidos = 0
    for f in filas:
        clave, reg = fila_a_registro("recibidos", pos, f.get("celdas", []))
        if not clave:
            continue
        ruta_xml = almacen.ruta_comprobante(op.carpeta, "recibidos", clave, ".xml")
        if ruta_xml.is_file():
            res.existentes += 1
            reg.archivos.append(_rel(op, ruta_xml))
            reg.origen, reg.estado = "XML del portal", "Ya existía"
            completar_con_xml(reg, ruta_xml.read_bytes())
        elif f.get("xml"):
            status, tipo, cont = yield from bajar_link(f["xml"], "x" + clave.texto[-12:])
            if xmlinfo.es_autorizacion(cont):
                almacen.guardar_archivo(ruta_xml, cont)
                reg.archivos.append(_rel(op, ruta_xml))
                reg.origen, reg.estado = "XML del portal", "Descargado"
                completar_con_xml(reg, cont)
                res.nuevos += 1
                res.xml_portal += 1
                seguidos = 0
            else:
                reg.estado = f"Error: el portal no entregó el XML (HTTP {status}, {tipo.split(';')[0]})"
                res.errores += 1
                seguidos += 1
            yield from pausa(op)
        if op.con_pdf and f.get("pdf"):
            ruta_pdf = ruta_xml.with_suffix(".pdf")
            if not ruta_pdf.is_file():
                _, _, cont = yield from bajar_link(f["pdf"], "p" + clave.texto[-12:])
                if cont.startswith(b"%PDF"):
                    almacen.guardar_archivo(ruta_pdf, cont)
                    res.pdf += 1
                yield from pausa(op)
            if ruta_pdf.is_file():
                reg.archivos.append(_rel(op, ruta_pdf))
        regs.de(clave.fecha.year, clave.fecha.month).poner(reg)
        if seguidos >= 3:
            raise Falla("El portal dejó de entregar los XML (¿se cerró la sesión?). "
                        "Vuelve a entrar y pulsa Descargar: lo ya bajado no se repite.")


def _captcha_rechazado(e: dict) -> bool:
    return "captcha" in (e.get("mensajes") or "").lower()


def consultar_recibidos_verificado(op: Opciones, mes: int, tipo: str, reintentos: int = 2,
                                   espera_reintento_ms: int = 10_000) -> Tarea:
    """Consulta recibidos. Si el SRI rechaza el reCAPTCHA invisible («Captcha incorrecta»),
    reintenta y, si sigue, pide al usuario que pulse «Consultar» en la ventana del SRI."""
    sufijo = "tablaCompRecibidos"
    for intento in range(reintentos + 1):
        r = yield Js(portal.consultar_recibidos(op.anio, mes, tipo))
        if not r or not r.get("ok"):
            raise Falla((r or {}).get("e", "No se pudo consultar recibidos"))
        e = yield from esperar_consulta(sufijo, entera=True)
        if not _captcha_rechazado(e):
            return e
        if intento < reintentos:
            yield Aviso(f"El SRI respondió «{e['mensajes']}»; reintento en {espera_reintento_ms // 1000} s.", "alerta")
            yield Esperar(espera_reintento_ms)
    # Lo resuelve el usuario: los filtros ya están puestos en la página.
    yield Js(portal.js("D.marcar(); return true;"))
    yield Captcha(True)
    yield Aviso("El SRI no aceptó la verificación automática. En la ventana del SRI pulsa «Consultar» "
                "(los filtros ya están puestos); la descarga sigue sola.", "alerta")
    while True:
        e = yield from esperar_consulta(sufijo, entera=True, timeout_s=1800)
        if not _captcha_rechazado(e):
            yield Captcha(False)
            return e
        yield Js(portal.js("D.marcar(); return true;"))


def tarea_recibidos(op: Opciones, regs: Registros, res: Resumen, mes: int, avance: Callable[[str], Tarea]) -> Tarea:
    sufijo = "tablaCompRecibidos"
    yield Navegar(portal.URL_RECIBIDOS)
    yield from esperar_condicion(portal.hay("document.getElementById('frmPrincipal:btnBuscar')"), bool, 60,
                                 "abrir comprobantes recibidos")
    for tipo in op.tipos_recibidos:
        nombre = portal.TIPOS_PORTAL[tipo]
        yield from avance(f"Recibidos {mes:02d}/{op.anio}: {nombre}")
        e = yield from consultar_recibidos_verificado(op, mes, tipo)
        if e.get("filas", 0) <= 0:
            yield Aviso(f"Recibidos {mes:02d}/{op.anio} · {nombre}: ninguno")
            continue
        cuenta = yield from iterar_paginas(
            sufijo, lambda cols, filas: procesar_recibidos(op, regs, res, cols, filas))
        yield Aviso(f"Recibidos {mes:02d}/{op.anio} · {nombre}: {cuenta}", "ok")
        regs.guardar()
        yield from pausa(op)


# --- Emitidos -----------------------------------------------------------------

@dataclass
class EstadoServicio:
    """Hasta qué fecha el servicio web ya no entrega XML (se aprende al vuelo)."""
    sin_xml_hasta: date | None = None
    caido: bool = False


def procesar_emitidos(op: Opciones, regs: Registros, res: Resumen, srv: EstadoServicio,
                      cols: list[str], filas: list[dict]) -> Tarea:
    pos = columnas(cols)
    for f in filas:
        clave, reg = fila_a_registro("emitidos", pos, f.get("celdas", []))
        if not clave:
            continue
        ruta_xml = almacen.ruta_comprobante(op.carpeta, "emitidos", clave, ".xml")
        ruta_pdf = ruta_xml.with_suffix(".pdf")
        intentar_xml = not srv.caido and not (srv.sin_xml_hasta and clave.fecha <= srv.sin_xml_hasta)
        if ruta_xml.is_file():
            res.existentes += 1
            reg.archivos.append(_rel(op, ruta_xml))
            reg.origen, reg.estado = "XML servicio web SRI", "Ya existía"
            completar_con_xml(reg, ruta_xml.read_bytes())
        else:
            xml = None
            if intentar_xml:
                try:
                    resp = yield Python(soap.consultar, (clave.texto,))
                except Cancelado:
                    raise
                except Exception as ex:  # red caída o servicio con problemas
                    srv.caido = True
                    yield Aviso(f"El servicio web del SRI no respondió ({ex}); se bajará el PDF.", "alerta")
                    resp = None
                if resp is not None:
                    if resp.autorizado:
                        xml = resp.xml
                    elif soap.fuera_de_rango(resp):
                        if not srv.sin_xml_hasta or clave.fecha > srv.sin_xml_hasta:
                            srv.sin_xml_hasta = clave.fecha
                            yield Aviso(f"El SRI ya no entrega el XML de comprobantes del {clave.fecha:%d/%m/%Y} "
                                        "o anteriores; para esos se baja el PDF (RIDE).", "alerta")
            if xml:
                almacen.guardar_archivo(ruta_xml, xml)
                reg.archivos.append(_rel(op, ruta_xml))
                reg.origen, reg.estado = "XML servicio web SRI", "Descargado"
                completar_con_xml(reg, xml)
                res.nuevos += 1
                res.xml_servicio += 1
            elif ruta_pdf.is_file():
                res.existentes += 1
                reg.origen, reg.estado = "PDF RIDE", "Ya existía"
            elif f.get("pdf"):
                status, tipo, cont = yield from bajar_link(f["pdf"], "p" + clave.texto[-12:])
                if cont.startswith(b"%PDF"):
                    almacen.guardar_archivo(ruta_pdf, cont)
                    reg.origen, reg.estado = "PDF RIDE", "Descargado (sin XML)"
                    res.nuevos += 1
                    res.pdf += 1
                else:
                    reg.estado = f"Error: el portal no entregó el PDF (HTTP {status})"
                    res.errores += 1
            yield from pausa(op)
        if op.con_pdf and not ruta_pdf.is_file() and f.get("pdf"):
            _, _, cont = yield from bajar_link(f["pdf"], "p" + clave.texto[-12:])
            if cont.startswith(b"%PDF"):
                almacen.guardar_archivo(ruta_pdf, cont)
                res.pdf += 1
            yield from pausa(op)
        if ruta_pdf.is_file():
            reg.archivos.append(_rel(op, ruta_pdf))
        regs.de(clave.fecha.year, clave.fecha.month).poner(reg)


def abrir_emitidos() -> Tarea:
    yield Navegar(portal.URL_CONSULTAS)
    enlace = "Comprobantes electrónicos emitidos"
    yield from esperar_condicion(
        portal.hay(f"[...document.querySelectorAll('a')].some(a => a.innerText.trim() === '{enlace}')"),
        bool, 60, "abrir consultas")
    yield Js(portal.clic_texto(enlace))
    yield from esperar_condicion(
        portal.hay("document.getElementById('frmPrincipal:calendarFechaDesde_input')"), bool, 60,
        "abrir comprobantes emitidos")


def dias_del_mes(anio: int, mes: int, hoy: date) -> list[date]:
    ultimo = calendar.monthrange(anio, mes)[1]
    return [date(anio, mes, d) for d in range(1, ultimo + 1) if date(anio, mes, d) <= hoy]


def tarea_emitidos(op: Opciones, regs: Registros, res: Resumen, srv: EstadoServicio, mes: int,
                   avance: Callable[[str], Tarea]) -> Tarea:
    sufijo = "tablaCompEmitidos"
    yield from abrir_emitidos()
    dias = dias_del_mes(op.anio, mes, op.hoy)
    for tipo in op.tipos_emitidos:
        nombre = portal.TIPOS_PORTAL[tipo]
        cuenta = 0
        for d in dias:
            yield from avance(f"Emitidos {d:%d/%m/%Y}: {nombre}")
            r = yield Js(portal.consultar_emitidos(d.strftime("%d/%m/%Y"), tipo))
            if not r or not r.get("ok"):
                # la página pudo haberse recargado: reabrir una vez
                yield from abrir_emitidos()
                r = yield Js(portal.consultar_emitidos(d.strftime("%d/%m/%Y"), tipo))
                if not r or not r.get("ok"):
                    raise Falla((r or {}).get("e", "No se pudo consultar emitidos"))
            e = yield from esperar_consulta(sufijo, entera=True)
            if e.get("filas", 0) > 0:
                cuenta += yield from iterar_paginas(
                    sufijo, lambda cols, filas: procesar_emitidos(op, regs, res, srv, cols, filas))
            yield from pausa(op)
        yield Aviso(f"Emitidos {mes:02d}/{op.anio} · {nombre}: {cuenta}", "ok" if cuenta else "info")
        regs.guardar()


# --- Anulados -----------------------------------------------------------------

def _columna(cols: list[str], *palabras: str) -> int | None:
    for i, c in enumerate(cols):
        t = c.lower()
        if any(p in t for p in palabras):
            return i
    return None


def tarea_anulados(op: Opciones, regs: Registros, res: Resumen, mes: int, avance: Callable[[str], Tarea]) -> Tarea:
    yield from avance(f"Anulados {mes:02d}/{op.anio}")
    yield Navegar(portal.URL_ANULACION)
    enlace = "Consulta comprobantes anulados"
    yield from esperar_condicion(
        portal.hay(f"[...document.querySelectorAll('a')].some(a => a.innerText.trim() === '{enlace}')"),
        bool, 60, "abrir anulación")
    yield Js(portal.clic_texto(enlace))
    yield from esperar_condicion(
        portal.hay("[...document.querySelectorAll('select option')].some(o => /^enero$/i.test(o.text.trim()))"),
        bool, 60, "abrir consulta de anulados")
    r = yield Js(portal.consultar_anulados(op.anio, mes))
    if not r or not r.get("ok"):
        yield Aviso(f"Anulados {mes:02d}/{op.anio}: {(r or {}).get('e', 'no se pudo consultar')}", "alerta")
        return
    for _ in range(120):
        e = (yield Js(portal.estado_anulados())) or {}
        if e.get("libre") and (e.get("nuevas", 0) > 0 or e.get("mensajes")):
            break
        yield Esperar(500)
    yield Esperar(500)
    k = yield Js(portal.anulados_filas_por_pagina())
    if k:
        yield Esperar(1500)
        for _ in range(60):
            e = (yield Js(portal.estado_anulados())) or {}
            if e.get("libre"):
                break
            yield Esperar(500)
    tablas = (yield Js(portal.leer_anulados())) or []
    n = 0
    for t in tablas:
        cols = t.get("cols", [])
        i_id = _columna(cols, "identificación", "identificacion", "ruc")
        i_raz = _columna(cols, "razón", "razon")
        i_tipo = _columna(cols, "tipo")
        i_serie = _columna(cols, "serie")
        for celdas in t.get("filas", []):
            clave = buscar_clave(" ".join(celdas))
            if not clave:
                continue

            def c(i):
                return celdas[i] if i is not None and i < len(celdas) else ""

            regs.de(clave.fecha.year, clave.fecha.month).poner_anulado(clave.texto, {
                "lado": t.get("lado", ""), "identificacion": c(i_id), "razon": c(i_raz),
                "tipo": c(i_tipo) or clave.nombre_tipo, "serie": c(i_serie) or clave.serie,
            })
            n += 1
        pag = t.get("pag") or {}
        if pag.get("total", 0) > len(t.get("filas", [])):
            yield Aviso(f"Anulados {t.get('lado')}: el portal tiene {pag['total']} y solo se leyeron "
                        f"{len(t.get('filas', []))}.", "alerta")
    res.anulados += n
    yield Aviso(f"Anulados {mes:02d}/{op.anio}: {n}", "ok" if n else "info")


# --- Tarea completa -------------------------------------------------------------

def meses_validos(op: Opciones) -> list[int]:
    return [m for m in op.meses if date(op.anio, m, 1) <= op.hoy]


def total_pasos(op: Opciones) -> int:
    n = 0
    for m in meses_validos(op):
        if op.recibidos:
            n += len(op.tipos_recibidos)
        if op.emitidos:
            n += len(op.tipos_emitidos) * len(dias_del_mes(op.anio, m, op.hoy))
        if op.anulados:
            n += 1
    return max(n, 1)


def descargar(op: Opciones) -> Tarea:
    res = Resumen()
    regs = Registros(op)
    srv = EstadoServicio()
    total = total_pasos(op)
    hecho = [0]

    def avance(texto: str) -> Tarea:
        yield Progreso(hecho[0], total, texto)
        hecho[0] += 1

    emp = yield from comprobar_sesion()
    if emp.get("ruc") != op.ruc:
        raise Falla(f"La sesión abierta es de {emp.get('ruc')} {emp.get('razon', '')}, no de {op.ruc}.")
    meses = meses_validos(op)
    if not meses:
        raise Falla("Los meses elegidos todavía no han empezado.")
    yield Aviso(f"Empresa {op.ruc} {op.razon} · {op.anio}, meses {', '.join(f'{m:02d}' for m in meses)}")
    try:
        for mes in meses:
            if op.recibidos and op.tipos_recibidos:
                yield from tarea_recibidos(op, regs, res, mes, avance)
            if op.emitidos and op.tipos_emitidos:
                yield from tarea_emitidos(op, regs, res, srv, mes, avance)
            if op.anulados:
                yield from tarea_anulados(op, regs, res, mes, avance)
    finally:
        regs.guardar()
    yield Progreso(total, total, "Terminado")
    yield Aviso(
        f"Listo: {res.nuevos} nuevo(s) ({res.xml_portal} XML del portal, {res.xml_servicio} XML del servicio web, "
        f"{res.pdf} PDF), {res.existentes} ya existían, {res.anulados} anulado(s), {res.errores} error(es).",
        "ok" if not res.errores else "alerta")
    return res
