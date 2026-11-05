"""Ventana principal: navegador del SRI + panel de descarga."""

from __future__ import annotations

import html
from datetime import date
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWebEngineCore import (QWebEngineDownloadRequest, QWebEnginePage, QWebEngineProfile,
                                     QWebEngineScript)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplitter,
                               QTextEdit, QToolButton, QVBoxLayout, QWidget)

from .. import __version__, portal
from ..motor import Motor
from ..tareas import Opciones, descargar

NOMBRE_APP = "Descargas Documentos Digitales SRI"
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre",
         "Octubre", "Noviembre", "Diciembre"]
COLORES = {"info": "#333333", "ok": "#1b7a2e", "alerta": "#b26a00", "error": "#c62828"}

ESTILO_BOTONES = """
QPushButton#descargar { background:#2e7d32; color:white; font-weight:bold; padding:8px; border-radius:4px; }
QPushButton#descargar:disabled { background:#a5c9a7; }
QPushButton#detener { background:#c62828; color:white; font-weight:bold; padding:8px; border-radius:4px; }
QPushButton#detener:disabled { background:#e3a5a5; }
QPushButton#sesion { background:#222; color:white; padding:6px; border-radius:4px; }
QLabel#captcha { background:#fff3cd; color:#7a5200; border:1px solid #e0b84c; padding:8px; font-weight:bold; }
QLabel#empresa { font-size:13px; font-weight:bold; }
"""


class Pagina(QWebEnginePage):
    """Abre en la misma pestaña los enlaces que el SRI manda a ventana nueva."""

    def createWindow(self, _tipo):
        return self


