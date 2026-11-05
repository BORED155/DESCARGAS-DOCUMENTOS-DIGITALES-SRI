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
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Generator

from . import almacen, portal, soap, verificacion, xmlinfo
from .clave import Clave, buscar_clave, leer_clave


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


@dataclass
class IngresoCorrecto:
    """El SRI aceptó usuario y clave (la ventana puede guardarlos si se pidió)."""
    ruc: str
    razon: str


@dataclass
class MostrarNavegador:
    """Pide el navegador con ventana. Devuelve True si estaba oculto y hubo que reabrirlo
    (en ese caso la sesión del SRI se perdió y hay que volver a entrar)."""


@dataclass
class BorrarSesion:
    """Cierra la sesión del SRI en el navegador (borra sus cookies)."""


@dataclass(repr=False)
class Credenciales:
    """Datos de ingreso que el usuario escribe en la app. No se guardan en disco."""
    usuario: str
    clave: str
    ci_adicional: str = ""
    verificar: bool = False  # entrar de nuevo aunque haya sesión, para comprobar la clave

    def __repr__(self) -> str:  # nunca mostrar la clave en registros
        return f"Credenciales(usuario={self.usuario!r}, ci_adicional={self.ci_adicional!r}, clave=***)"

    def corresponde(self, ruc: str) -> bool:
        u = self.usuario.strip()
        return ruc == u or (len(u) == 10 and ruc == u + "001")


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
    con_pdf: bool = False   # además del XML, el PDF (RIDE)
    solo_pdf: bool = False  # solo el PDF (RIDE), por ejemplo para imprimir
    pausa_ms: tuple[int, int] = (400, 1000)
    hoy: date = field(default_factory=date.today)
    reparados: list = field(default_factory=list, repr=False)  # archivos dañados que se volvieron a bajar
    revisar_todo: bool = False  # volver a consultar en el SRI los días de emitidos ya verificados
    dias_recientes: int = 3     # estos últimos días siempre se vuelven a consultar
    cred: "Credenciales | None" = field(default=None, repr=False)

    @property
    def carpeta(self) -> Path:
        return almacen.carpeta_empresa(self.base, self.ruc, self.razon)


@dataclass
class Resumen:
    conteos: dict = field(default_factory=dict)  # (mes, sección, tipo) -> Conteo
    completo: bool = False
    anulados_fallidos: bool = False
    diferencias_reporte: dict = field(default_factory=dict)  # mes -> [Diferencia]
    nuevos: int = 0
    existentes: int = 0
    errores: int = 0
    xml_portal: int = 0
    xml_servicio: int = 0
    pdf: int = 0
    anulados: int = 0

    def conteo(self, mes: int, seccion: str, tipo: str):
        k = (mes, seccion, tipo)
        if k not in self.conteos:
            self.conteos[k] = Conteo()
        return self.conteos[k]


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


def iniciar_sesion(cred: Credenciales, silencioso: bool = False) -> Tarea:
    """Entra al SRI con los datos del usuario (o reutiliza la sesión si ya es de ese RUC)."""
    if not silencioso:
        yield Aviso("Conectando con el SRI…")
    yield Navegar(portal.URL_RECIBIDOS)
    estado = None
    for _ in range(40):
        estado = (yield Js(portal.en_login())) or {}
        if estado.get("login"):
            break
        hay = yield Js(portal.hay("document.getElementById('frmPrincipal:btnBuscar')"))
        if hay:
            break
        yield Esperar(750)
    if not estado.get("login"):
        emp = yield from comprobar_sesion()
        if cred.corresponde(emp["ruc"]) and cred.verificar and cred.clave:
            yield Aviso("Se vuelve a entrar para comprobar la clave antes de guardarla.")
            yield BorrarSesion()
            yield Navegar(portal.URL_RECIBIDOS)
            yield from esperar_condicion(portal.hay("document.getElementById('kc-form-login')"), bool, 45,
                                         "abrir la página de ingreso")
        elif cred.corresponde(emp["ruc"]):
            if not silencioso:
                yield Aviso(f"Sesión ya abierta: {emp['ruc']} {emp.get('razon', '')}", "ok")
            return emp
        else:
            yield Aviso(f"Había una sesión de {emp['ruc']} {emp.get('razon', '')}; "
                        f"se cierra para entrar con {cred.usuario}.")
            yield BorrarSesion()
            yield Navegar(portal.URL_RECIBIDOS)
            yield from esperar_condicion(portal.hay("document.getElementById('kc-form-login')"), bool, 45,
                                         "abrir la página de ingreso")
    if not cred.clave:
        raise FallaFatal("El SRI pide entrar de nuevo: escribe la clave y pulsa Descargar.")
    for intento in (1, 2):
        r = yield Js(portal.ingresar(cred.usuario.strip(), cred.ci_adicional.strip(), cred.clave))
        if not r or not r.get("ok"):
            raise Falla((r or {}).get("e", "No se pudo llenar el ingreso del SRI."))
        yield Esperar(2000)
        error = ""
        for _ in range(60):
            e = (yield Js(portal.error_login())) or {}
            if not e.get("login"):
                break
            if e.get("error"):
                error = e["error"]
                break
            yield Esperar(750)
        else:
            raise Falla("El SRI no terminó el ingreso a tiempo.")
        if not error:
            break
        if intento == 1 and "tardado demasiado" in error.lower():
            # la página de ingreso caducó: se abre de nuevo y se reintenta
            yield Navegar(portal.URL_RECIBIDOS)
            yield from esperar_condicion(portal.hay("document.getElementById('kc-form-login')"), bool, 45,
                                         "abrir la página de ingreso")
            continue
        raise FallaFatal(f"El SRI no aceptó el ingreso: {error}")
    emp = yield from comprobar_sesion(30)
    if not cred.corresponde(emp["ruc"]):
        yield Aviso(f"Entraste como {emp['ruc']} {emp.get('razon', '')}.", "alerta")
    yield Aviso(("Se volvió a entrar al SRI: " if silencioso else "Ingreso correcto: ")
                + f"{emp['ruc']} {emp.get('razon', '')}", "ok")
    yield IngresoCorrecto(emp["ruc"], emp.get("razon", ""))
    return emp


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


