"""Ejecuta las tareas (generadores) sobre una QWebEnginePage."""

from __future__ import annotations

import json
from concurrent.futures import Future, ThreadPoolExecutor

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

from . import portal
from .tareas import (Aviso, Cancelado, Captcha, Esperar, Falla, Js, JsAsync, Navegar, Progreso,
                     Python, Tarea)

TIMEOUT_JS_MS = 60_000
TIMEOUT_CARGA_MS = 90_000


class Motor(QObject):
    aviso = Signal(str, str)
    progreso = Signal(int, int, str)
    captcha = Signal(bool)
    terminado = Signal(bool, str)  # ok, mensaje

    def __init__(self, pagina: QWebEnginePage, parent=None):
        super().__init__(parent)
        self.pagina = pagina
        self._gen: Tarea | None = None
        self._turno = 0
        self._cancelar = False
        self._hilos = ThreadPoolExecutor(max_workers=1)
        self._vigia = QTimer(self)
        self._vigia.setSingleShot(True)
        self._vigia.timeout.connect(self._vencido)
        self._carga_conectada = False

    @property
    def ocupado(self) -> bool:
        return self._gen is not None

    def iniciar(self, gen: Tarea) -> None:
        if self._gen is not None:
            raise RuntimeError("Ya hay una descarga en curso")
        self._gen = gen
        self._cancelar = False
        QTimer.singleShot(0, lambda: self._avanzar(None))

    def detener(self) -> None:
        if self._gen is not None:
            self._cancelar = True
            QTimer.singleShot(0, lambda: self._avanzar(None))

    # --- núcleo -------------------------------------------------------------

    def _nuevo_turno(self) -> int:
        self._turno += 1
        self._vigia.stop()
        return self._turno

    def _avanzar(self, valor, error: BaseException | None = None) -> None:
        if self._gen is None:
            return
        turno = self._nuevo_turno()
        try:
            if self._cancelar:
                self._cancelar = False
                orden = self._gen.throw(Cancelado())
            elif error is not None:
                orden = self._gen.throw(error)
            else:
                orden = self._gen.send(valor)
        except StopIteration:
            self._fin(True, "Descarga terminada.")
            return
        except Cancelado:
            self._fin(False, "Descarga detenida. Lo ya bajado quedó guardado.")
            return
        except Falla as e:
            self._fin(False, str(e))
            return
        except Exception as e:  # error de programación: mostrarlo
            self._fin(False, f"Error inesperado: {type(e).__name__}: {e}")
            return
        self._ejecutar(orden, turno)

    def _fin(self, ok: bool, msg: str) -> None:
        self._vigia.stop()
        self._gen = None
        self._desconectar_carga()
        self.captcha.emit(False)
        self.terminado.emit(ok, msg)

    def _vencido(self) -> None:
        self._avanzar(None, Falla("El navegador no respondió a tiempo."))

    def _ejecutar(self, orden, turno: int) -> None:
        def seguir(v=None, err=None):
            if turno == self._turno:
                self._avanzar(v, err)

        if isinstance(orden, Js):
            self._vigia.start(TIMEOUT_JS_MS)
            self.pagina.runJavaScript(orden.codigo, QWebEngineScript.MainWorld,
                                      lambda r: seguir(_json(r)))
        elif isinstance(orden, JsAsync):
            self._vigia.start(int(orden.timeout_s * 1000) + 5000)
            self.pagina.runJavaScript(orden.codigo, QWebEngineScript.MainWorld,
                                      lambda _r: self._sondear(orden.ident, turno, 0, orden.timeout_s))
        elif isinstance(orden, Esperar):
            QTimer.singleShot(max(0, orden.ms), lambda: seguir(None))
        elif isinstance(orden, Navegar):
            self._vigia.start(TIMEOUT_CARGA_MS)
            self._desconectar_carga()

            def cargado(ok):
                self._desconectar_carga()
                seguir(ok)

            self._al_cargar = cargado
            self.pagina.loadFinished.connect(cargado)
            self._carga_conectada = True
            self.pagina.load(orden.url)
        elif isinstance(orden, Python):
            fut: Future = self._hilos.submit(orden.funcion, *orden.args)
            self._esperar_hilo(fut, turno)
        elif isinstance(orden, Aviso):
            self.aviso.emit(orden.texto, orden.nivel)
            QTimer.singleShot(0, lambda: seguir(None))
        elif isinstance(orden, Progreso):
            self.progreso.emit(orden.hecho, orden.total, orden.texto)
            QTimer.singleShot(0, lambda: seguir(None))
        elif isinstance(orden, Captcha):
            self.captcha.emit(orden.visible)
            QTimer.singleShot(0, lambda: seguir(None))
        else:
            QTimer.singleShot(0, lambda: seguir(None, TypeError(f"Orden desconocida: {orden!r}")))

    def _desconectar_carga(self) -> None:
        if self._carga_conectada:
            try:
                self.pagina.loadFinished.disconnect(self._al_cargar)
            except (RuntimeError, TypeError):
                pass
            self._carga_conectada = False

    def _sondear(self, ident: str, turno: int, transcurrido_ms: int, timeout_s: float) -> None:
        if turno != self._turno:
            return
        if self._cancelar:
            self._avanzar(None)
            return

        def recibido(r):
            if turno != self._turno:
                return
            v = _json(r)
            if v is not None:
                self._avanzar(v)
            elif transcurrido_ms > timeout_s * 1000:
                self._avanzar(None, Falla("La descarga del archivo tardó demasiado."))
            else:
                QTimer.singleShot(250, lambda: self._sondear(ident, turno, transcurrido_ms + 250, timeout_s))

        self.pagina.runJavaScript(portal.js_resultado(ident), QWebEngineScript.MainWorld, recibido)

    def _esperar_hilo(self, fut: Future, turno: int) -> None:
        if turno != self._turno:
            return
        if self._cancelar:
            self._avanzar(None)
            return
        if fut.done():
            ex = fut.exception()
            self._avanzar(None if ex else fut.result(), ex)
        else:
            QTimer.singleShot(150, lambda: self._esperar_hilo(fut, turno))


def _json(r):
    if r is None or r == "":
        return None
    if isinstance(r, str):
        try:
            return json.loads(r)
        except ValueError:
            return r
    return r
