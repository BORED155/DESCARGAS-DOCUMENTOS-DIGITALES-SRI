# Descargas Documentos Digitales SRI

Descarga masiva de comprobantes electrónicos del portal **SRI en línea** (Ecuador).
Aplicación de escritorio en Python + PySide6 (Linux y Windows).

## Cómo se usa
1. Abre la app: a la izquierda está el portal del SRI. Entra con tu RUC y clave
   (la app no ve ni guarda la clave; el navegador interno no guarda nada en disco).
2. Elige año, meses, qué descargar y los tipos de comprobante.
3. Pulsa **Descargar**. Si el SRI muestra un captcha de imágenes, la app se pausa
   hasta que lo resuelvas.

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