def completar_con_pdf(reg: almacen.Registro, contenido: bytes) -> None:
    try:
        info = xmlinfo.leer_ride(contenido)
    except Exception:
        return
    reg.razon_contraparte = reg.razon_contraparte or info.razon_receptor
    reg.ruc_contraparte = reg.ruc_contraparte or info.id_receptor
    reg.total = reg.total or info.total


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


def contraparte_de_archivo(seccion: str, ruta: Path) -> str:
    """Nombre del proveedor (recibidos) o cliente/retenido (emitidos) leído del archivo."""
    try:
        cont = ruta.read_bytes()
        if ruta.suffix.lower() == ".pdf":
            return xmlinfo.leer_ride(cont).razon_receptor if seccion == "emitidos" else ""
        info = xmlinfo.leer(cont)
        return info.razon_receptor if seccion == "emitidos" else info.razon_emisor
    except Exception:
        return ""


def existente(op: Opciones, regs: "Registros", seccion: str, clave: Clave, ext: str,
              contraparte: str = "") -> Path | None:
    """Archivo ya guardado de ese comprobante (por el registro o con el nombre antiguo).

    Si está con el nombre antiguo (la clave), se renombra al formato legible."""
    encontrado = _existente(op, regs, seccion, clave, ext, contraparte)
    if encontrado is None or verificacion.archivo_valido(encontrado, clave.texto):
        return encontrado
    # No es el comprobante que debería (dañado o cambiado): se aparta y se vuelve a bajar.
    apartado = encontrado.with_name(encontrado.name + ".dañado")
    encontrado.replace(apartado)
    op.reparados.append(encontrado.name)
    return None


def existente_pdf(op: Opciones, regs: "Registros", seccion: str, clave: Clave, contraparte: str = "") -> Path | None:
    """PDF ya guardado. Con «Solo PDF» debe estar en la carpeta PDF: si está en otra, se copia."""
    encontrado = existente(op, regs, seccion, clave, ".pdf", contraparte)
    if not op.solo_pdf:
        return encontrado
    # buscar también uno ya puesto en la carpeta PDF (de una ejecución anterior)
    rmes = regs.de(clave.fecha.year, clave.fecha.month)
    previo = rmes.datos.get(clave.texto)
    for a in (previo.archivos if previo else []):
        r = op.carpeta / a
        if r.suffix.lower() == ".pdf" and "PDF" in r.parts and r.is_file() and \
                verificacion.archivo_valido(r, clave.texto):
            return r
    if encontrado is None:
        return None
    if "PDF" in encontrado.parts:
        return encontrado
    quien = contraparte or contraparte_de_archivo(seccion, encontrado)
    destino = almacen.ruta_pdf_agrupada(op.carpeta, seccion, clave, quien)
    if not destino.is_file():
        almacen.guardar_archivo(destino, encontrado.read_bytes())
    return destino


def ruta_pdf_nueva(op: Opciones, seccion: str, clave: Clave, contraparte: str) -> Path:
    if op.solo_pdf:
        return almacen.ruta_pdf_agrupada(op.carpeta, seccion, clave, contraparte)
    return almacen.ruta_legible(op.carpeta, seccion, clave, contraparte, ".pdf")


def _existente(op: Opciones, regs: "Registros", seccion: str, clave: Clave, ext: str,
               contraparte: str = "") -> Path | None:
    rmes = regs.de(clave.fecha.year, clave.fecha.month)
    previo = rmes.datos.get(clave.texto)
    if previo:
        for a in previo.archivos:
            r = op.carpeta / a
            if r.suffix.lower() == ext and r.is_file() and r.stem != clave.texto:
                return r
    viejo = almacen.ruta_comprobante(op.carpeta, seccion, clave, ext)
    if viejo.is_file():
        quien = contraparte or contraparte_de_archivo(seccion, viejo)
        nuevo = almacen.ruta_legible(op.carpeta, seccion, clave, quien, ext)
        if nuevo.exists():
            return nuevo
        viejo.rename(nuevo)
        if previo:
            previo.archivos = [x for x in previo.archivos if not x.endswith(viejo.name)]
        return nuevo
    if contraparte:
        nuevo = almacen.ruta_legible(op.carpeta, seccion, clave, contraparte, ext)
        if nuevo.is_file():
            return nuevo
    return None


