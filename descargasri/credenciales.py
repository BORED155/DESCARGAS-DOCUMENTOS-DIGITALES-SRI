"""Claves del SRI guardadas en el llavero del sistema operativo.

Linux: Secret Service (Llavero de GNOME / KWallet). Windows: Administrador de
credenciales. macOS: Llavero. Nunca se escriben en archivos de la aplicación;
si el sistema no tiene llavero, la opción de guardar la clave se desactiva.
"""

from __future__ import annotations

SERVICIO = "Descargas Documentos Digitales SRI"


def _cuenta(usuario: str, ci_adicional: str = "") -> str:
    usuario, ci_adicional = usuario.strip(), ci_adicional.strip()
    return f"{usuario}[ad]{ci_adicional}" if ci_adicional else usuario


def llavero_disponible() -> bool:
    try:
        import keyring
        from keyring.backends import fail

        kr = keyring.get_keyring()
        return not isinstance(kr, fail.Keyring) and getattr(kr, "priority", 1) > 0 and \
            "null" not in type(kr).__module__.lower()
    except Exception:
        return False


def guardar(usuario: str, ci_adicional: str, clave: str) -> bool:
    try:
        import keyring

        keyring.set_password(SERVICIO, _cuenta(usuario, ci_adicional), clave)
        return True
    except Exception:
        return False


def leer(usuario: str, ci_adicional: str = "") -> str | None:
    try:
        import keyring

        return keyring.get_password(SERVICIO, _cuenta(usuario, ci_adicional))
    except Exception:
        return None


def borrar(usuario: str, ci_adicional: str = "") -> None:
    try:
        import keyring

        keyring.delete_password(SERVICIO, _cuenta(usuario, ci_adicional))
    except Exception:
        pass
