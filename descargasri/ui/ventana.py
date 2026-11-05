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
from .. import credenciales
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
        self.motor.ingreso.connect(self._ingreso_correcto)
        self._pendiente: tuple[str, str, str] | None = None

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

        antes = self._modo_navegador()
        if Asistente(self.ajustes, self).exec():
            self.carpeta.setText(str(self.ajustes.value("carpeta")))
            self._mostrar_navegador()
            if self._modo_navegador() != antes and self.nav.disponible() and not self.motor.ocupado:
                self.nav.cerrar_navegador()  # se abrirá en el modo nuevo al descargar
                self.empresa = None
                self.lbl_empresa.setText("Empresa: sin sesión")

    def _mostrar_navegador(self):
        from ..navegador import nombre_navegador

        exe = str(self.ajustes.value("navegador", "") or "") or buscar_ejecutable()
        if exe and Path(exe).is_file() or (exe and not Path(exe).is_absolute()):
            modo = {"oculto": "oculto", "minimizado": "minimizado", "visible": "visible"}[self._modo_navegador()]
            self.lbl_navegador.setText(f"Navegador: {nombre_navegador(exe)} ({modo})")
        else:
            self.lbl_navegador.setText("Navegador: ninguno — pulsa Configuración")

    # --- panel ---------------------------------------------------------------

    def _panel(self) -> QWidget:
        p = QWidget()
        p.setMinimumWidth(420)
        lay = QVBoxLayout(p)

        g = QGroupBox("Ingreso al SRI en línea")
        gl = QGridLayout(g)
        self.cmb_empresas = QComboBox()
        self.cmb_empresas.setToolTip("Empresas con las que ya entraste en este equipo")
        self.cmb_empresas.activated.connect(self._elegir_empresa)
        self.btn_olvidar = QPushButton("Olvidar")
        self.btn_olvidar.setToolTip("Quitar esta empresa de la lista y borrar su clave guardada")
        self.btn_olvidar.clicked.connect(self._olvidar_empresa)
        self.usuario = QLineEdit()
        self.usuario.setPlaceholderText("RUC o cédula")
        self.usuario.setMaxLength(13)
        self.ci_adicional = QLineEdit()
        self.ci_adicional.setPlaceholderText("Solo si eres usuario adicional")
        self.ci_adicional.setMaxLength(10)
        self.clave = QLineEdit()
        self.clave.setEchoMode(QLineEdit.Password)
        self.clave.setPlaceholderText("Clave del SRI")
        self.clave.returnPressed.connect(self._descargar)
        ver = QToolButton()
        ver.setText("👁")
        ver.setCheckable(True)
        ver.setToolTip("Mostrar u ocultar la clave")
        ver.toggled.connect(lambda v: self.clave.setEchoMode(QLineEdit.Normal if v else QLineEdit.Password))
        self.chk_guardar_clave = QCheckBox("Guardar la clave en este equipo")
        if credenciales.llavero_disponible():
            self.chk_guardar_clave.setToolTip(
                "Se guarda cifrada en el llavero del sistema (Llavero de GNOME / Administrador de "
                "credenciales de Windows), solo después de que el SRI la acepte.")
            self.chk_guardar_clave.setChecked(self.ajustes.value("guardar_clave", "false") == "true")
        else:
            self.chk_guardar_clave.setEnabled(False)
            self.chk_guardar_clave.setText("Guardar la clave (no disponible: el sistema no tiene llavero)")
        gl.addWidget(QLabel("Empresa"), 0, 0)
        gl.addWidget(self.cmb_empresas, 0, 1)
        gl.addWidget(self.btn_olvidar, 0, 2)
        gl.addWidget(QLabel("RUC / C.I."), 1, 0)
        gl.addWidget(self.usuario, 1, 1, 1, 2)
        gl.addWidget(QLabel("C.I. adicional"), 2, 0)
        gl.addWidget(self.ci_adicional, 2, 1, 1, 2)
        gl.addWidget(QLabel("Clave"), 3, 0)
        gl.addWidget(self.clave, 3, 1)
        gl.addWidget(ver, 3, 2)
        gl.addWidget(self.chk_guardar_clave, 4, 0, 1, 3)
        self._cargar_empresas()
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
        modo = self._modo_navegador()
        try:
            self.nav.abrir(portal.INICIO, exe, oculto=(modo == "oculto"))
        except NavegadorError as e:
            QMessageBox.warning(self, NOMBRE_APP, str(e))
            return False
        self._abierto_por_app = True
        self.motor.ocultar_navegador = modo != "visible"
        if modo == "minimizado":
            self.nav.minimizar()
        if modo != "visible":
            self.raise_()
            self.activateWindow()
        return True

    def _modo_navegador(self) -> str:
        m = str(self.ajustes.value("modo_navegador", "oculto") or "oculto")
        return m if m in ("oculto", "minimizado", "visible") else "oculto"

    # --- empresas guardadas -------------------------------------------------------

    def _empresas(self) -> list[dict]:
        import json
        try:
            lista = json.loads(str(self.ajustes.value("empresas", "[]")))
        except ValueError:
            lista = []
        # versiones anteriores guardaban solo un RUC
        viejo = str(self.ajustes.value("usuario", "") or "")
        if viejo and not any(e.get("usuario") == viejo for e in lista):
            lista.append({"usuario": viejo, "ci": str(self.ajustes.value("ci_adicional", "") or ""), "razon": ""})
            self.ajustes.remove("usuario")
            self.ajustes.remove("ci_adicional")
            self._guardar_empresas(lista)
        return lista

    def _guardar_empresas(self, lista: list[dict]) -> None:
        import json
        self.ajustes.setValue("empresas", json.dumps(lista, ensure_ascii=False))

    def _cargar_empresas(self, elegir: tuple[str, str] | None = None):
        lista = self._empresas()
        self.cmb_empresas.clear()
        self.cmb_empresas.addItem("— Nueva empresa —", None)
        for e in lista:
            llave = " 🔑" if credenciales.leer(e["usuario"], e.get("ci", "")) else ""
            nombre = f"{e.get('razon') or 'sin nombre'} · {e['usuario']}" + (f" ({e['ci']})" if e.get("ci") else "")
            self.cmb_empresas.addItem(nombre + llave, (e["usuario"], e.get("ci", "")))
        ultima = str(self.ajustes.value("ultima_empresa", "") or "")
        objetivo = elegir or (tuple(ultima.split("|", 1)) if ultima else None)
        if objetivo:
            for i in range(1, self.cmb_empresas.count()):
                if tuple(self.cmb_empresas.itemData(i)) == tuple(objetivo):
                    self.cmb_empresas.setCurrentIndex(i)
                    self._elegir_empresa(i)
                    break
        self.btn_olvidar.setEnabled(self.cmb_empresas.currentIndex() > 0)

    def _elegir_empresa(self, i: int):
        dato = self.cmb_empresas.itemData(i)
        self.btn_olvidar.setEnabled(dato is not None)
        if not dato:
            self.usuario.clear()
            self.ci_adicional.clear()
            self.clave.clear()
            self.usuario.setFocus()
            return
        usuario, ci = dato
        self.usuario.setText(usuario)
        self.ci_adicional.setText(ci)
        guardada = credenciales.leer(usuario, ci)
        self.clave.setText(guardada or "")
        if guardada:
            self.chk_guardar_clave.setChecked(True)
        else:
            self.clave.setFocus()
        self.ajustes.setValue("ultima_empresa", f"{usuario}|{ci}")

    def _olvidar_empresa(self):
        dato = self.cmb_empresas.currentData()
        if not dato:
            return
        usuario, ci = dato
        if QMessageBox.question(self, NOMBRE_APP, f"¿Quitar {usuario} de la lista y borrar su clave guardada?") \
                != QMessageBox.Yes:
            return
        credenciales.borrar(usuario, ci)
        self._guardar_empresas([e for e in self._empresas()
                                if (e["usuario"], e.get("ci", "")) != (usuario, ci)])
        self.ajustes.remove("ultima_empresa")
        self._cargar_empresas()
        self._elegir_empresa(0)
        self._log(f"Se olvidó {usuario} y su clave guardada.", "info")

    def _ingreso_correcto(self, ruc: str, razon: str):
        """El SRI aceptó la clave: recordar la empresa y, si se pidió, guardar la clave."""
        if not self._pendiente:
            return
        usuario, ci, clave = self._pendiente
        lista = [e for e in self._empresas() if (e["usuario"], e.get("ci", "")) != (usuario, ci)]
        lista.insert(0, {"usuario": usuario, "ci": ci, "razon": razon})
        self._guardar_empresas(lista)
        self.ajustes.setValue("ultima_empresa", f"{usuario}|{ci}")
        if clave:
            if credenciales.guardar(usuario, ci, clave):
                self._log("Clave guardada en el llavero del sistema.", "ok")
            else:
                self._log("No se pudo guardar la clave en el llavero del sistema.", "alerta")
        self._pendiente = (usuario, ci, "")
        self._cargar_empresas((usuario, ci))

    def _ver_navegador(self):
        if self.nav.disponible() and self.nav.es_oculto():
            if QMessageBox.question(
                    self, NOMBRE_APP,
                    "El navegador está trabajando oculto. Para mostrarlo hay que volver a abrirlo y "
                    "la sesión del SRI se cierra (al descargar, la app vuelve a entrar sola).\n\n¿Mostrarlo?") \
                    != QMessageBox.Yes:
                return
            try:
                self.nav.reabrir_visible(portal.INICIO)
            except NavegadorError as e:
                QMessageBox.warning(self, NOMBRE_APP, str(e))
                return
            self._abierto_por_app = True
            self.nav.mostrar()
            return
        if not self.nav.disponible():
            exe = str(self.ajustes.value("navegador", "") or "") or None
            try:
                self.nav.abrir(portal.INICIO, exe if exe and Path(exe).is_file() else None, oculto=False)
            except NavegadorError as e:
                QMessageBox.warning(self, NOMBRE_APP, str(e))
                return
            self._abierto_por_app = True
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
        for w in (self.usuario, self.ci_adicional, self.clave, self.mes, self.anio, self.cmb_empresas,
                  self.btn_olvidar, self.chk_guardar_clave):
            w.setEnabled(not ocupado)

    def _guardar_ajustes(self):
        a = self.ajustes
        a.setValue("anio", self.anio.value())
        a.setValue("mes", self.mes.currentIndex())
        a.setValue("guardar_clave", "true" if self.chk_guardar_clave.isChecked() else "false")
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
        ci = self.ci_adicional.text().strip()
        clave = self.clave.text()
        guardada = credenciales.leer(usuario, ci) if self.chk_guardar_clave.isChecked() else None
        quiere_guardar = self.chk_guardar_clave.isChecked() and bool(clave)
        # una clave nueva se comprueba con el SRI antes de guardarla
        cred = Credenciales(usuario, clave, ci, verificar=quiere_guardar and clave != guardada)
        self._pendiente = (usuario, ci, clave if quiere_guardar else "")
        if not self.chk_guardar_clave.isChecked():
            self.clave.clear()  # sin guardar: la clave solo vive mientras dura la descarga
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
        pendiente, self._pendiente = self._pendiente, None
        if not ok and "no aceptó el ingreso" in msg and pendiente:
            usuario, ci, _ = pendiente
            if credenciales.leer(usuario, ci) and QMessageBox.question(
                    self, NOMBRE_APP, "El SRI no aceptó la clave guardada (¿la cambiaste en el SRI?).\n"
                    "¿Borrar la clave guardada para escribir la nueva?") == QMessageBox.Yes:
                credenciales.borrar(usuario, ci)
                self.clave.clear()
                self._cargar_empresas((usuario, ci))
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