def _rel(op: Opciones, ruta: Path) -> str:
    try:
        return str(ruta.relative_to(op.carpeta))
    except ValueError:
        return str(ruta)




class Cancelado(Exception):
    """El usuario pulsó Detener."""


# --- Errores y reintentos -------------------------------------------------------

class FallaFatal(Falla):
    """Error que no se arregla reintentando (clave incorrecta, sesión sin clave, año no permitido)."""


NO_HAY_DATOS = "no existen datos"
DIAS_REPASO = 10  # días después del cierre del mes en que conviene volver a revisar


def asegurar_sesion(op: Opciones) -> Tarea:
    """Tras un problema: comprueba la sesión y, si el SRI la cerró, vuelve a entrar."""
    if op.cred is not None and op.cred.clave:
        emp = yield from iniciar_sesion(op.cred, silencioso=True)
    else:
        try:
            emp = yield from comprobar_sesion(10)
        except Falla:
            raise FallaFatal("El SRI cerró la sesión. Escribe la clave y pulsa Descargar: "
                             "solo se bajará lo que falta.")
    if emp.get("ruc") != op.ruc:
        raise FallaFatal(f"La sesión del SRI cambió a {emp.get('ruc')}; se detiene para no mezclar empresas.")


def unidad(op: Opciones, desc: str, fabrica: Callable[[], Tarea], intentos: int = 3,
           espera_ms: int = 15_000) -> Tarea:
    """Ejecuta un paso (un tipo de un mes, un día de emitidos…) con reintentos.

    Si se agotan los intentos, avisa y devuelve None para seguir con el resto."""
    ultimo: Exception | None = None
    for intento in range(1, intentos + 1):
        try:
            return (yield from fabrica())
        except FallaFatal:
            raise
        except Falla as e:
            ultimo = e
            if intento < intentos:
                yield Aviso(f"{desc}: {e} Reintento {intento}/{intentos - 1} en {espera_ms // 1000} s.", "alerta")
                yield Esperar(espera_ms)
                yield from asegurar_sesion(op)
    yield Aviso(f"{desc}: no se pudo completar ({ultimo}). Vuelve a pulsar Descargar más tarde: "
                "solo se bajará lo que falta.", "error")
    return None


@dataclass
class Conteo:
    sri: int = 0        # lo que el SRI dice que hay
    leidos: int = 0     # filas que la app leyó
    guardados: int = 0  # comprobantes con archivo en la carpeta
    fallidos: list[str] = field(default_factory=list)  # pasos que no se pudieron completar

    @property
    def completo(self) -> bool:
        return not self.fallidos and self.guardados >= self.sri and self.leidos >= self.sri


# --- Recorrido por páginas ----------------------------------------------------

def iterar_paginas(sufijo: str, procesar: Callable[[list[str], list[dict]], Tarea]) -> Tarea:
    """Pone muchas filas por página, recorre las páginas y procesa cada una.

    El portal solo entrega el archivo de las filas visibles, por eso cada
    página se procesa antes de pedir la siguiente. Devuelve un Conteo."""
    t = (yield Js(portal.total(sufijo))) or {}
    if t.get("total", 0) > t.get("porPagina", 0):
        r = yield Js(portal.poner_filas_por_pagina(sufijo))
        if r and r.get("ok"):
            yield from esperar_consulta(sufijo, entera=False)
            t = (yield Js(portal.total(sufijo))) or t
    c = Conteo(sri=int(t.get("total", 0)))
    paginas = max(1, math.ceil(c.sri / max(1, t.get("porPagina") or 1)))
    for p in range(paginas):
        if p > 0:
            yield Js(portal.ir_a_pagina(sufijo, p))
            yield from esperar_consulta(sufijo, entera=False)
        datos = (yield Js(portal.leer_tabla(sufijo))) or {"cols": [], "filas": []}
        c.leidos += len(datos["filas"])
        c.guardados += yield from procesar(datos["cols"], datos["filas"])
    if c.leidos < c.sri:
        raise Falla(f"el SRI indicó {c.sri} pero la tabla mostró {c.leidos}.")
    if c.guardados < c.leidos:
        raise Falla(f"faltan {c.leidos - c.guardados} archivo(s) por bajar.")
    return c


FECHA_FUTURA = "menor a la fecha actual"  # el SRI no deja consultar emitidos de hoy


def sin_resultados(e: dict) -> bool:
    """La consulta terminó sin filas: solo es válido si el SRI dice que no hay datos."""
    if e.get("filas", 0) > 0:
        return False
    msg = (e.get("mensajes") or "").strip()
    if NO_HAY_DATOS in msg.lower() or FECHA_FUTURA in msg.lower() or not msg:
        return True
    raise Falla(f"el SRI respondió «{msg}».")


# --- Recibidos ----------------------------------------------------------------