class Ventana(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{NOMBRE_APP} {__version__}")
        self.resize(1450, 900)
        self.ajustes = QSettings("Leninsodu", "DescargasSRI")
        self.empresa: dict | None = None

        # Perfil sin disco (off-the-record): no se guardan cookies, historial ni claves.
        self.perfil = QWebEngineProfile(self)
        self.perfil.downloadRequested.connect(self._descarga_manual)
        self.vista = QWebEngineView(self)
        self.pagina = Pagina(self.perfil, self.vista)
        self.vista.setPage(self.pagina)

        self.motor = Motor(self.pagina, self)
        self.motor.aviso.connect(self._log)
        self.motor.progreso.connect(self._progreso)
        self.motor.captcha.connect(self._captcha)
        self.motor.terminado.connect(self._terminado)

        barra = QWidget()
        bl = QHBoxLayout(barra)
        bl.setContentsMargins(4, 2, 4, 2)
        for texto, fn in (("◀", self.vista.back), ("▶", self.vista.forward), ("⟳", self.vista.reload),
                          ("Inicio SRI", lambda: self.vista.load(QUrl(portal.INICIO)))):
            b = QToolButton()
            b.setText(texto)
            b.clicked.connect(fn)
            bl.addWidget(b)
        self.url = QLabel()
        self.url.setStyleSheet("color:#666")
        bl.addWidget(self.url, 1)
        self.vista.urlChanged.connect(lambda u: self.url.setText(u.toString()[:140]))

        izq = QWidget()
        il = QVBoxLayout(izq)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(0)
        il.addWidget(barra)
        il.addWidget(self.vista, 1)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(izq)
        split.addWidget(self._panel())
        split.setStretchFactor(0, 1)
        split.setSizes([1050, 400])
        self.setCentralWidget(split)
        self.setStyleSheet(ESTILO_BOTONES)

        self.vista.load(QUrl(portal.INICIO))
        self._reloj = QTimer(self)
        self._reloj.timeout.connect(self._detectar_empresa)
        self._reloj.start(2500)
        self._actualizar_botones()

    # --- panel ---------------------------------------------------------------

    def _panel(self) -> QWidget:
        p = QWidget()
        p.setMinimumWidth(360)
        lay = QVBoxLayout(p)

        ayuda = QLabel("1. Entra al SRI en línea en la página de la izquierda con tu RUC y clave "
                       "(la app no ve ni guarda tu clave).\n2. Elige periodo y qué bajar.\n3. Pulsa Descargar.")
        ayuda.setWordWrap(True)
        lay.addWidget(ayuda)

        self.lbl_empresa = QLabel("Empresa: sin sesión")
        self.lbl_empresa.setObjectName("empresa")
        self.lbl_empresa.setWordWrap(True)
        lay.addWidget(self.lbl_empresa)

        g = QGroupBox("Periodo")
        gl = QGridLayout(g)
        hoy = date.today()
        self.anio = QSpinBox()
        self.anio.setRange(2012, hoy.year)
        self.anio.setValue(int(self.ajustes.value("anio", hoy.year)))
        self.mes_desde = QComboBox()
        self.mes_hasta = QComboBox()
        self.mes_desde.addItems(MESES)
        self.mes_hasta.addItems(MESES)
        previo = hoy.month - 2 if hoy.month > 1 else 0
        self.mes_desde.setCurrentIndex(int(self.ajustes.value("mes_desde", previo)))
        self.mes_hasta.setCurrentIndex(int(self.ajustes.value("mes_hasta", previo)))
        gl.addWidget(QLabel("Año"), 0, 0)
        gl.addWidget(self.anio, 0, 1)
        gl.addWidget(QLabel("Desde"), 1, 0)
        gl.addWidget(self.mes_desde, 1, 1)
        gl.addWidget(QLabel("Hasta"), 2, 0)
        gl.addWidget(self.mes_hasta, 2, 1)
        lay.addWidget(g)

        g = QGroupBox("Qué descargar")
        gl = QGridLayout(g)
        self.chk_rec = QCheckBox("Recibidos (compras) – XML")
        self.chk_emi = QCheckBox("Emitidos (ventas y retenciones) – XML o PDF")
        self.chk_anu = QCheckBox("Anulados – lista")
        for i, c in enumerate((self.chk_rec, self.chk_emi, self.chk_anu)):
            c.setChecked(self.ajustes.value(f"sec{i}", "true") == "true")
            gl.addWidget(c, i, 0, 1, 2)
        lay.addWidget(g)

        g = QGroupBox("Tipos de comprobante")
        gl = QGridLayout(g)
        self.chk_tipos: dict[str, QCheckBox] = {}
        for i, (cod, nombre) in enumerate(portal.TIPOS_PORTAL.items()):
            c = QCheckBox(nombre + (" (solo emitidos)" if cod == "5" else ""))
            c.setChecked(self.ajustes.value(f"tipo{cod}", "false" if cod == "5" else "true") == "true")
            self.chk_tipos[cod] = c
            gl.addWidget(c, i // 2, i % 2)
        self.chk_pdf = QCheckBox("Guardar también el PDF (RIDE) de todo")
        self.chk_pdf.setChecked(self.ajustes.value("pdf", "false") == "true")
        gl.addWidget(self.chk_pdf, 3, 0, 1, 2)
        lay.addWidget(g)

        g = QGroupBox("Carpeta de destino")
        gl = QHBoxLayout(g)
        defecto = Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)) / "Descargas SRI"
        self.carpeta = QLineEdit(str(self.ajustes.value("carpeta", str(defecto))))
        b = QPushButton("Cambiar…")
        b.clicked.connect(self._elegir_carpeta)
        b2 = QPushButton("Abrir")
        b2.clicked.connect(self._abrir_carpeta)
        gl.addWidget(self.carpeta, 1)
        gl.addWidget(b)
        gl.addWidget(b2)
        lay.addWidget(g)

        fila = QHBoxLayout()
        self.btn_descargar = QPushButton("Descargar")
        self.btn_descargar.setObjectName("descargar")
        self.btn_descargar.clicked.connect(self._descargar)
        self.btn_detener = QPushButton("Detener")
        self.btn_detener.setObjectName("detener")
        self.btn_detener.clicked.connect(self.motor.detener)
        fila.addWidget(self.btn_descargar, 2)
        fila.addWidget(self.btn_detener, 1)
        lay.addLayout(fila)

        self.lbl_captcha = QLabel("El SRI pide resolver el captcha.\nResuélvelo en la página; la descarga sigue sola.")
        self.lbl_captcha.setObjectName("captcha")
        self.lbl_captcha.setWordWrap(True)
        self.lbl_captcha.hide()
        lay.addWidget(self.lbl_captcha)

        self.barra = QProgressBar()
        self.barra.setFormat("%p%")
        lay.addWidget(self.barra)
        self.lbl_paso = QLabel("")
        self.lbl_paso.setWordWrap(True)
        lay.addWidget(self.lbl_paso)

        self.registro = QTextEdit()
        self.registro.setReadOnly(True)
        self.registro.setFont(QFont("Monospace", 9))
        lay.addWidget(self.registro, 1)

        self.btn_sesion = QPushButton("Cerrar sesión / cambiar de empresa")
        self.btn_sesion.setObjectName("sesion")
        self.btn_sesion.clicked.connect(self._cerrar_sesion)
        lay.addWidget(self.btn_sesion)
        return p

    # --- acciones ----------------------------------------------------------------

    def _elegir_carpeta(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta de destino", self.carpeta.text())
        if d:
            self.carpeta.setText(d)

    def _abrir_carpeta(self):
        ruta = Path(self.carpeta.text())
        if self.empresa:
            from ..almacen import carpeta_empresa
            c = carpeta_empresa(ruta, self.empresa["ruc"], self.empresa.get("razon", ""))
            if c.is_dir():
                ruta = c
        ruta.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ruta)))

    def _cerrar_sesion(self):
        if self.motor.ocupado:
            return
        self.perfil.cookieStore().deleteAllCookies()
        self.perfil.clearHttpCache()
        self.empresa = None
        self.lbl_empresa.setText("Empresa: sin sesión")
        self.vista.load(QUrl(portal.INICIO))
        self._log("Sesión cerrada. Entra con la otra empresa.", "info")
        self._actualizar_botones()

    def _detectar_empresa(self):
        if self.motor.ocupado:
            return
        if portal.DOMINIO not in self.vista.url().host():
            return

        def listo(r):
            import json
            try:
                v = json.loads(r) if isinstance(r, str) else None
            except ValueError:
                v = None
            emp = v.get("emp") if v and not v.get("login") else None
            if emp and (not self.empresa or emp["ruc"] != self.empresa["ruc"]):
                self.empresa = emp
                self.lbl_empresa.setText(f"Empresa: {emp['ruc']}\n{emp.get('razon', '')}")
                self._log(f"Sesión detectada: {emp['ruc']} {emp.get('razon', '')}", "ok")
                self._actualizar_botones()

        self.pagina.runJavaScript(portal.empresa(), QWebEngineScript.MainWorld, listo)

    def _actualizar_botones(self):
        ocupado = self.motor.ocupado
        self.btn_descargar.setEnabled(not ocupado and self.empresa is not None)
        self.btn_detener.setEnabled(ocupado)
        self.btn_sesion.setEnabled(not ocupado)

    def _guardar_ajustes(self):
        a = self.ajustes
        a.setValue("anio", self.anio.value())
        a.setValue("mes_desde", self.mes_desde.currentIndex())
        a.setValue("mes_hasta", self.mes_hasta.currentIndex())
        for i, c in enumerate((self.chk_rec, self.chk_emi, self.chk_anu)):
            a.setValue(f"sec{i}", "true" if c.isChecked() else "false")
        for cod, c in self.chk_tipos.items():
            a.setValue(f"tipo{cod}", "true" if c.isChecked() else "false")
        a.setValue("pdf", "true" if self.chk_pdf.isChecked() else "false")
        a.setValue("carpeta", self.carpeta.text())

    def _descargar(self):
        if not self.empresa or self.motor.ocupado:
            return
        d, h = self.mes_desde.currentIndex() + 1, self.mes_hasta.currentIndex() + 1
        if h < d:
            QMessageBox.warning(self, NOMBRE_APP, "El mes «Hasta» es anterior al mes «Desde».")
            return
        tipos = [c for c, chk in self.chk_tipos.items() if chk.isChecked()]
        if not tipos and (self.chk_rec.isChecked() or self.chk_emi.isChecked()):
            QMessageBox.warning(self, NOMBRE_APP, "Elige al menos un tipo de comprobante.")
            return
        if not (self.chk_rec.isChecked() or self.chk_emi.isChecked() or self.chk_anu.isChecked()):
            QMessageBox.warning(self, NOMBRE_APP, "Elige qué descargar.")
            return
        self._guardar_ajustes()
        op = Opciones(
            base=Path(self.carpeta.text()),
            ruc=self.empresa["ruc"],
            razon=self.empresa.get("razon", ""),
            anio=self.anio.value(),
            meses=list(range(d, h + 1)),
            recibidos=self.chk_rec.isChecked(),
            emitidos=self.chk_emi.isChecked(),
            anulados=self.chk_anu.isChecked(),
            tipos_recibidos=[t for t in tipos if t in portal.TIPOS_RECIBIDOS],
            tipos_emitidos=[t for t in tipos if t in portal.TIPOS_EMITIDOS],
            con_pdf=self.chk_pdf.isChecked(),
        )
        self.registro.clear()
        self.barra.setValue(0)
        self.motor.iniciar(descargar(op))
        self._actualizar_botones()

    # --- señales del motor ------------------------------------------------------

    def _log(self, texto: str, nivel: str = "info"):
        color = COLORES.get(nivel, "#333")
        self.registro.append(f'<span style="color:{color}">{html.escape(texto)}</span>')
        try:
            from datetime import datetime
            ruta = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)) / "registro.log"
            ruta.parent.mkdir(parents=True, exist_ok=True)
            with ruta.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [{nivel}] {texto}\n")
        except OSError:
            pass

    def _progreso(self, hecho: int, total: int, texto: str):
        self.barra.setMaximum(max(total, 1))
        self.barra.setValue(hecho)
        self.lbl_paso.setText(texto)
        if hecho % 10 == 0 or hecho == total:
            self._log_archivo(f"progreso {hecho}/{total} {texto}")

    def _log_archivo(self, texto: str):
        try:
            from datetime import datetime
            ruta = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)) / "registro.log"
            with ruta.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [paso] {texto}\n")
        except OSError:
            pass

    def _captcha(self, visible: bool):
        self.lbl_captcha.setVisible(visible)
        if visible:
            self.raise_()
            self.activateWindow()

    def _terminado(self, ok: bool, msg: str):
        self._log(msg, "ok" if ok else "error")
        self.lbl_paso.setText(msg)
        self._actualizar_botones()
        if not ok:
            QMessageBox.information(self, NOMBRE_APP, msg)

    def _descarga_manual(self, d: QWebEngineDownloadRequest):
        carpeta = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
        d.setDownloadDirectory(carpeta)
        d.accept()
        self._log(f"Descarga manual: {Path(carpeta) / d.downloadFileName()}", "info")

    def closeEvent(self, e):
        if self.motor.ocupado:
            r = QMessageBox.question(self, NOMBRE_APP, "Hay una descarga en curso. ¿Detenerla y salir?")
            if r != QMessageBox.Yes:
                e.ignore()
                return
            self.motor.detener()
        self._guardar_ajustes()
        # liberar la página antes que el perfil (evita avisos de Qt al cerrar)
        self.vista.setPage(None)
        self.pagina.deleteLater()
        super().closeEvent(e)
