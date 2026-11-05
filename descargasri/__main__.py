"""Punto de entrada: abre la ventana con el navegador del SRI."""

from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication

    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setApplicationName("Descargas Documentos Digitales SRI")
    app.setOrganizationName("Leninsodu")
    app.setDesktopFileName("descargas-sri")
    from .ui.ventana import Ventana

    v = Ventana()
    v.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