def procesar_recibidos(op: Opciones, regs: Registros, res: Resumen, cols: list[str], filas: list[dict]) -> Tarea:
    """Baja el XML y/o el PDF de cada fila visible, según el formato elegido.

    Devuelve cuántas filas quedaron con los archivos pedidos."""
    pos = columnas(cols)
    quiere_xml = not op.solo_pdf
    quiere_pdf = op.con_pdf or op.solo_pdf
    seguidos = 0
    guardados = 0
    for f in filas:
        clave, reg = fila_a_registro("recibidos", pos, f.get("celdas", []))
        if not clave:
            continue
        ruta_xml = ruta_pdf = None
        if quiere_xml:
            ruta_xml = existente(op, regs, "recibidos", clave, ".xml", reg.razon_contraparte)
            if ruta_xml:
                res.existentes += 1
                reg.origen, reg.estado = "XML del portal", "Ya existía"
                completar_con_xml(reg, ruta_xml.read_bytes())
            elif f.get("xml"):
                status, tipo, cont = yield from bajar_link(f["xml"], "x" + clave.texto[-12:])
                if xmlinfo.es_autorizacion(cont):
                    completar_con_xml(reg, cont)
                    ruta_xml = almacen.ruta_legible(op.carpeta, "recibidos", clave, reg.razon_contraparte, ".xml")
                    almacen.guardar_archivo(ruta_xml, cont)
                    reg.origen, reg.estado = "XML del portal", "Descargado"
                    res.nuevos += 1
                    res.xml_portal += 1
                    seguidos = 0
                else:
                    reg.estado = f"Error: el portal no entregó el XML (HTTP {status}, {tipo.split(';')[0]})"
                    res.errores += 1
                    seguidos += 1
                yield from pausa(op)
        if quiere_pdf:
            ruta_pdf = existente_pdf(op, regs, "recibidos", clave, reg.razon_contraparte)
            if ruta_pdf:
                if not quiere_xml:
                    res.existentes += 1
                    reg.origen, reg.estado = "PDF RIDE", "Ya existía"
            elif f.get("pdf"):
                status, tipo, cont = yield from bajar_link(f["pdf"], "p" + clave.texto[-12:])
                if cont.startswith(b"%PDF"):
                    ruta_pdf = ruta_pdf_nueva(op, "recibidos", clave, reg.razon_contraparte)
                    almacen.guardar_archivo(ruta_pdf, cont)
                    res.pdf += 1
                    if not quiere_xml:
                        res.nuevos += 1
                        reg.origen, reg.estado = "PDF RIDE", "Descargado"
                    seguidos = 0
                else:
                    reg.estado = f"Error: el portal no entregó el PDF (HTTP {status})"
                    res.errores += 1
                    seguidos += 1
                yield from pausa(op)
        for r in (ruta_xml, ruta_pdf):
            if r and r.is_file():
                reg.archivos.append(_rel(op, r))
        if (not quiere_xml or (ruta_xml and ruta_xml.is_file())) and \
                (not quiere_pdf or (ruta_pdf and ruta_pdf.is_file())):
            guardados += 1
        regs.de(clave.fecha.year, clave.fecha.month).poner(reg)
        if seguidos >= 3:
            regs.guardar()
            raise Falla("el portal dejó de entregar los archivos (¿se cerró la sesión?).")
    return guardados


def _captcha_rechazado(e: dict) -> bool:
    return "captcha" in (e.get("mensajes") or "").lower()


def consultar_recibidos_verificado(op: Opciones, mes: int, tipo: str, reintentos: int = 2,
                                   espera_reintento_ms: int = 10_000) -> Tarea:
    """Consulta recibidos. Si el SRI rechaza el reCAPTCHA invisible («Captcha incorrecta»),
    reintenta y, si sigue, pide al usuario que pulse «Consultar» en la ventana del SRI."""
    sufijo = "tablaCompRecibidos"

    def intentar() -> Tarea:
        for intento in range(reintentos + 1):
            r = yield Js(portal.consultar_recibidos(op.anio, mes, tipo))
            if not r or not r.get("ok"):
                e = (r or {}).get("e", "No se pudo consultar recibidos")
                raise (FallaFatal if "no permite" in e else Falla)(e)
            e = yield from esperar_consulta(sufijo, entera=True)
            if not _captcha_rechazado(e):
                return e
            if intento < reintentos:
                yield Aviso(f"El SRI respondió «{e['mensajes']}»; reintento en {espera_reintento_ms // 1000} s.",
                            "alerta")
                yield Esperar(espera_reintento_ms)
        return None

    e = yield from intentar()
    if e is not None:
        return e
    # Con el navegador oculto el SRI puede no aceptar la verificación: se abre con ventana.
    if (yield MostrarNavegador()):
        yield Aviso("Sin ventana el SRI no aceptó la verificación; se abrió el navegador visible "
                    "y se vuelve a entrar.", "alerta")
        if op.cred is not None and op.cred.clave:
            yield from iniciar_sesion(op.cred, silencioso=True)
        else:
            raise FallaFatal("Se reabrió el navegador y hay que entrar de nuevo: escribe la clave y pulsa "
                             "Descargar (solo bajará lo que falta).")
        yield from abrir_recibidos()
        e = yield from intentar()
        if e is not None:
            return e
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


def abrir_recibidos() -> Tarea:
    hay = yield Js(portal.hay("document.getElementById('frmPrincipal:btnBuscar')"))
    if not hay:
        yield Navegar(portal.URL_RECIBIDOS)
        yield from esperar_condicion(portal.hay("document.getElementById('frmPrincipal:btnBuscar')"), bool, 60,
                                     "abrir comprobantes recibidos")


