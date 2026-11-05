"""Ejecuta las tareas (generadores) en un hilo, sobre el navegador de la app."""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QThread, Signal

from . import portal
from .navegador import Navegador, NavegadorError
from .tareas import (Aviso, BorrarSesion, Cancelado, Captcha, Esperar, Falla, Js, JsAsync, Navegar,
                     Progreso, Python, Tarea)


class _Hilo(QThread):
    def __init__(self, motor: "Motor", gen: Tarea):
        super().__init__()
        self.motor = motor
        self.gen = gen

    def run(self):
        self.motor._correr(self.gen)


class Motor(QObject):
    aviso = Signal(str, str)
    progreso = Signal(int, int, str)
    captcha = Signal(bool)
    terminado = Signal(bool, str)  # ok, mensaje

    def __init__(self, navegador: Navegador, parent=None):
        super().__init__(parent)
        self.nav = navegador
        self._hilo: _Hilo | None = None
        self._cancelar = threading.Event()
        self.ocultar_navegador = True

    @property
    def ocupado(self) -> bool:
        return self._hilo is not None and self._hilo.isRunning()

    def iniciar(self, gen: Tarea) -> None:
        if self.ocupado:
            raise RuntimeError("Ya hay una descarga en curso")
        self._cancelar.clear()
        self._hilo = _Hilo(self, gen)
        self._hilo.start()

    def detener(self) -> None:
        self._cancelar.set()

    def esperar_fin(self, ms: int = 5000) -> None:
        if self._hilo is not None:
            self._hilo.wait(ms)

    # --- dentro del hilo ---------------------------------------------------------

    def _dormir(self, segundos: float) -> None:
        fin = time.time() + segundos
        while time.time() < fin:
            if self._cancelar.is_set():
                raise Cancelado()
            time.sleep(min(0.1, max(0.0, fin - time.time())))

    def _orden(self, orden):
        if self._cancelar.is_set():
            raise Cancelado()
        if isinstance(orden, Js):
            return self.nav.evaluar(orden.codigo)
        if isinstance(orden, JsAsync):
            self.nav.evaluar(orden.codigo)
            limite = time.time() + orden.timeout_s
            while time.time() < limite:
                v = self.nav.evaluar(portal.js_resultado(orden.ident))
                if v is not None:
                    return v
                self._dormir(0.2)
            raise Falla("La descarga del archivo tardó demasiado.")
        if isinstance(orden, Esperar):
            self._dormir(orden.ms / 1000)
            return None
        if isinstance(orden, Navegar):
            return self.nav.navegar(orden.url)
        if isinstance(orden, Python):
            return orden.funcion(*orden.args)
        if isinstance(orden, Aviso):
            self.aviso.emit(orden.texto, orden.nivel)
            return None
        if isinstance(orden, Progreso):
            self.progreso.emit(orden.hecho, orden.total, orden.texto)
            return None
        if isinstance(orden, Captcha):
            self.captcha.emit(orden.visible)
            if orden.visible:
                self.nav.mostrar()
            elif self.ocultar_navegador:
                self.nav.minimizar()
            return None
        if isinstance(orden, BorrarSesion):
            self.nav.borrar_cookies()
            return None
        raise TypeError(f"Orden desconocida: {orden!r}")

    def _correr(self, gen: Tarea) -> None:
        valor, error = None, None
        ok, msg = True, "Descarga terminada."
        try:
            while True:
                try:
                    orden = gen.throw(error) if error is not None else gen.send(valor)
                except StopIteration:
                    break
                valor, error = None, None
                try:
                    valor = self._orden(orden)
                except Cancelado as e:
                    error = e
                except NavegadorError as e:
                    error = Falla(str(e))
                except Falla as e:
                    error = e
                except Exception as e:  # p. ej. red caída en el servicio web
                    error = e
        except Cancelado:
            ok, msg = False, "Descarga detenida. Lo ya bajado quedó guardado."
        except Falla as e:
            ok, msg = False, str(e)
        except Exception as e:
            ok, msg = False, f"Error inesperado: {type(e).__name__}: {e}"
        finally:
            gen.close()
        self.captcha.emit(False)
        self.terminado.emit(ok, msg)
