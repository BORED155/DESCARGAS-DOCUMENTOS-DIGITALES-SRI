"""Asistente de primera vez: navegador y carpeta de destino."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QButtonGroup, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QRadioButton, QStackedWidget, QVBoxLayout,
                               QWidget)

from ..navegador import navegadores_instalados, nombre_navegador

ESTILO = """
QLabel#titulo { font-size:18px; font-weight:bold; }
QLabel#nota { color:#555; }
QFrame#caja { background:#f4f7fb; border:1px solid #d5dde8; border-radius:6px; }
QPushButton#siguiente { background:#2e7d32; color:white; font-weight:bold; padding:7px 18px; border-radius:4px; }
QPushButton#siguiente:disabled { background:#a5c9a7; }
QLabel#alerta { background:#fff3cd; color:#7a5200; border:1px solid #e0b84c; padding:8px; }
"""


def carpeta_por_defecto() -> str:
    return str(Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)) / "Descargas SRI")


class Asistente(QDialog):
    def __init__(self, ajustes: QSettings, parent=None):
        super().__init__(parent)
        self.ajustes = ajustes
        self.setWindowTitle("Descargas Documentos Digitales SRI — Configuración")
        self.setMinimumSize(560, 470)
        self.setStyleSheet(ESTILO)
        self.paginas = QStackedWidget()
        self.paginas.addWidget(self._bienvenida())
        self.paginas.addWidget(self._navegador())
        self.paginas.addWidget(self._carpeta())

        self.btn_atras = QPushButton("Atrás")
        self.btn_atras.clicked.connect(lambda: self._ir(self.paginas.currentIndex() - 1))
        self.btn_sig = QPushButton("Siguiente")
        self.btn_sig.setObjectName("siguiente")
        self.btn_sig.clicked.connect(self._siguiente)
        abajo = QHBoxLayout()
        self.lbl_paso = QLabel()
        self.lbl_paso.setObjectName("nota")
        abajo.addWidget(self.lbl_paso)
        abajo.addStretch(1)
        abajo.addWidget(self.btn_atras)
        abajo.addWidget(self.btn_sig)
        lay = QVBoxLayout(self)
        lay.addWidget(self.paginas, 1)
        lay.addLayout(abajo)
        self._ir(0)

    # --- páginas -----------------------------------------------------------

    def _pagina(self, titulo: str) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        l = QVBoxLayout(w)
        t = QLabel(titulo)
        t.setObjectName("titulo")
        l.addWidget(t)
        return w, l

    def _bienvenida(self) -> QWidget:
        w, l = self._pagina("Bienvenido")
        txt = QLabel(
            "Esta aplicación descarga de una sola vez los comprobantes electrónicos del "
            "<b>SRI en línea</b> de un mes:<br><br>"
            "• <b>Recibidos</b> (compras): facturas, retenciones, notas de crédito y débito, liquidaciones — en XML.<br>"
            "• <b>Emitidos</b> (ventas y retenciones hechas): XML si es reciente; si no, el PDF (RIDE).<br>"
            "• <b>Anulados</b>: la lista, marcada en el Excel índice.<br><br>"
            "Al final compara lo que reporta el SRI con lo que quedó en la carpeta y te dice si está "
            "<b>completo</b>.")
        txt.setWordWrap(True)
        txt.setTextFormat(Qt.RichText)
        l.addWidget(txt)
        caja = QFrame()
        caja.setObjectName("caja")
        cl = QVBoxLayout(caja)
        priv = QLabel("🔒 <b>Tu clave del SRI no se guarda.</b> La escribes cada vez; se usa solo para entrar "
                      "y se borra al terminar la descarga. No se envía a ningún otro lugar que no sea el SRI.")
        priv.setWordWrap(True)
        priv.setTextFormat(Qt.RichText)
        cl.addWidget(priv)
        l.addWidget(caja)
        l.addStretch(1)
        return w

    def _navegador(self) -> QWidget:
        w, l = self._pagina("Navegador para el SRI")
        exp = QLabel("El SRI solo deja consultar los comprobantes recibidos desde un navegador normal. "
                     "La aplicación abre uno <b>en segundo plano, con un perfil propio</b>: no toca tus "
                     "marcadores, contraseñas ni sesiones, y solo lo verás si el SRI pide algo.")
        exp.setWordWrap(True)
        exp.setTextFormat(Qt.RichText)
        l.addWidget(exp)
        self.caja_nav = QVBoxLayout()
        l.addLayout(self.caja_nav)
        self.grupo_nav = QButtonGroup(self)
        self.grupo_nav.buttonClicked.connect(lambda _b: self._actualizar())
        fila = QHBoxLayout()
        b = QPushButton("Volver a buscar")
        b.clicked.connect(self._llenar_navegadores)
        fila.addWidget(b)
        b = QPushButton("Elegir otro manualmente…")
        b.clicked.connect(self._elegir_manual)
        fila.addWidget(b)
        fila.addStretch(1)
        l.addLayout(fila)

        modo = QLabel("<b>¿Cómo trabaja el navegador?</b>")
        modo.setTextFormat(Qt.RichText)
        l.addWidget(modo)
        self.grupo_modo = QButtonGroup(self)
        actual = str(self.ajustes.value("modo_navegador", "oculto") or "oculto")
        for valor, texto in (
                ("oculto", "Oculto: sin ninguna ventana (recomendado). Si el SRI pide algo, se muestra solo."),
                ("minimizado", "Minimizado: aparece en la barra de tareas, sin taparte."),
                ("visible", "Visible: ver lo que hace la app en el SRI.")):
            r = QRadioButton(texto)
            r.setProperty("modo", valor)
            r.setChecked(valor == actual)
            self.grupo_modo.addButton(r)
            l.addWidget(r)
        l.addStretch(1)
        self._manual: str | None = None
        self._llenar_navegadores()
        return w

    def _llenar_navegadores(self):
        while self.caja_nav.count():
            it = self.caja_nav.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for b in self.grupo_nav.buttons():
            self.grupo_nav.removeButton(b)
        encontrados = navegadores_instalados()
        if self._manual and all(r != self._manual for _, r in encontrados):
            encontrados.append((nombre_navegador(self._manual), self._manual))
        guardado = self.ajustes.value("navegador", "")
        if not encontrados:
            a = QLabel("No encontré <b>Brave</b>, <b>Google Chrome</b> ni <b>Microsoft Edge</b> en esta computadora.<br>"
                       "Instala uno (recomendado: Brave) y pulsa «Volver a buscar».")
            a.setObjectName("alerta")
            a.setWordWrap(True)
            a.setTextFormat(Qt.RichText)
            self.caja_nav.addWidget(a)
            fila = QWidget()
            fl = QHBoxLayout(fila)
            fl.setContentsMargins(0, 0, 0, 0)
            for texto, url in (("Descargar Brave", "https://brave.com/download/"),
                               ("Descargar Google Chrome", "https://www.google.com/chrome/")):
                b = QPushButton(texto)
                b.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
                fl.addWidget(b)
            fl.addStretch(1)
            self.caja_nav.addWidget(fila)
        else:
            t = QLabel(f"Encontré {len(encontrados)} navegador(es) compatible(s). Elige cuál usar:")
            self.caja_nav.addWidget(t)
            elegido = False
            for i, (nombre, ruta) in enumerate(encontrados):
                r = QRadioButton(f"{nombre}{'   (recomendado)' if i == 0 else ''}\n{ruta}")
                r.setProperty("ruta", ruta)
                self.grupo_nav.addButton(r)
                self.caja_nav.addWidget(r)
                if ruta == guardado or (not guardado and i == 0) or ruta == self._manual:
                    r.setChecked(True)
                    elegido = True
            if not elegido:
                self.grupo_nav.buttons()[0].setChecked(True)
        self._actualizar()

    def _elegir_manual(self):
        ruta, _ = QFileDialog.getOpenFileName(self, "Elegir el programa del navegador (Brave, Chrome o Edge)")
        if ruta:
            self._manual = ruta
            self._llenar_navegadores()

    def _carpeta(self) -> QWidget:
        w, l = self._pagina("¿Dónde guardar los comprobantes?")
        exp = QLabel("Dentro de esta carpeta se crea una carpeta por empresa y, adentro, una por mes:\n\n"
                     "   1790000000001 - EMPRESA S.A./\n"
                     "      2026-06/  recibidos/  emitidos/  indice 2026-06.xlsx")
        exp.setWordWrap(True)
        l.addWidget(exp)
        fila = QHBoxLayout()
        self.carpeta = QLineEdit(str(self.ajustes.value("carpeta", carpeta_por_defecto())))
        b = QPushButton("Cambiar…")
        b.clicked.connect(self._cambiar_carpeta)
        fila.addWidget(self.carpeta, 1)
        fila.addWidget(b)
        l.addLayout(fila)
        l.addStretch(1)
        return w

    def _cambiar_carpeta(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta de destino", self.carpeta.text())
        if d:
            self.carpeta.setText(d)

    # --- navegación ------------------------------------------------------------

    def _ir(self, i: int):
        i = max(0, min(i, self.paginas.count() - 1))
        self.paginas.setCurrentIndex(i)
        self.lbl_paso.setText(f"Paso {i + 1} de {self.paginas.count()}")
        self.btn_atras.setEnabled(i > 0)
        self.btn_sig.setText("Empezar" if i == self.paginas.count() - 1 else "Siguiente")
        self._actualizar()

    def _actualizar(self):
        if not hasattr(self, "btn_sig") or not hasattr(self, "paginas"):
            return  # todavía se está armando la ventana
        if self.paginas.currentIndex() == 1:
            self.btn_sig.setEnabled(self.grupo_nav.checkedButton() is not None)
        else:
            self.btn_sig.setEnabled(True)

    def _siguiente(self):
        if self.paginas.currentIndex() < self.paginas.count() - 1:
            self._ir(self.paginas.currentIndex() + 1)
            return
        b = self.grupo_nav.checkedButton()
        self.ajustes.setValue("navegador", b.property("ruta") if b else "")
        m = self.grupo_modo.checkedButton()
        self.ajustes.setValue("modo_navegador", m.property("modo") if m else "oculto")
        self.ajustes.setValue("carpeta", self.carpeta.text().strip() or carpeta_por_defecto())
        self.ajustes.setValue("configurado", "si")
        self.accept()