def recibidos_tipo(op: Opciones, regs: Registros, res: Resumen, mes: int, tipo: str) -> Tarea:
    sufijo = "tablaCompRecibidos"
    yield from abrir_recibidos()
    e = yield from consultar_recibidos_verificado(op, mes, tipo)
    if sin_resultados(e):
        return Conteo()
    c = yield from iterar_paginas(sufijo, lambda cols, filas: procesar_recibidos(op, regs, res, cols, filas))
    regs.guardar()
    return c


def tarea_recibidos(op: Opciones, regs: Registros, res: Resumen, mes: int, avance: Callable[[str], Tarea]) -> Tarea:
    yield Navegar(portal.URL_RECIBIDOS)
    for tipo in op.tipos_recibidos:
        nombre = portal.TIPOS_PORTAL[tipo]
        desc = f"Recibidos {mes:02d}/{op.anio} · {nombre}"
        yield from avance(desc)
        c = yield from unidad(op, desc, lambda: recibidos_tipo(op, regs, res, mes, tipo))
        total = res.conteo(mes, "recibidos", nombre)
        if c is None:
            total.fallidos.append("consulta del mes")
            continue
        total.sri, total.leidos, total.guardados = c.sri, c.leidos, c.guardados
        yield Aviso(f"{desc}: {c.sri}" if c.sri else f"{desc}: ninguno", "ok" if c.sri else "info")
        yield from pausa(op)


# --- Emitidos -----------------------------------------------------------------

@dataclass
class EstadoServicio:
    """Hasta qué fecha el servicio web ya no entrega XML (se aprende al vuelo)."""
    sin_xml_hasta: date | None = None
    fallos_seguidos: int = 0

    @property
    def caido(self) -> bool:
        return self.fallos_seguidos >= 3


def pedir_xml_servicio(srv: EstadoServicio, clave: Clave) -> Tarea:
    """XML autorizado desde el servicio web (con un reintento si no responde)."""
    for intento in (1, 2):
        try:
            resp = yield Python(soap.consultar, (clave.texto,))
        except Cancelado:
            raise
        except Exception as ex:  # red caída o servicio lento
            if intento == 1:
                yield Esperar(3000)
                continue
            srv.fallos_seguidos += 1
            if srv.caido:
                yield Aviso(f"El servicio web del SRI no responde ({ex}); el resto se baja en PDF.", "alerta")
            return None
        srv.fallos_seguidos = 0
        if resp.autorizado:
            return resp.xml
        if soap.fuera_de_rango(resp) and (not srv.sin_xml_hasta or clave.fecha > srv.sin_xml_hasta):
            srv.sin_xml_hasta = clave.fecha
            yield Aviso(f"El SRI ya no entrega el XML de comprobantes del {clave.fecha:%d/%m/%Y} "
                        "o anteriores; para esos se baja el PDF (RIDE).", "alerta")
        return None
    return None


def procesar_emitidos(op: Opciones, regs: Registros, res: Resumen, srv: EstadoServicio,
                      cols: list[str], filas: list[dict]) -> Tarea:
    """XML (servicio web) o PDF de cada fila visible. Devuelve cuántas quedaron con archivo."""
    pos = columnas(cols)
    guardados = 0
    for f in filas:
        clave, reg = fila_a_registro("emitidos", pos, f.get("celdas", []))
        if not clave:
            continue
        ruta_xml = existente(op, regs, "emitidos", clave, ".xml")
        ruta_pdf = existente_pdf(op, regs, "emitidos", clave)
        intentar_xml = not op.solo_pdf and not srv.caido and \
            not (srv.sin_xml_hasta and clave.fecha <= srv.sin_xml_hasta)
        if op.solo_pdf:
            ruta_xml = None  # en «solo PDF» no se usa el XML aunque exista
        if ruta_xml:
            res.existentes += 1
            reg.origen, reg.estado = "XML servicio web SRI", "Ya existía"
            completar_con_xml(reg, ruta_xml.read_bytes())
        else:
            xml = (yield from pedir_xml_servicio(srv, clave)) if intentar_xml else None
            if xml:
                completar_con_xml(reg, xml)
                ruta_xml = almacen.ruta_legible(op.carpeta, "emitidos", clave, reg.razon_contraparte, ".xml")
                almacen.guardar_archivo(ruta_xml, xml)
                reg.origen, reg.estado = "XML servicio web SRI", "Descargado"
                res.nuevos += 1
                res.xml_servicio += 1
            elif ruta_pdf:
                res.existentes += 1
                reg.origen, reg.estado = "PDF RIDE", "Ya existía"
            elif f.get("pdf"):
                status, tipo, cont = yield from bajar_link(f["pdf"], "p" + clave.texto[-12:])
                if cont.startswith(b"%PDF"):
                    completar_con_pdf(reg, cont)
                    ruta_pdf = ruta_pdf_nueva(op, "emitidos", clave, reg.razon_contraparte)
                    almacen.guardar_archivo(ruta_pdf, cont)
                    reg.origen, reg.estado = "PDF RIDE", "Descargado (sin XML)"
                    res.nuevos += 1
                    res.pdf += 1
                else:
                    reg.estado = f"Error: el portal no entregó el PDF (HTTP {status})"
                    res.errores += 1
            yield from pausa(op)
        if (op.con_pdf or op.solo_pdf) and not ruta_pdf and f.get("pdf"):
            _, _, cont = yield from bajar_link(f["pdf"], "p" + clave.texto[-12:])
            if cont.startswith(b"%PDF"):
                completar_con_pdf(reg, cont)
                ruta_pdf = ruta_pdf_nueva(op, "emitidos", clave, reg.razon_contraparte)
                almacen.guardar_archivo(ruta_pdf, cont)
                res.pdf += 1
            yield from pausa(op)
        if ruta_pdf and ruta_pdf.is_file():
            if not reg.razon_contraparte:
                completar_con_pdf(reg, ruta_pdf.read_bytes())
            reg.archivos.append(_rel(op, ruta_pdf))
        if ruta_xml and ruta_xml.is_file():
            reg.archivos.append(_rel(op, ruta_xml))
        tiene_pdf = bool(ruta_pdf and ruta_pdf.is_file())
        tiene_xml = bool(ruta_xml and ruta_xml.is_file())
        if (op.con_pdf or op.solo_pdf):
            completo = tiene_pdf  # se pidió el PDF (el XML, si el SRI lo da, es un extra)
        else:
            completo = tiene_xml or tiene_pdf
        if completo:
            guardados += 1
        regs.de(clave.fecha.year, clave.fecha.month).poner(reg)
    return guardados


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
    """Días que se pueden consultar en emitidos: el SRI solo acepta fechas anteriores a hoy."""
    ultimo = calendar.monthrange(anio, mes)[1]
    return [date(anio, mes, d) for d in range(1, ultimo + 1) if date(anio, mes, d) < hoy]


