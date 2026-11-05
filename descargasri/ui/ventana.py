"""Ventana principal: navegador del SRI + panel de descarga."""

from __future__ import annotations

import html
from datetime import date
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QScrollArea, QSpinBox, QTextEdit,
                               QToolButton, QVBoxLayout, QWidget)

from .. import __version__, portal
from ..motor import Motor
from ..navegador import Navegador, NavegadorError, buscar_ejecutable
from ..tareas import Credenciales, Opciones, descargar

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


class Ventana(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{NOMBRE_APP} {__version__}")
        self.ajustes = QSettings("Leninsodu", "DescargasSRI")
        self.empresa: dict | None = None
        self._abierto_por_app = False

        datos = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
        self.nav = Navegador(datos / "perfil-navegador")

        self.motor = Motor(self.nav, self)
        self.motor.aviso.connect(self._log)
        self.motor.progreso.connect(self._progreso)
        self.motor.captcha.connect(self._captcha)
        self.motor.terminado.connect(self._terminado)

        panel = self._panel()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        self.setCentralWidget(scroll)
        self.resize(520, 900)
        self.setStyleSheet(ESTILO_BOTONES)

        self._reloj = QTimer(self)
        self._reloj.timeout.connect(self._detectar_empresa)
        self._reloj.start(3000)
        self._actualizar_botones()
        QTimer.singleShot(0, self._primera_vez)

    def _primera_vez(self):
        if self.ajustes.value("configurado", "") != "si":
            self._configurar()
        self._mostrar_navegador()

    def _configurar(self):
        from .asistente import Asistente

        if Asistente(self.ajustes, self).exec():
            self.carpeta.setText(str(self.ajustes.value("carpeta")))
            self._mostrar_navegador()

    def _mostrar_navegador(self):
        from ..navegador import nombre_navegador

        exe = str(self.ajustes.value("navegador", "") or "") or buscar_ejecutable()
        if exe and Path(exe).is_file() or (exe and not Path(exe).is_absolute()):
            self.lbl_navegador.setText(f"Navegador: {nombre_navegador(exe)}")
        else:
            self.lbl_navegador.setText("Navegador: ninguno — pulsa Configuración")

    # --- panel ---------------------------------------------------------------

    def _panel(self) -> QWidget:
        p = QWidget()
        p.setMinimumWidth(420)
        lay = QVBoxLayout(p)

        g = QGroupBox("Ingreso al SRI en línea")
        gl = QGridLayout(g)
        self.usuario = QLineEdit(str(self.ajustes.value("usuario", "")))
        self.usuario.setPlaceholderText("RUC o cédula")
        self.usuario.setMaxLength(13)
        self.ci_adicional = QLineEdit(str(self.ajustes.value("ci_adicional", "")))
        self.ci_adicional.setPlaceholderText("Solo si eres usuario adicional")
        self.ci_adicional.setMaxLength(10)
        self.clave = QLineEdit()
        self.clave.setEchoMode(QLineEdit.Password)
        self.clave.setPlaceholderText("Clave del SRI (no se guarda)")
        self.clave.returnPressed.connect(self._descargar)
        ver = QToolButton()
        ver.setText("👁")
        ver.setCheckable(True)
        ver.setToolTip("Mostrar u ocultar la clave")
        ver.toggled.connect(lambda v: self.clave.setEchoMode(QLineEdit.Normal if v else QLineEdit.Password))
        self.chk_recordar = QCheckBox("Recordar RUC (la clave nunca se guarda)")
        self.chk_recordar.setChecked(self.ajustes.value("recordar", "true") == "true")
        gl.addWidget(QLabel("RUC / C.I."), 0, 0)
        gl.addWidget(self.usuario, 0, 1, 1, 2)
        gl.addWidget(QLabel("C.I. adicional"), 1, 0)
        gl.addWidget(self.ci_adicional, 1, 1, 1, 2)
        gl.addWidget(QLabel("Clave"), 2, 0)
        gl.addWidget(self.clave, 2, 1)
        gl.addWidget(ver, 2, 2)
        gl.addWidget(self.chk_recordar, 3, 0, 1, 3)
        lay.addWidget(g)

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
        self.mes = QComboBox()
        self.mes.addItems(MESES)
        previo = hoy.month - 2 if hoy.month > 1 else 0
        self.mes.setCurrentIndex(int(self.ajustes.value("mes", previo)))
        gl.addWidget(QLabel("Mes"), 0, 0)
        gl.addWidget(self.mes, 0, 1)
        gl.addWidget(QLabel("Año"), 0, 2)
        gl.addWidget(self.anio, 0, 3)
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
        self.chk_revisar = QCheckBox("Volver a revisar todo en el SRI (más lento)")
        self.chk_revisar.setToolTip("Consulta otra vez los días de emitidos que ya se verificaron completos")
        gl.addWidget(self.chk_revisar, 4, 0, 1, 2)
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
        self.registro.setMinimumHeight(220)
        lay.addWidget(self.registro, 1)

        fila = QHBoxLayout()
        self.btn_ver = QPushButton("Ver navegador")
        self.btn_ver.setToolTip("Muestra la ventana de Brave que usa la app")
        self.btn_ver.clicked.connect(self._ver_navegador)
        fila.addWidget(self.btn_ver)
        lay.addLayout(fila)

        b = QPushButton("Configuración")
        b.setToolTip("Navegador y carpeta de destino")
        b.clicked.connect(self._configurar)
        fila.addWidget(b)

        self.btn_sesion = QPushButton("Cerrar sesión del SRI")
        self.btn_sesion.setObjectName("sesion")
        self.btn_sesion.clicked.connect(self._cerrar_sesion)
        fila.addWidget(self.btn_sesion)
        self.lbl_navegador = QLabel("")
        self.lbl_navegador.setStyleSheet("color:#666")
        lay.addWidget(self.lbl_navegador)
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

    def _asegurar_navegador(self) -> bool:
        if self.nav.disponible():
            return True
        exe = str(self.ajustes.value("navegador", "") or "") or None
        if exe and not Path(exe).is_file():
            exe = None
        try:
            self.nav.abrir(portal.INICIO, exe)
        except NavegadorError as e:
            QMessageBox.warning(self, NOMBRE_APP, str(e))
            return False
        self._abierto_por_app = True
        self.nav.minimizar()
        self.raise_()
        self.activateWindow()
        return True

    def _ver_navegador(self):
        if self._asegurar_navegador():
            self.nav.mostrar()

    def _cerrar_sesion(self):
        if self.motor.ocupado or not self.nav.disponible():
            return
        if QMessageBox.question(self, NOMBRE_APP, "¿Cerrar la sesión del SRI?") != QMessageBox.Yes:
            return
        try:
            self.nav.borrar_cookies()
            self.nav.navegar(portal.INICIO, 30)
        except NavegadorError as e:
            self._log(str(e), "error")
        self.empresa = None
        self.lbl_empresa.setText("Empresa: sin sesión")
        self._log("Sesión del SRI cerrada.", "info")
        self._actualizar_botones()

    def _detectar_empresa(self):
        if self.motor.ocupado:
            return
        if not self.nav.disponible():
            if self.empresa:
                self.empresa = None
                self.lbl_empresa.setText("Empresa: sin sesión")
                self._actualizar_botones()
            return
        try:
            v = self.nav.evaluar(portal.empresa(), 3)
        except NavegadorError:
            return
        emp = v.get("emp") if isinstance(v, dict) and not v.get("login") else None
        if emp and (not self.empresa or emp["ruc"] != self.empresa["ruc"]):
            self.empresa = emp
            self.lbl_empresa.setText(f"Sesión abierta: {emp['ruc']}\n{emp.get('razon', '')}")
            self._actualizar_botones()

    def _actualizar_botones(self):
        ocupado = self.motor.ocupado
        self.btn_descargar.setEnabled(not ocupado)
        self.btn_detener.setEnabled(ocupado)
        self.btn_sesion.setEnabled(not ocupado and self.empresa is not None)
        for w in (self.usuario, self.ci_adicional, self.clave, self.mes, self.anio):
            w.setEnabled(not ocupado)

    def _guardar_ajustes(self):
        a = self.ajustes
        a.setValue("anio", self.anio.value())
        a.setValue("mes", self.mes.currentIndex())
        a.setValue("recordar", "true" if self.chk_recordar.isChecked() else "false")
        a.setValue("usuario", self.usuario.text().strip() if self.chk_recordar.isChecked() else "")
        a.setValue("ci_adicional", self.ci_adicional.text().strip() if self.chk_recordar.isChecked() else "")
        for i, c in enumerate((self.chk_rec, self.chk_emi, self.chk_anu)):
            a.setValue(f"sec{i}", "true" if c.isChecked() else "false")
        for cod, c in self.chk_tipos.items():
            a.setValue(f"tipo{cod}", "true" if c.isChecked() else "false")
        a.setValue("pdf", "true" if self.chk_pdf.isChecked() else "false")
        a.setValue("carpeta", self.carpeta.text())

    def _descargar(self):
        if self.motor.ocupado:
            return
        usuario = self.usuario.text().strip()
        if not (usuario.isdigit() and len(usuario) in (10, 13)):
            QMessageBox.warning(self, NOMBRE_APP, "Escribe el RUC (13 dígitos) o la cédula (10 dígitos).")
            self.usuario.setFocus()
            return
        sesion_activa = self.empresa is not None and Credenciales(usuario, "").corresponde(self.empresa["ruc"])
        if not self.clave.text() and not sesion_activa:
            QMessageBox.warning(self, NOMBRE_APP, "Escribe la clave del SRI.")
            self.clave.setFocus()
            return
        tipos = [c for c, chk in self.chk_tipos.items() if chk.isChecked()]
        if not tipos and (self.chk_rec.isChecked() or self.chk_emi.isChecked()):
            QMessageBox.warning(self, NOMBRE_APP, "Elige al menos un tipo de comprobante.")
            return
        if not (self.chk_rec.isChecked() or self.chk_emi.isChecked() or self.chk_anu.isChecked()):
            QMessageBox.warning(self, NOMBRE_APP, "Elige qué descargar.")
            return
        if not self._asegurar_navegador():
            return
        self._guardar_ajustes()
        cred = Credenciales(usuario, self.clave.text(), self.ci_adicional.text().strip())
        self.clave.clear()  # la clave solo vive mientras se entra al SRI
        op = Opciones(
            base=Path(self.carpeta.text()),
            ruc=usuario,
            razon="",
            anio=self.anio.value(),
            meses=[self.mes.currentIndex() + 1],
            recibidos=self.chk_rec.isChecked(),
            emitidos=self.chk_emi.isChecked(),
            anulados=self.chk_anu.isChecked(),
            tipos_recibidos=[t for t in tipos if t in portal.TIPOS_RECIBIDOS],
            tipos_emitidos=[t for t in tipos if t in portal.TIPOS_EMITIDOS],
            con_pdf=self.chk_pdf.isChecked(),
            revisar_todo=self.chk_revisar.isChecked(),
        )
        self.registro.clear()
        self.barra.setValue(0)
        self.motor.iniciar(descargar(op, cred))
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
        self.empresa = None  # se vuelve a leer de la cabecera
        self._detectar_empresa()
        primera = msg.split("\n", 1)[0]
        self._log(primera, "ok" if ok else "error")
        self.lbl_paso.setText(primera)
        self._actualizar_botones()
        if ok:
            QMessageBox.information(self, NOMBRE_APP, msg)
        else:
            QMessageBox.warning(self, NOMBRE_APP, msg)

    def closeEvent(self, e):
        if self.motor.ocupado:
            r = QMessageBox.question(self, NOMBRE_APP, "Hay una descarga en curso. ¿Detenerla y salir?")
            if r != QMessageBox.Yes:
                e.ignore()
                return
            self.motor.detener()
            self.motor.esperar_fin(8000)
        self._guardar_ajustes()
        if self._abierto_por_app:
            try:  # cerrar el Brave que abrió la app (termina la sesión del SRI)
                self.nav._llamar_navegador("Browser.close", {})
            except Exception:
                pass
        self.nav.cerrar()
        super().closeEvent(e)
