"""Brave (o Chrome/Chromium) con un perfil propio de la app, manejado por DevTools.

¿Por qué no un navegador incorporado? El SRI usa reCAPTCHA Enterprise invisible
y rechaza («Captcha incorrecta») al navegador de Qt. En un Brave/Chrome normal
la consulta pasa sin hacer nada especial, así que la app usa uno de verdad: el
usuario entra y resuelve cualquier captcha ahí mismo, y la app solo llena
filtros y pide archivos dentro de esa sesión.

El puerto de DevTools escucha solo en 127.0.0.1 y únicamente mientras esa
ventana esté abierta. El perfil está separado del perfil personal del usuario.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

PUERTO_DEFECTO = 9223


class NavegadorError(Exception):
    pass


def candidatos() -> list[str]:
    if sys.platform.startswith("win"):
        rutas = []
        for base in (os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                     os.environ.get("LOCALAPPDATA", "")):
            if not base:
                continue
            rutas += [
                str(Path(base) / "BraveSoftware/Brave-Browser/Application/brave.exe"),
                str(Path(base) / "Google/Chrome/Application/chrome.exe"),
            ]
        # Edge viene con Windows 10/11: queda como último recurso
        for base in (os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                     os.environ.get("PROGRAMFILES", r"C:\Program Files")):
            rutas.append(str(Path(base) / "Microsoft/Edge/Application/msedge.exe"))
        return rutas
    if sys.platform == "darwin":
        return ["/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    return ["brave-browser", "brave", "brave-browser-stable", "google-chrome", "google-chrome-stable",
            "chromium", "chromium-browser", "microsoft-edge", "microsoft-edge-stable"]


def buscar_ejecutable() -> str | None:
    for c in candidatos():
        if os.path.isabs(c):
            if os.path.isfile(c):
                return c
        else:
            r = shutil.which(c)
            if r:
                return r
    return None


def navegadores_instalados() -> list[tuple[str, str]]:
    """(nombre, ruta) de cada navegador compatible encontrado, en orden de preferencia."""
    vistos: set[str] = set()
    out = []
    for c in candidatos():
        ruta = c if os.path.isabs(c) and os.path.isfile(c) else (None if os.path.isabs(c) else shutil.which(c))
        if not ruta:
            continue
        real = os.path.realpath(ruta)
        if real in vistos:
            continue
        vistos.add(real)
        out.append((nombre_navegador(ruta), ruta))
    return out


def nombre_navegador(ruta: str) -> str:
    r = ruta.lower()
    return ("Brave" if "brave" in r else "Edge" if "edge" in r else "Chrome" if "chrome" in r else "Chromium")


def _json_http(url: str, timeout: float = 2.0, metodo: str = "GET"):
    req = urllib.request.Request(url, method=metodo)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _puerto_libre(p: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", p))
            return True
        except OSError:
            return False


def preparar_perfil(perfil: Path) -> None:
    """Desactiva el gestor de contraseñas del perfil de la app: la clave no se guarda en Brave."""
    pref = Path(perfil) / "Default" / "Preferences"
    datos: dict = {}
    if pref.is_file():
        try:
            datos = json.loads(pref.read_text(encoding="utf-8"))
        except ValueError:
            datos = {}
    datos["credentials_enable_service"] = False
    datos.setdefault("profile", {})["password_manager_enabled"] = False
    datos["profile"]["password_manager_leak_detection"] = False
    datos.setdefault("autofill", {})["profile_enabled"] = False
    pref.parent.mkdir(parents=True, exist_ok=True)
    pref.write_text(json.dumps(datos), encoding="utf-8")


class Navegador:
    """Conexión DevTools a una pestaña del navegador de la app."""

    def __init__(self, perfil: Path, puerto: int = PUERTO_DEFECTO):
        self.perfil = Path(perfil)
        self.puerto = int(os.environ.get("DESCARGASRI_PUERTO", puerto))  # para pruebas
        self.proceso: subprocess.Popen | None = None
        self._ws = None
        self._id_pestana: str | None = None
        self._n = 0
        self._candado = threading.RLock()

    # --- arranque --------------------------------------------------------

    def disponible(self) -> bool:
        try:
            _json_http(f"http://127.0.0.1:{self.puerto}/json/version", 1.0)
            return True
        except Exception:
            return False

    def abrir(self, url: str, ejecutable: str | None = None) -> str:
        """Abre el navegador (o reutiliza el ya abierto) y devuelve su nombre."""
        if self.disponible():
            self._asegurar_pestana(url)
            return "navegador ya abierto"
        exe = ejecutable or buscar_ejecutable()
        if not exe:
            raise NavegadorError("No encontré Brave, Google Chrome ni Microsoft Edge instalados. "
                                 "Instala Brave (brave.com) o Google Chrome y vuelve a intentar.")
        if not _puerto_libre(self.puerto):
            raise NavegadorError(f"El puerto {self.puerto} está ocupado por otro programa.")
        self.perfil.mkdir(parents=True, exist_ok=True)
        preparar_perfil(self.perfil)
        args = [exe, f"--remote-debugging-port={self.puerto}", "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={self.perfil}", "--no-first-run", "--no-default-browser-check",
                # que la página siga trabajando aunque la ventana esté minimizada
                "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
                "--window-size=1100,800", "--new-window", url]
        extra = {}
        if sys.platform.startswith("win"):
            extra["creationflags"] = 0x00000008  # DETACHED_PROCESS
        else:
            extra["start_new_session"] = True
        self.proceso = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **extra)
        for _ in range(60):
            if self.disponible():
                return nombre_navegador(exe)
            time.sleep(0.25)
        raise NavegadorError("El navegador no abrió su puerto de control.")

    def _pestanas(self) -> list[dict]:
        return [p for p in _json_http(f"http://127.0.0.1:{self.puerto}/json") if p.get("type") == "page"]

    def _asegurar_pestana(self, url: str | None = None) -> dict:
        pest = self._pestanas()
        if self._id_pestana:
            for p in pest:
                if p["id"] == self._id_pestana:
                    return p
        sri = [p for p in pest if "sri.gob.ec" in p.get("url", "")]
        if sri:
            elegida = sri[0]
        elif pest:
            elegida = pest[0]
        else:
            from urllib.parse import quote
            elegida = _json_http(f"http://127.0.0.1:{self.puerto}/json/new?{quote(url or 'about:blank', safe=':/?=&')}",
                                 metodo="PUT")
        if elegida["id"] != self._id_pestana:
            self._cerrar_ws()
            self._id_pestana = elegida["id"]
        return elegida

    # --- DevTools ------------------------------------------------------------

    def _cerrar_ws(self):
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
        self._ws = None

    def _conectar(self):
        import websocket  # websocket-client

        p = self._asegurar_pestana()
        if self._ws is None:
            self._ws = websocket.create_connection(p["webSocketDebuggerUrl"], timeout=30,
                                                   suppress_origin=True)

    def _llamar(self, metodo: str, params: dict, timeout: float = 60) -> dict:
        with self._candado:
            for intento in (1, 2):
                try:
                    self._conectar()
                    self._n += 1
                    ident = self._n
                    self._ws.settimeout(timeout)
                    self._ws.send(json.dumps({"id": ident, "method": metodo, "params": params}))
                    while True:
                        m = json.loads(self._ws.recv())
                        if m.get("id") == ident:
                            if "error" in m:
                                raise NavegadorError(m["error"].get("message", "error de DevTools"))
                            return m.get("result", {})
                except NavegadorError:
                    raise
                except Exception as e:
                    self._cerrar_ws()
                    self._id_pestana = None if intento == 1 else self._id_pestana
                    if intento == 2:
                        raise NavegadorError(f"Se perdió la conexión con el navegador: {e}") from e
        raise NavegadorError("sin respuesta")

    def evaluar(self, codigo: str, timeout: float = 60):
        """Ejecuta JavaScript en la página y devuelve el valor (JSON ya decodificado)."""
        r = self._llamar("Runtime.evaluate", {"expression": codigo, "returnByValue": True,
                                              "awaitPromise": True}, timeout)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            texto = (d.get("exception") or {}).get("description") or d.get("text", "error")
            raise NavegadorError(f"Error en la página: {texto.splitlines()[0]}")
        v = r.get("result", {}).get("value")
        if isinstance(v, str):
            try:
                return json.loads(v)
            except ValueError:
                return v
        return v

    def navegar(self, url: str, timeout: float = 90) -> bool:
        self._llamar("Page.navigate", {"url": url})
        time.sleep(1.0)
        limite = time.time() + timeout
        while time.time() < limite:
            try:
                if self.evaluar("document.readyState", 5) == "complete":
                    return True
            except NavegadorError:
                pass
            time.sleep(0.5)
        return False

    # --- ventana -----------------------------------------------------------

    def _llamar_navegador(self, metodo: str, params: dict) -> dict:
        """Orden al navegador (no a la pestaña): ventanas."""
        import websocket

        url = _json_http(f"http://127.0.0.1:{self.puerto}/json/version")["webSocketDebuggerUrl"]
        ws = websocket.create_connection(url, timeout=10, suppress_origin=True)
        try:
            ws.send(json.dumps({"id": 1, "method": metodo, "params": params}))
            while True:
                m = json.loads(ws.recv())
                if m.get("id") == 1:
                    if "error" in m:
                        raise NavegadorError(m["error"].get("message", "error"))
                    return m.get("result", {})
        finally:
            ws.close()

    def _ventana(self, estado: str) -> None:
        try:
            p = self._asegurar_pestana()
            w = self._llamar_navegador("Browser.getWindowForTarget", {"targetId": p["id"]})["windowId"]
            if estado == "normal":
                self._llamar_navegador("Browser.setWindowBounds", {"windowId": w, "bounds": {"windowState": "normal"}})
            else:
                self._llamar_navegador("Browser.setWindowBounds", {"windowId": w, "bounds": {"windowState": estado}})
        except Exception:
            pass

    def minimizar(self) -> None:
        self._ventana("minimized")

    def mostrar(self) -> None:
        self._ventana("normal")
        self.traer_al_frente()

    def url(self) -> str:
        try:
            return self._asegurar_pestana().get("url", "")
        except Exception:
            return ""

    def borrar_cookies(self) -> None:
        self._llamar("Network.clearBrowserCookies", {})

    def traer_al_frente(self) -> None:
        try:
            self._llamar("Page.bringToFront", {})
        except NavegadorError:
            pass

    def cerrar(self) -> None:
        self._cerrar_ws()