TIPO_PORTAL_A_CLAVE = {"1": "01", "2": "03", "3": "04", "4": "05", "5": "06", "6": "07"}


def dia_ya_verificado(op: Opciones, regs: Registros, d: date, tipo: str) -> int | None:
    """Si el día se verificó completo antes y sus archivos siguen en la carpeta, devuelve cuántos tenía."""
    if op.revisar_todo or (op.hoy - d).days < op.dias_recientes:
        return None
    rmes = regs.de(d.year, d.month)
    previo = rmes.dias_verificados.get(f"{tipo}|{d.isoformat()}")
    if previo is None:
        return None
    if previo == 0:
        return 0
    cod = TIPO_PORTAL_A_CLAVE.get(tipo)
    exige_pdf = op.solo_pdf or op.con_pdf
    presentes = 0
    for r in rmes.datos.values():
        c = leer_clave(r.clave)
        if r.seccion == "emitidos" and c and c.tipo == cod and c.fecha == d and \
                any((op.carpeta / x).is_file() and (not exige_pdf or x.lower().endswith(".pdf"))
                    and verificacion.archivo_valido(op.carpeta / x, r.clave) for x in r.archivos):
            presentes += 1
    return previo if presentes >= previo else None


def emitidos_dia(op: Opciones, regs: Registros, res: Resumen, srv: EstadoServicio, d: date, tipo: str) -> Tarea:
    sufijo = "tablaCompEmitidos"
    hay = yield Js(portal.hay("document.getElementById('frmPrincipal:calendarFechaDesde_input')"))
    if not hay:
        yield from abrir_emitidos()
    r = yield Js(portal.consultar_emitidos(d.strftime("%d/%m/%Y"), tipo))
    if not r or not r.get("ok"):
        raise Falla((r or {}).get("e", "no se pudo consultar emitidos."))
    e = yield from esperar_consulta(sufijo, entera=True)
    if sin_resultados(e):
        return Conteo()
    return (yield from iterar_paginas(
        sufijo, lambda cols, filas: procesar_emitidos(op, regs, res, srv, cols, filas)))


def tarea_emitidos(op: Opciones, regs: Registros, res: Resumen, srv: EstadoServicio, mes: int,
                   avance: Callable[[str], Tarea]) -> Tarea:
    yield from abrir_emitidos()
    dias = dias_del_mes(op.anio, mes, op.hoy)
    if op.hoy.year == op.anio and op.hoy.month == mes:
        yield Aviso(f"Mes en curso: los emitidos se consultan hasta ayer. Los de hoy ({op.hoy:%d/%m/%Y}) "
                    "se podrán consultar desde mañana (el SRI no lo permite el mismo día).")
    for tipo in op.tipos_emitidos:
        nombre = portal.TIPOS_PORTAL[tipo]
        total = res.conteo(mes, "emitidos", nombre)
        saltados = 0
        for d in dias:
            desc = f"Emitidos {d:%d/%m/%Y} · {nombre}"
            yield from avance(desc)
            previo = dia_ya_verificado(op, regs, d, tipo)
            if previo is not None:  # ya estaba completo: no se vuelve a consultar
                total.sri += previo
                total.leidos += previo
                total.guardados += previo
                saltados += 1
                continue
            c = yield from unidad(op, desc, lambda: emitidos_dia(op, regs, res, srv, d, tipo))
            if c is None:
                total.fallidos.append(f"{d:%d/%m}")
            else:
                total.sri += c.sri
                total.leidos += c.leidos
                total.guardados += c.guardados
                if c.completo:
                    regs.de(d.year, d.month).dias_verificados[f"{tipo}|{d.isoformat()}"] = c.sri
            yield from pausa(op)
        if saltados:
            yield Aviso(f"Emitidos {mes:02d}/{op.anio} · {nombre}: {saltados} día(s) ya verificados antes, "
                        "no se volvieron a consultar.")
        yield Aviso(f"Emitidos {mes:02d}/{op.anio} · {nombre}: {total.sri}", "ok" if total.sri else "info")
        regs.guardar()


