from __future__ import annotations

import base64
import json
import re
from datetime import date
from pathlib import Path

import pytest

from descargasri import almacen, portal, soap, tareas, xmlinfo
from descargasri.clave import buscar_clave, digito_modulo11, leer_clave

from fabrica import (RUC_CLIENTE, RUC_EMPRESA, RUC_PROVEEDOR, autorizacion, clave, factura_xml,
                     respuesta_soap)


# --- clave -----------------------------------------------------------------

def test_clave_partes_y_verificador():
    cl = clave(date(2026, 3, 2), "01", RUC_PROVEEDOR, "002", "010", 4567)
    c = leer_clave(cl)
    assert c is not None
    assert c.fecha == date(2026, 3, 2)
    assert c.tipo == "01" and c.nombre_tipo == "Factura" and c.carpeta_tipo == "facturas"
    assert c.ruc_emisor == RUC_PROVEEDOR
    assert c.serie == "002-010-000004567"
    assert c.verificador_ok


def test_modulo11_casos_especiales():
    # 11 → 0 y 10 → 1, según la ficha técnica
    for base in ("0" * 48, "1" * 48, "2602202601" + "9" * 38):
        d = digito_modulo11(base)
        assert 0 <= d <= 9


def test_buscar_clave_en_texto():
    cl = clave(date(2026, 10, 2), "07", RUC_PROVEEDOR)
    assert buscar_clave(f"Retención 001-001 {cl} 02/10/2026").texto == cl
    assert buscar_clave("123") is None
    assert leer_clave("9" * 49) is None  # fecha imposible


# --- servicio web --------------------------------------------------------------

def test_soap_autorizado_arma_xml_como_el_portal():
    cl = clave(date(2026, 10, 2), "01", RUC_EMPRESA)
    comp = factura_xml(cl, RUC_EMPRESA, "EMPRESA FICTICIA S.A.", RUC_CLIENTE, "CLIENTE & CIA", date(2026, 10, 2))
    r = soap.interpretar(respuesta_soap(cl, comp))
    assert r.autorizado
    assert xmlinfo.es_autorizacion(r.xml)
    info = xmlinfo.leer(r.xml)
    assert info.clave == cl
    assert info.razon_receptor == "CLIENTE & CIA"
    assert info.total == "115.00"


def test_soap_fuera_de_rango():
    cl = clave(date(2026, 3, 2), "01", RUC_EMPRESA)
    r = soap.interpretar(respuesta_soap(cl, None, "RECHAZADA",
                                        "No es posible validar la clave de acceso ya que la fecha de emision esta fuera del rango permitido."))
    assert not r.autorizado and soap.fuera_de_rango(r)
    r2 = soap.interpretar(respuesta_soap(cl, None))
    assert r2.estado == "SIN_RESULTADO" and soap.fuera_de_rango(r2)


def test_es_autorizacion_rechaza_html():
    assert not xmlinfo.es_autorizacion(b"<!DOCTYPE html><html><body>autorizacion</body></html>")
    assert not xmlinfo.es_autorizacion(b"%PDF-1.4")


# --- almacén ------------------------------------------------------------------------

def test_registro_fusiona_y_marca_anulados(tmp_path: Path):
    cl = clave(date(2026, 3, 5), "01", RUC_PROVEEDOR)
    for n in ("a.xml", "a.pdf"):
        (tmp_path / n).write_text("x")
    reg = almacen.RegistroMes(tmp_path / "2026-03")
    reg.poner(almacen.Registro(clave=cl, seccion="recibidos", tipo="Factura", total="10,50", archivos=["a.xml"]))
    reg.poner_anulado(cl, {"lado": "recibidos"})
    reg.guardar()
    otra = almacen.RegistroMes(tmp_path / "2026-03")
    otra.poner(almacen.Registro(clave=cl, seccion="recibidos", tipo="Factura", archivos=["a.pdf"]))
    assert otra.datos[cl].anulado
    assert otra.datos[cl].archivos == ["a.pdf", "a.xml"]
    ruta = almacen.escribir_indice(otra, 2026, 3)
    from openpyxl import load_workbook
    wb = load_workbook(ruta)
    assert wb.sheetnames == ["Resumen", "Recibidos", "Emitidos", "Anulados", "Comprobación"]
    assert wb["Recibidos"]["M2"].value == "Sí"


