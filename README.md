# Descargas Documentos Digitales SRI

Descarga masiva de comprobantes electrónicos del portal **SRI en línea** (Ecuador).
Aplicación de escritorio en Python + PySide6 (Linux y Windows).

## Cómo se usa
1. Pulsa **Abrir el SRI en Brave**: se abre una ventana de Brave (o Chrome) con un
   perfil propio de la app, separado del perfil personal. Entra ahí con tu RUC y clave
   (la app no ve ni guarda la clave).
2. La app detecta la empresa en la cabecera del portal.
3. Elige año, meses, qué descargar y los tipos de comprobante, y pulsa **Descargar**.
4. Si el SRI rechaza la verificación («Captcha incorrecta»), la app reintenta y, si
   sigue, te pide pulsar «Consultar» en la ventana del SRI; luego continúa sola.

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
    recibidos/<tipo>/<clave>.xml
    emitidos/<tipo>/<clave>.xml | .pdf
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