# --- Anulados -----------------------------------------------------------------

def _columna(cols: list[str], *palabras: str) -> int | None:
    for i, c in enumerate(cols):
        t = c.lower()
        if any(p in t for p in palabras):
            return i
    return None


def _esperar_tablas_anulados(timeout_s: float = 60) -> Tarea:
    for _ in range(int(timeout_s * 2)):
        e = (yield Js(portal.estado_anulados())) or {}
        if e.get("nuevas", 0) > 0 and e.get("libre", True):
            yield Esperar(400)
            return e
        if e.get("mensajes"):
            msg = e["mensajes"]
            if NO_HAY_DATOS in msg.lower():
                return e
            raise Falla(f"el SRI respondió «{msg}».")
        yield Esperar(500)
    raise Falla("el portal no devolvió la lista de anulados.")


def anulados_mes(op: Opciones, regs: Registros, mes: int) -> Tarea:
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
        e = (r or {}).get("e", "no se pudo consultar anulados.")
        raise (FallaFatal if "no permite" in e else Falla)(e)
    yield from _esperar_tablas_anulados()
    n = 0
    vistos: set[str] = set()
    tablas = (yield Js(portal.leer_anulados())) or []
    pendientes = list(tablas)
    while pendientes:
        t = pendientes.pop(0)
        cols = t.get("cols", [])
        i_id = _columna(cols, "identificación", "identificacion", "ruc")
        i_raz = _columna(cols, "razón", "razon")
        i_tipo = _columna(cols, "tipo")
        i_serie = _columna(cols, "serie")
        for celdas in t.get("filas", []):
            clave = buscar_clave(" ".join(celdas))
            if not clave or clave.texto in vistos:
                continue
            vistos.add(clave.texto)

            def c(i):
                return celdas[i] if i is not None and i < len(celdas) else ""

            regs.de(clave.fecha.year, clave.fecha.month).poner_anulado(clave.texto, {
                "lado": t.get("lado", ""), "identificacion": c(i_id), "razon": c(i_raz),
                "tipo": c(i_tipo) or clave.nombre_tipo, "serie": c(i_serie) or clave.serie,
            })
            n += 1
        if t.get("hay_siguiente") and t.get("id"):
            paso = yield Js(portal.anulados_siguiente_pagina(t["id"]))
            if paso:
                yield from _esperar_tablas_anulados()
                otra = next((x for x in ((yield Js(portal.leer_anulados())) or []) if x.get("id") == t["id"]), None)
                if otra:
                    pendientes.insert(0, otra)
    return n


def tarea_anulados(op: Opciones, regs: Registros, res: Resumen, mes: int, avance: Callable[[str], Tarea]) -> Tarea:
    desc = f"Anulados {mes:02d}/{op.anio}"
    yield from avance(desc)
    n = yield from unidad(op, desc, lambda: anulados_mes(op, regs, mes))
    total = res.conteo(mes, "anulados", "Lista")
    if n is None:
        total.fallidos.append("consulta del mes")
        return
    total.sri = total.leidos = total.guardados = n
    res.anulados += n
    yield Aviso(f"{desc}: {n}", "ok" if n else "info")


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


def comprobacion(res: Resumen, regs: Registros, op: Opciones) -> Tarea:
    """Compara lo que dice el SRI con lo que quedó en la carpeta y lo deja en el registro.

    Devuelve si los comprobantes están completos (los anulados se informan aparte)."""
    todo_bien = True
    for (mes, seccion, tipo), c in sorted(res.conteos.items()):
        rmes = regs.de(op.anio, mes)
        rmes.comprobacion[f"{seccion}|{tipo}"] = {
            "sri": c.sri, "leidos": c.leidos, "guardados": c.guardados, "fallidos": c.fallidos,
            "completo": c.completo,
        }
        if seccion == "anulados":
            if c.fallidos:
                res.anulados_fallidos = True
                yield Aviso("⚠ Anulados: el SRI no devolvió la lista; vuelve a intentarlo más tarde.", "alerta")
            continue
        if c.completo and c.sri == 0:
            continue
        nombre = f"{seccion.capitalize()} · {tipo}"
        if c.completo:
            yield Aviso(f"✔ {nombre}: SRI {c.sri} · en carpeta {c.guardados}", "ok")
        else:
            todo_bien = False
            extra = f" · sin consultar: {', '.join(c.fallidos)}" if c.fallidos else ""
            yield Aviso(f"✘ {nombre}: SRI {c.sri} · en carpeta {c.guardados}{extra}", "error")
    # Revisión de cada archivo guardado (y, si hay, comparación con el reporte del SRI)
    for mes in sorted({m for (m, _s, _t) in res.conteos}):
        rmes = regs.de(op.anio, mes)
        revisados, difs = verificacion.revisar_archivos(op.carpeta, rmes)
        difs += res.diferencias_reporte.get(mes, [])
        rmes.diferencias = [d.como_dict() for d in difs]
        # un día con problemas deja de contar como verificado: se vuelve a consultar
        clave_a_portal = {v: k for k, v in TIPO_PORTAL_A_CLAVE.items()}
        for d in difs:
            c = leer_clave(d.clave)
            if c and d.seccion == "emitidos":
                rmes.dias_verificados.pop(f"{clave_a_portal.get(c.tipo)}|{c.fecha.isoformat()}", None)
        de_archivo = [d for d in difs if d.fuente == "Archivo"]
        if revisados and not de_archivo:
            yield Aviso(f"✔ Archivos revisados ({mes:02d}/{op.anio}): los {revisados} comprobantes tienen su "
                        "archivo correcto (clave y autorización).", "ok")
        for d in difs[:15]:
            yield Aviso(f"✘ {d.fuente}: {d.tipo} {d.serie} {d.contraparte} — {d.problema}", "error")
        if len(difs) > 15:
            yield Aviso(f"… y {len(difs) - 15} más: ver la hoja «Diferencias» del Excel índice.", "error")
        if difs:
            todo_bien = False
        # Facturas autorizadas tarde: recordar volver a revisar pasado el cierre del mes
        fin = date(op.anio, mes, calendar.monthrange(op.anio, mes)[1])
        repaso = fin + timedelta(days=DIAS_REPASO)
        rmes.comprobacion["_revisado"] = {"fecha": op.hoy.isoformat()}
        if op.hoy <= repaso:
            yield Aviso(f"Recordatorio: {mes:02d}/{op.anio} se revisó el {op.hoy:%d/%m/%Y}. Algunos proveedores "
                        f"autorizan con días de atraso; vuelve a pulsar Descargar desde el "
                        f"{repaso + timedelta(days=1):%d/%m/%Y} (solo bajará lo nuevo).", "alerta")
    return todo_bien


