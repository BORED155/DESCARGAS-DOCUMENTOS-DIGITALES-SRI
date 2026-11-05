# Descargas Documentos Digitales SRI

Descarga masiva de comprobantes electrónicos del portal **SRI en línea** (Ecuador).
Aplicación de escritorio en Python + PySide6 (Linux y Windows).

## Cómo se usa
1. Elige la empresa de la lista o escribe el **RUC o cédula**, la **C.I. adicional** (solo si
   eres usuario adicional) y la **clave del SRI**. Con «Guardar la clave en este equipo» la clave
   queda cifrada en el llavero del sistema (keyring), solo después de que el SRI la acepte; si no,
   se borra al terminar. «Olvidar» la quita.
2. Elige **mes y año**, qué descargar y los tipos de comprobante, y pulsa **Descargar**.
3. La app abre Brave (o Chrome) minimizado con un perfil propio (gestor de contraseñas
   desactivado), entra al SRI y descarga. Si ya hay una sesión de ese RUC la reutiliza; si es
   de otra empresa, la cierra primero.
4. Si el SRI rechaza la verificación («Captcha incorrecta»), la app reintenta y, si sigue,
   muestra la ventana de Brave para que pulses «Consultar»; luego continúa sola.

### ¿Por qué Brave y no un navegador dentro de la app?
La consulta de recibidos usa reCAPTCHA Enterprise invisible. Con el navegador de Qt el
SRI responde «Captcha incorrecta»; con Brave/Chrome la consulta pasa normalmente. La app
no intenta engañar ni saltar el captcha: usa un navegador real donde el usuario está
presente. El control de Brave es por DevTools en 127.0.0.1, solo mientras esa ventana
esté abierta.

## Qué baja
| Sección | Cómo | Resultado |
|---|---|---|
| Recibidos | una consulta por mes y tipo (500 filas por página) | XML del portal |
| Emitidos | una consulta por día (el portal no permite el mes) | XML del servicio web del SRI si es reciente; si no, PDF (RIDE) |
| Anulados | una consulta por mes | lista; se marcan en el índice |

## Carpetas
```
<carpeta>/<RUC> - <RAZÓN SOCIAL>/<AAAA-MM>/
    recibidos/<tipo>/AAAA-MM-DD PROVEEDOR Tipo 001-001-000000123.xml
    emitidos/<tipo>/AAAA-MM-DD CLIENTE Tipo 001-001-000000123.xml | .pdf
    registro.json
    indice AAAA-MM.xlsx
```
Lo que ya existe no se vuelve a bajar.

## Desarrollo
```
python3 -m venv .venv && .venv/bin/pip install -e .[dev]
.venv/bin/python -m pytest
.venv/bin/descargasri
```
Las pruebas usan solo datos ficticios.