def test_nombre_seguro():
    assert almacen.nombre_seguro('EMPRESA: "X/Y" S.A.') == "EMPRESA X Y S.A"


# --- tareas con un portal simulado ------------------------------------------------------

class PortalFalso:
    """Imita las respuestas del portal del SRI a las órdenes de las tareas."""

    def __init__(self, recibidos: dict, emitidos: dict, anulados: list, por_pagina=50):
        self.recibidos = recibidos  # (mes, tipo) -> [(clave, xml bytes)]
        self.emitidos = emitidos    # (fecha, tipo) -> [clave]
        self.anulados = anulados    # [(lado, clave)]
        self.pagina = ""
        self.filas: list = []
        self.por_pagina = por_pagina
        self.pag = 0
        self.seccion = ""
        self.consultas = 0
        self.rechazos = 0       # cuántas consultas de recibidos responden «Captcha incorrecta»
        self.rechazado = False

    def js(self, codigo: str):
        t = codigo[len(portal.LIB):] if codigo.startswith(portal.LIB) else codigo
        if "D.empresa()" in t:
            return {"url": "x", "emp": {"ruc": RUC_EMPRESA, "razon": "EMPRESA FICTICIA S.A."}, "login": False}
        if "g('btnBuscar').click()" in t:
            mes = int(re.search(r"D\.elegir\(g\('mes'\), '(\d+)'\)", t).group(1))
            tipo = re.search(r"cmbTipoComprobante'\), '(\d)'\)", t).group(1)
            self._poner([(c, x) for c, x in self.recibidos.get((mes, tipo), [])], "rec")
            self.rechazado = self.rechazos > 0
            if self.rechazado:
                self.rechazos -= 1
            return {"ok": True}
        if t.strip().startswith("const D = window.__dds; D.marcar(); return true;") or "D.marcar(); return true;" in t:
            self.marcado_manual = getattr(self, "marcado_manual", 0) + 1
            self.rechazado = False  # el usuario pulsa Consultar
            return True
        if "g('btnConsultar').click()" in t:
            f = re.search(r"c\.value = '([\d/]+)'", t).group(1)
            tipo = re.search(r"cmbTipoComprobante'\), '(\d)'\)", t).group(1)
            self._poner([(c, None) for c in self.emitidos.get((f, tipo), [])], "emi")
            return {"ok": True}
        if "D.estado(" in t:
            if self.rechazado:
                return {"libre": True, "captcha": False, "filas": -1, "mensajes": "Captcha incorrecta"}
            n = len(self._visibles())
            return {"libre": True, "captcha": False, "filas": n,
                    "mensajes": "" if n else "No existen datos para los parámetros ingresados"}
        if "D.total(" in t:
            return {"total": len(self.filas), "porPagina": self.por_pagina,
                    "paginas": max(1, -(-len(self.filas) // self.por_pagina)), "pagina": self.pag}
        if "setRowsPerPage" in t:
            self.por_pagina = 500
            self.pag = 0
            return {"ok": True}
        if "setPage(" in t:
            self.pag = int(re.search(r"setPage\((\d+)\)", t).group(1))
            return {"ok": True}
        if "D.leer(" in t:
            if self.seccion == "rec":
                cols = ["Nro", "RUC y Razón social emisor", "Tipo y serie de comprobante",
                        "Clave de acceso / Nro. Autorización", "Fecha y hora de autorización", "Fecha emisión",
                        "Valor sin impuestos", "IVA", "Importe Total", "Documento", "RIDE", "Documentos relacionados"]
            else:
                cols = ["Nro", "Tipo y serie de comprobante", "Clave de acceso / Nro. Autorización",
                        "Fecha y hora de autorización", "Fecha emisión", "Valor sin impuestos", "IVA",
                        "Importe Total", "RIDE", "Documentos Relacionados"]
            filas = []
            base = self.pag * self.por_pagina
            for i, (c, _x) in enumerate(self._visibles()):
                k = base + i
                cel = [str(k + 1)]
                if self.seccion == "rec":
                    cel.append(f"{RUC_PROVEEDOR} PROVEEDOR FICTICIO")
                cel += ["Factura " + c[24:27] + "-" + c[27:30] + "-" + c[30:39], c, "02/03/2026 10:00:00",
                        f"{c[0:2]}/{c[2:4]}/{c[4:8]}", "100.00", "15.00", "115.00"]
                cel += ["", "", ""] if self.seccion == "rec" else ["", ""]
                filas.append({"celdas": cel,
                              "xml": f"frmPrincipal:tablaCompRecibidos:{k}:lnkXml" if self.seccion == "rec" else None,
                              "pdf": f"frmPrincipal:tabla:{k}:lnkPdf"})
            return {"cols": cols, "filas": filas}
        if "btnBuscar')" in t:
            return self.pagina == "rec"
        if "Comprobantes electrónicos emitidos'" in t and "some(" in t:
            return self.pagina == "consultas"
        if "D.clicTexto(" in t:
            self.pagina = "emi" if "emitidos" in t else "anu"
            return True
        if "calendarFechaDesde_input')" in t:
            return self.pagina == "emi"
        if "Consulta comprobantes anulados'" in t:
            return self.pagina == "anulacion"
        if "enero" in t and "select option" in t:
            return self.pagina == "anu"
        if "No encontré los filtros" in t:
            return {"ok": True}
        if "nuevas:" in t:
            return {"libre": True, "nuevas": 2, "total": 2, "mensajes": ""}
        if "rf-ds-btn-next');\n      if (!sig" in t:
            return False
        if "const out = []" in t:
            out = []
            for lado in ("emitidos", "recibidos"):
                filas = [["0000000000001", "X", "Factura", c[24:27] + "-" + c[27:30] + "-" + c[30:39], c]
                         for l, c in self.anulados if l == lado]
                out.append({"id": lado, "lado": lado,
                            "cols": ["Identificación receptor" if lado == "emitidos" else "RUC Emisor",
                                     "Razón social", "Tipo de comprobante", "Serie de comprobante", "Clave de acceso"],
                            "filas": filas, "hay_siguiente": False})
            return out
        raise AssertionError("JS no esperado: " + t[:200])

    def _poner(self, filas, seccion):
        self.filas = filas
        self.seccion = seccion
        self.por_pagina = 50
        self.pag = 0
        self.consultas += 1

    def _visibles(self):
        a = self.pag * self.por_pagina
        return self.filas[a:a + self.por_pagina]

    def bajar(self, codigo: str):
        link = re.search(r'D\.bajar\("([^"]+)"\)', codigo).group(1)
        k = int(link.split(":")[2])
        visibles = range(self.pag * self.por_pagina, self.pag * self.por_pagina + len(self._visibles()))
        if k not in visibles:  # como el portal real: solo filas visibles
            cont, tipo = b"<!DOCTYPE html><html></html>", "text/html"
        elif link.endswith("lnkXml"):
            cont, tipo = self.filas[k][1], "text/xml"
        else:
            cont, tipo = b"%PDF-1.4 ficticio", "application/pdf"
        return {"ok": True, "v": {"status": 200, "tipo": tipo, "b64": base64.b64encode(cont).decode()}}

    def navegar(self, url: str):
        self.pagina = {portal.URL_RECIBIDOS: "rec", portal.URL_CONSULTAS: "consultas",
                       portal.URL_ANULACION: "anulacion"}[url]


def ejecutar(gen, falso: PortalFalso, avisos: list):
    valor = None
    while True:
        try:
            orden = gen.send(valor)
        except StopIteration as fin:
            return fin.value
        valor = None
        if isinstance(orden, tareas.Js):
            valor = json.loads(json.dumps(falso.js(orden.codigo)))
        elif isinstance(orden, tareas.JsAsync):
            valor = falso.bajar(orden.codigo)
        elif isinstance(orden, tareas.Navegar):
            falso.navegar(orden.url)
            valor = True
        elif isinstance(orden, tareas.Python):
            valor = orden.funcion(*orden.args)
        elif isinstance(orden, tareas.Aviso):
            avisos.append(orden.texto)


@pytest.fixture
def escenario(tmp_path, monkeypatch):
    rec = []
    for i in range(53):  # 53 facturas: dos páginas de 50, como marzo
        cl = clave(date(2026, 3, 1 + i % 28), "01", RUC_PROVEEDOR, sec=i + 1)
        rec.append((cl, autorizacion(cl, factura_xml(cl, RUC_PROVEEDOR, "PROVEEDOR FICTICIO", RUC_EMPRESA,
                                                         "EMPRESA FICTICIA S.A.", date(2026, 3, 1)))))
    emi_reciente = clave(date(2026, 3, 2), "01", RUC_EMPRESA, "001", "002", 454)
    emi_viejo = clave(date(2026, 3, 2), "01", RUC_EMPRESA, "001", "002", 455)
    ret = clave(date(2026, 3, 3), "07", RUC_EMPRESA, "001", "002", 12)
    falso = PortalFalso(
        recibidos={(3, "1"): rec},
        emitidos={("02/03/2026", "1"): [emi_reciente, emi_viejo], ("03/03/2026", "6"): [ret]},
        anulados=[("recibidos", rec[0][0])],
    )

    def soap_falso(cl, timeout=30.0):
        if cl == emi_reciente:
            comp = factura_xml(cl, RUC_EMPRESA, "EMPRESA FICTICIA S.A.", RUC_CLIENTE, "CLIENTE FICTICIO", date(2026, 3, 2))
            return soap.interpretar(respuesta_soap(cl, comp))
        return soap.interpretar(respuesta_soap(cl, None, "RECHAZADA", "fecha de emision esta fuera del rango permitido"))

    monkeypatch.setattr(soap, "consultar", soap_falso)
    op = tareas.Opciones(base=tmp_path, ruc=RUC_EMPRESA, razon="EMPRESA FICTICIA S.A.", anio=2026, meses=[3],
                         tipos_recibidos=["1"], tipos_emitidos=["1", "6"], pausa_ms=(0, 0), hoy=date(2026, 3, 4))
    return falso, op, rec, emi_reciente, emi_viejo, ret


def test_descarga_completa_simulada(escenario):
    falso, op, rec, emi_reciente, emi_viejo, ret = escenario
    avisos: list = []
    res = ejecutar(tareas.descargar(op), falso, avisos)
    assert res.xml_portal == 53            # las dos páginas (se pide 500 por página)
    assert res.errores == 0
    mes = op.carpeta / "2026-03"
    assert len(list((mes / "recibidos" / "facturas").glob("*.xml"))) == 53
    # emitido reciente: XML del servicio web; el que el SRI ya no da: PDF
    assert (mes / "emitidos" / "facturas" / "2026-03-02 CLIENTE FICTICIO Factura 001-002-000000454.xml").is_file()
    assert (mes / "emitidos" / "facturas" / "2026-03-02 SIN NOMBRE Factura 001-002-000000455.pdf").is_file()
    assert (mes / "emitidos" / "retenciones" / "2026-03-03 SIN NOMBRE Retención 001-002-000000012.pdf").is_file()
    assert (mes / "recibidos" / "facturas" / "2026-03-01 PROVEEDOR FICTICIO Factura 001-001-000000001.xml").is_file()
    assert res.xml_servicio == 1 and res.anulados == 1
    datos = json.loads((mes / "registro.json").read_text(encoding="utf-8"))
    assert datos["comprobantes"][rec[0][0]]["anulado"] is True
    assert datos["comprobantes"][emi_reciente]["razon_contraparte"] == "CLIENTE FICTICIO"
    assert (mes / "indice 2026-03.xlsx").is_file()
    assert res.completo
    assert any(a.startswith("COMPLETO") for a in avisos)
    assert any("✔ Recibidos · Factura: SRI 53 · en carpeta 53" in a for a in avisos)
    assert datos["comprobacion"]["recibidos|Factura"]["completo"] is True

    # segunda vez: nada se vuelve a bajar
    res2 = ejecutar(tareas.descargar(op), PortalFalso(falso.recibidos, falso.emitidos, falso.anulados), [])
    assert res2.nuevos == 0 and res2.existentes == 56


def test_sesion_de_otra_empresa(escenario):
    falso, op, *_ = escenario
    op.ruc = "0999999999001"
    with pytest.raises(tareas.Falla):
        ejecutar(tareas.descargar(op), falso, [])


def test_dias_del_mes_no_pasa_de_hoy():
    assert len(tareas.dias_del_mes(2026, 2, date(2026, 11, 5))) == 28
    assert tareas.dias_del_mes(2026, 11, date(2026, 11, 5))[-1] == date(2026, 11, 5)


def test_columnas_por_titulo():
    pos = tareas.columnas(["Nro", "RUC y Razón social emisor", "Tipo y serie de comprobante",
                           "Clave de acceso / Nro. Autorización", "Fecha y hora de autorización", "Fecha emisión",
                           "Valor sin impuestos", "IVA", "Importe Total"])
    assert pos == {"emisor": 1, "tipo_serie": 2, "clave": 3, "fecha_aut": 4, "fecha_emi": 5,
                   "sin_imp": 6, "iva": 7, "total": 8}


def test_captcha_rechazado_reintenta_y_pide_al_usuario(escenario):
    falso, op, rec, *_ = escenario
    op.emitidos = op.anulados = False
    falso.rechazos = 3  # 1 consulta + 2 reintentos rechazados → se pide al usuario
    avisos: list = []
    capt: list = []
    gen = tareas.descargar(op)
    valor = None
    while True:
        try:
            orden = gen.send(valor)
        except StopIteration as fin:
            res = fin.value
            break
        valor = None
        if isinstance(orden, tareas.Js):
            valor = json.loads(json.dumps(falso.js(orden.codigo)))
        elif isinstance(orden, tareas.JsAsync):
            valor = falso.bajar(orden.codigo)
        elif isinstance(orden, tareas.Navegar):
            falso.navegar(orden.url)
        elif isinstance(orden, tareas.Aviso):
            avisos.append(orden.texto)
        elif isinstance(orden, tareas.Captcha):
            capt.append(orden.visible)
    assert res.xml_portal == 53
    assert capt == [True, False]
    assert sum("reintento" in a for a in avisos) == 2
    assert any("pulsa «Consultar»" in a for a in avisos)


# --- ingreso al SRI -------------------------------------------------------------

class LoginFalso:
    def __init__(self, sesion_ruc: str | None, clave_ok: str = "buena"):
        self.sesion_ruc = sesion_ruc
        self.clave_ok = clave_ok
        self.en_login = sesion_ruc is None
        self.error = ""
        self.borrados = 0
        self.ingresos = []

    def js(self, codigo: str):
        t = codigo[len(portal.LIB):] if codigo.startswith(portal.LIB) else codigo
        if "error: [...new Set" in t:
            return {"login": self.en_login, "url": "x", "error": self.error}
        if "kc-form-login'), url" in t:
            return {"login": self.en_login, "url": "x"}
        if "poner('usuario'" in t:
            usuario = re.search(r"poner\('usuario', \"(\d+)\"\)", t).group(1)
            clave = re.search(r"poner\('password', \"([^\"]*)\"\)", t).group(1)
            self.ingresos.append(usuario)
            if clave == self.clave_ok:
                self.sesion_ruc, self.en_login = (usuario if len(usuario) == 13 else usuario + "001"), False
            else:
                self.error = "Usuario o clave incorrectos"
            return {"ok": True}
        if "D.empresa()" in t:
            return {"url": "x", "emp": {"ruc": self.sesion_ruc, "razon": "EMPRESA " + self.sesion_ruc[-4:]} if self.sesion_ruc else None,
                    "login": self.en_login}
        if "btnBuscar')" in t:
            return not self.en_login
        if "kc-form-login')" in t:
            return self.en_login
        raise AssertionError("JS no esperado: " + t[:120])


def correr_login(falso: LoginFalso, cred):
    gen = tareas.iniciar_sesion(cred)
    valor = None
    while True:
        try:
            orden = gen.send(valor)
        except StopIteration as fin:
            return fin.value
        valor = None
        if isinstance(orden, tareas.Js):
            valor = falso.js(orden.codigo)
        elif isinstance(orden, tareas.BorrarSesion):
            falso.borrados += 1
            falso.sesion_ruc, falso.en_login = None, True


def test_login_reutiliza_sesion_del_mismo_ruc():
    f = LoginFalso(RUC_EMPRESA)
    emp = correr_login(f, tareas.Credenciales(RUC_EMPRESA, ""))
    assert emp["ruc"] == RUC_EMPRESA and f.ingresos == [] and f.borrados == 0


def test_login_cambia_de_empresa():
    f = LoginFalso(RUC_PROVEEDOR)
    emp = correr_login(f, tareas.Credenciales(RUC_EMPRESA, "buena"))
    assert f.borrados == 1 and f.ingresos == [RUC_EMPRESA] and emp["ruc"] == RUC_EMPRESA


def test_login_con_cedula_y_clave_incorrecta():
    f = LoginFalso(None)
    with pytest.raises(tareas.Falla, match="Usuario o clave incorrectos"):
        correr_login(f, tareas.Credenciales("1700000003", "mala"))
    f2 = LoginFalso(None)
    emp = correr_login(f2, tareas.Credenciales("1700000003", "buena"))
    assert emp["ruc"] == "1700000003001"


def test_credenciales_no_muestran_la_clave():
    assert "secreta" not in repr(tareas.Credenciales(RUC_EMPRESA, "secreta"))


class PortalQueFalla(PortalFalso):
    """Portal que corta la sesión tras unas descargas o miente en el total."""

    def __init__(self, *a, cortar_tras=None, total_falso=None, **k):
        super().__init__(*a, **k)
        self.cortar_tras = cortar_tras
        self.total_falso = total_falso
        self.descargas = 0
        self.sesion = True
        self.reingresos = 0

    def js(self, codigo):
        t = codigo[len(portal.LIB):] if codigo.startswith(portal.LIB) else codigo
        if "kc-form-login'), url" in t and "error:" not in t:
            return {"login": not self.sesion, "url": "x"}
        if "error: [...new Set" in t:
            return {"login": not self.sesion, "url": "x", "error": ""}
        if "poner('usuario'" in t:
            self.sesion = True
            self.reingresos += 1
            return {"ok": True}
        if not self.sesion and "D.empresa()" in t:
            return {"url": "x", "emp": None, "login": True}
        if not self.sesion and "btnBuscar')" in t:
            return False
        if self.total_falso and "D.total(" in t:
            v = super().js(codigo)
            v["total"] = self.total_falso
            return v
        return super().js(codigo)

    def bajar(self, codigo):
        self.descargas += 1
        if self.cortar_tras and self.descargas == self.cortar_tras and self.sesion:
            self.sesion = False
        if not self.sesion:
            return {"ok": True, "v": {"status": 200, "tipo": "text/html",
                                      "b64": base64.b64encode(b"<html>login</html>").decode()}}
        return super().bajar(codigo)

    def navegar(self, url):
        if not self.sesion:
            self.pagina = "login"
            return
        super().navegar(url)


def _solo_recibidos(op):
    op.emitidos = op.anulados = False
    return op


def test_sesion_cortada_vuelve_a_entrar_y_completa(escenario):
    falso, op, *_ = escenario
    p = PortalQueFalla(falso.recibidos, falso.emitidos, falso.anulados, cortar_tras=10)
    avisos: list = []
    cred = tareas.Credenciales(RUC_EMPRESA, "clave-de-prueba")
    gen = tareas.descargar(_solo_recibidos(op), cred)
    res = _correr(gen, p, avisos)
    assert p.reingresos == 1
    assert res.completo and res.xml_portal == 53
    assert any("Se volvió a entrar" in a for a in avisos)
    assert cred.clave == ""  # se borra al terminar


def test_total_inconsistente_queda_incompleto(escenario):
    falso, op, *_ = escenario
    p = PortalQueFalla(falso.recibidos, falso.emitidos, falso.anulados, total_falso=60)
    avisos: list = []
    res = _correr(tareas.descargar(_solo_recibidos(op)), p, avisos)
    assert not res.completo
    assert any(a.startswith("INCOMPLETO") for a in avisos)
    assert any("✘ Recibidos · Factura" in a for a in avisos)


def _correr(gen, falso, avisos):
    valor = None
    while True:
        try:
            orden = gen.send(valor)
        except StopIteration as fin:
            return fin.value
        valor = None
        if isinstance(orden, tareas.Js):
            valor = json.loads(json.dumps(falso.js(orden.codigo)))
        elif isinstance(orden, tareas.JsAsync):
            valor = falso.bajar(orden.codigo)
        elif isinstance(orden, tareas.Navegar):
            falso.navegar(orden.url)
        elif isinstance(orden, tareas.Python):
            valor = orden.funcion(*orden.args)
        elif isinstance(orden, tareas.Aviso):
            avisos.append(orden.texto)


def test_segunda_vez_no_vuelve_a_consultar_dias_verificados(escenario):
    falso, op, rec, emi_reciente, emi_viejo, ret = escenario
    op.hoy = date(2026, 4, 30)  # marzo ya cerrado
    res1 = _correr(tareas.descargar(op), falso, [])
    assert res1.completo
    consultas_1 = falso.consultas
    assert consultas_1 >= 31 * 2  # 31 días × 2 tipos de emitidos + recibidos

    p2 = PortalFalso(falso.recibidos, falso.emitidos, falso.anulados)
    avisos: list = []
    res2 = _correr(tareas.descargar(op), p2, avisos)
    assert res2.completo and res2.nuevos == 0
    assert p2.consultas == 1  # solo la consulta de recibidos (facturas)
    assert any(a.startswith("En la carpeta ya hay (03/2026)") for a in avisos)
    assert any("día(s) ya verificados" in a for a in avisos)

    # si se borra un archivo emitido, ese día se vuelve a consultar y se baja solo ese
    (op.carpeta / "2026-03" / "emitidos" / "facturas" / "2026-03-02 SIN NOMBRE Factura 001-002-000000455.pdf").unlink()
    p3 = PortalFalso(falso.recibidos, falso.emitidos, falso.anulados)
    res3 = _correr(tareas.descargar(op), p3, [])
    assert res3.completo and res3.nuevos == 1 and p3.consultas == 2

    # «Volver a revisar todo» consulta todos los días otra vez
    op.revisar_todo = True
    p4 = PortalFalso(falso.recibidos, falso.emitidos, falso.anulados)
    _correr(tareas.descargar(op), p4, [])
    assert p4.consultas == consultas_1


def test_archivos_con_nombre_antiguo_se_renombran_sin_bajar(escenario):
    falso, op, rec, *_ = escenario
    op.emitidos = op.anulados = False
    carpeta = op.carpeta / "2026-03" / "recibidos" / "facturas"
    carpeta.mkdir(parents=True)
    for cl, xml in rec[:5]:  # 5 ya bajados con la versión anterior (nombre = clave)
        (carpeta / f"{cl}.xml").write_bytes(xml)
    res = _correr(tareas.descargar(op), PortalFalso(falso.recibidos, {}, []), [])
    assert res.completo and res.existentes == 5 and res.xml_portal == 48
    nombres = sorted(x.name for x in carpeta.iterdir())
    assert len(nombres) == 53 and not any(n.startswith("0") for n in nombres)
    assert "2026-03-01 PROVEEDOR FICTICIO Factura 001-001-000000001.xml" in nombres


def test_nombre_archivo_legible():
    from descargasri.clave import leer_clave
    c = leer_clave(clave(date(2026, 3, 31), "07", RUC_PROVEEDOR, "001", "102", 17002))
    assert almacen.nombre_archivo(c, 'SUPER/LIQUORS: "S.A."', ".xml") == \
        "2026-03-31 SUPER LIQUORS S.A. Retención 001-102-000017002.xml"