def descargar(op: Opciones, cred: Credenciales | None = None) -> Tarea:
    res = Resumen()
    regs = Registros(op)
    srv = EstadoServicio()
    total = total_pasos(op)
    hecho = [0]
    op.cred = cred

    def avance(texto: str) -> Tarea:
        yield Progreso(hecho[0], total, texto)
        hecho[0] += 1

    try:
        if cred is not None:
            emp = yield from iniciar_sesion(cred)
            op.ruc, op.razon = emp["ruc"], emp.get("razon", "")
        else:
            emp = yield from comprobar_sesion()
            if emp.get("ruc") != op.ruc:
                raise Falla(f"La sesión abierta es de {emp.get('ruc')} {emp.get('razon', '')}, no de {op.ruc}.")
        meses = meses_validos(op)
        if not meses:
            raise Falla("El mes elegido todavía no ha empezado.")
        yield Aviso(f"Empresa {op.ruc} {op.razon} · {', '.join(f'{m:02d}' for m in meses)}/{op.anio}")
        for mes in meses:
            inv = regs.de(op.anio, mes).inventario(op.carpeta)
            if inv:
                partes = ", ".join(f"{n} {t.lower()} {s}" for (s, t), n in sorted(inv.items()))
                yield Aviso(f"En la carpeta ya hay ({mes:02d}/{op.anio}): {partes}. "
                            "Solo se bajará lo que falte.")
            else:
                yield Aviso(f"La carpeta de {mes:02d}/{op.anio} está vacía: se baja todo.")
        try:
            for mes in meses:
                if op.recibidos and op.tipos_recibidos:
                    yield from tarea_recibidos(op, regs, res, mes, avance)
                if op.emitidos and op.tipos_emitidos:
                    yield from tarea_emitidos(op, regs, res, srv, mes, avance)
                if op.anulados:
                    yield from tarea_anulados(op, regs, res, mes, avance)
            if op.reparados:
                yield Aviso(f"Se encontraron {len(op.reparados)} archivo(s) dañados o cambiados; se apartaron "
                            "con la extensión .dañado y se volvieron a bajar del SRI.", "alerta")
            if op.solo_pdf:
                for mes in meses:
                    ruta, docs, pags, excl = almacen.consolidar_pdf(op.carpeta, regs.de(op.anio, mes), op.anio, mes)
                    if ruta:
                        yield Aviso(f"Consolidado para imprimir: {ruta.name} — {docs} documento(s), {pags} página(s)"
                                    + (f"; se dejaron fuera {excl} anulado(s)." if excl else "."), "ok")
            yield Progreso(total, total, "Comprobando")
            yield Aviso("Comprobación (lo que dice el SRI frente a lo que está en la carpeta):")
            res.completo = yield from comprobacion(res, regs, op)
        finally:
            regs.guardar()
    finally:
        if cred is not None:
            cred.clave = ""  # la clave solo vive en memoria mientras dura la descarga
        op.cred = None
    yield Aviso(
        f"{res.nuevos} nuevo(s) ({res.xml_portal} XML del portal, {res.xml_servicio} XML del servicio web, "
        f"{res.pdf} PDF), {res.existentes} ya existían, {res.anulados} anulado(s).", "info")
    if res.completo:
        yield Aviso("COMPLETO: todo lo que el SRI reporta para el periodo está en la carpeta."
                    + (" (Falta solo la lista de anulados.)" if res.anulados_fallidos else ""), "ok")
    else:
        yield Aviso("INCOMPLETO: faltan comprobantes. Pulsa Descargar otra vez (solo baja lo que falta).", "error")
    return res
