"""Conocimiento del portal SRI en línea (páginas JSF/PrimeFaces).

Todo lo que se ejecuta dentro de la página está aquí, como JavaScript.
La app nunca escribe la clave del usuario ni toca el captcha: solo llena
filtros, pulsa «Consultar» (el reCAPTCHA invisible lo ejecuta la propia
página) y pide los archivos dentro de la sesión que el usuario abrió.
"""

from __future__ import annotations

import json

INICIO = "https://srienlinea.sri.gob.ec/sri-en-linea/inicio/NAT"
DOMINIO = "srienlinea.sri.gob.ec"
URL_RECIBIDOS = "https://srienlinea.sri.gob.ec/tuportal-internet/accederAplicacion.jspa?redireccion=57&idGrupo=55"
URL_CONSULTAS = "https://srienlinea.sri.gob.ec/tuportal-internet/accederAplicacion.jspa?redireccion=60&idGrupo=58"
URL_ANULACION = "https://srienlinea.sri.gob.ec/tuportal-internet/accederAplicacion.jspa?redireccion=61&idGrupo=58"

# Valores del combo «Tipo de comprobante» del portal
TIPOS_PORTAL: dict[str, str] = {
    "1": "Factura",
    "2": "Liquidación de compra",
    "3": "Nota de crédito",
    "4": "Nota de débito",
    "5": "Guía de remisión",
    "6": "Comprobante de retención",
}
TIPOS_RECIBIDOS = ("1", "2", "3", "4", "6")
TIPOS_EMITIDOS = ("1", "2", "3", "4", "5", "6")

FILAS_POR_PAGINA = 500

# Biblioteca que se inyecta en cada llamada (las páginas JSF recargan y la pierden).
LIB = r"""
(function(){
if (window.__dds && window.__dds.v === 6) return;
const D = {v: 6, R: {}};
D.g = id => document.getElementById('frmPrincipal:' + id);
D.widget = suf => {
  // Los widgets de PrimeFaces son variables globales con .cfg.id. Se saltan los
  // marcos (p. ej. el del reCAPTCHA, de otro dominio): leerlos lanza SecurityError.
  for (const k of Object.keys(window)) {
    if (/^\d+$/.test(k)) continue;
    try {
      const v = window[k];
      if (v && v.cfg && typeof v.cfg.id === 'string' && v.cfg.id.endsWith(suf)) return v;
    } catch (e) { /* marco de otro origen */ }
  }
  return null;
};
D.marcar = () => {
  document.querySelectorAll('.ui-datatable, .ui-messages, .ui-growl, .ui-message, .ui-datatable tbody tr')
    .forEach(e => e.setAttribute('data-dds-viejo', '1'));
};
D.ajaxLibre = () => {
  try { return !window.PrimeFaces || !PrimeFaces.ajax || !PrimeFaces.ajax.Queue || PrimeFaces.ajax.Queue.isEmpty(); }
  catch (e) { return true; }
};
D.visible = el => {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  if (r.width < 30 || r.height < 30) return false;
  for (let p = el; p && p !== document.body; p = p.parentElement) {
    const s = getComputedStyle(p);
    if (s.visibility === 'hidden' || s.display === 'none' || s.opacity === '0') return false;
  }
  return true;
};
D.captcha = () => [...document.querySelectorAll('iframe[src*="recaptcha"][src*="bframe"], iframe[title*="recaptcha" i][src*="bframe"]')].some(D.visible);
D.mensajes = () => [...document.querySelectorAll('.ui-messages, .ui-growl, .ui-message')]
  .filter(m => !m.hasAttribute('data-dds-viejo')).map(m => m.innerText.trim()).filter(Boolean).join(' / ');
D.tabla = suf => document.querySelector('[id$="' + suf + '"]');
// entera=true: tras una consulta (la tabla o sus filas deben ser nuevas);
// entera=false: tras cambiar de página (basta con filas nuevas).
D.filasNuevas = (suf, entera) => {
  const t = D.tabla(suf); if (!t) return -1;
  const tr = [...t.querySelectorAll('tbody tr')];
  if (tr.some(r => r.hasAttribute('data-dds-viejo'))) return -1;
  const n = tr.filter(r => r.cells.length > 3).length;
  if (entera && t.hasAttribute('data-dds-viejo') && n === 0) return -1;
  return n;
};
D.total = suf => {
  const w = D.widget(suf);
  if (w && w.cfg && w.cfg.paginator) return {total: w.cfg.paginator.rowCount, porPagina: w.cfg.paginator.rows, paginas: w.cfg.paginator.pageCount, pagina: w.cfg.paginator.page || 0};
  const n = D.filasNuevas(suf, false);
  return {total: Math.max(n, 0), porPagina: Math.max(n, 0), paginas: 1, pagina: 0};
};
D.estado = (suf, entera) => ({libre: D.ajaxLibre(), captcha: D.captcha(), filas: D.filasNuevas(suf, entera), mensajes: D.mensajes()});
D.leer = suf => {
  const t = D.tabla(suf); if (!t) return {cols: [], filas: []};
  const cols = [...t.querySelectorAll('thead th')].map(h => h.innerText.replace(/\s+/g, ' ').trim());
  const filas = [...t.querySelectorAll('tbody tr')].filter(r => r.cells.length > 3).map(r => ({
    celdas: [...r.cells].map(c => c.innerText.replace(/\s+/g, ' ').trim()),
    xml: (r.querySelector('a[id$=":lnkXml"]') || {}).id || null,
    pdf: (r.querySelector('a[id$=":lnkPdf"]') || {}).id || null,
  }));
  return {cols, filas};
};
D.bajar = async linkId => {
  const f = document.getElementById('frmPrincipal');
  const fd = new URLSearchParams(new FormData(f));
  fd.delete('g-recaptcha-response');
  fd.set(linkId, linkId);
  const r = await fetch(f.action, {method: 'POST', body: fd, credentials: 'include'});
  const b = new Uint8Array(await r.arrayBuffer());
  let s = '';
  for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000));
  return {status: r.status, tipo: r.headers.get('content-type') || '', b64: btoa(s)};
};
D.elegir = (sel, valor) => {
  if (!sel) return false;
  const o = [...sel.options].find(o => o.value === String(valor) || o.text.trim().toLowerCase() === String(valor).toLowerCase());
  if (!o) return false;
  sel.value = o.value;
  sel.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
};
// Empresa de la sesión: se lee de la cabecera del usuario (div.area-usuario-blue en
// las páginas JSF, div.nombre-contribuyente en el portal nuevo). Se busca un texto
// que sea solo el RUC, fuera de tablas y diálogos, y la razón social a su lado.
// Nunca del contenido de las tablas (ahí hay RUC de proveedores).
D.empresa = () => {
  if (!document.body) return null;
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = w.nextNode())) {
    const ruc = n.textContent.trim();
    if (!/^\d{13}$/.test(ruc)) continue;
    const e = n.parentElement;
    if (!e || e.closest('table, .ui-dialog, form[id^="form-detalle"], input, textarea')) continue;
    for (let p = e, k = 0; p && k < 3; p = p.parentElement, k++) {
      const lineas = (p.innerText || '').split('\n').map(x => x.replace(ruc, '').trim()).filter(Boolean);
      const razon = lineas.find(x => /[A-Za-zÁÉÍÓÚÑáéíóúñ]{2}/.test(x) && x.length < 120);
      if (razon) return {ruc, razon};
    }
  }
  return null;
};
D.clicTexto = texto => {
  const a = [...document.querySelectorAll('a, button')].find(x => x.innerText && x.innerText.trim().toLowerCase() === texto.toLowerCase());
  if (!a) return false; a.click(); return true;
};
window.__dds = D;
})();
"""


def js(cuerpo: str) -> str:
    """Código síncrono: devuelve el valor de la última expresión como JSON."""
    return LIB + f"\nJSON.stringify((function(){{ const D = window.__dds; {cuerpo} }})())"


def js_async(cuerpo: str, ident: str) -> str:
    """Código asíncrono: deja el resultado en window.__dds.R[ident]."""
    i = json.dumps(ident)
    return (
        LIB
        + f"\n(function(){{ const D = window.__dds; D.R[{i}] = null;"
        + f" (async () => {{ {cuerpo} }})()"
        + f".then(v => D.R[{i}] = {{ok: true, v: v}})"
        + f".catch(e => D.R[{i}] = {{ok: false, e: String(e)}}); return 1; }})()"
    )


def js_resultado(ident: str) -> str:
    i = json.dumps(ident)
    return LIB + f"\nJSON.stringify(window.__dds.R[{i}] || null)"


# --- Acciones concretas -------------------------------------------------

def empresa() -> str:
    return js("return {url: location.href, emp: D.empresa(), login: /login|auth|openid/i.test(location.href)};")


def consultar_recibidos(anio: int, mes: int, tipo: str) -> str:
    return js(f"""
      const g = D.g;
      if (!g('btnBuscar')) return {{ok: false, e: 'No estoy en la página de comprobantes recibidos'}};
      const anios = [...g('ano').options].map(o => o.value);
      if (!anios.includes('{anio}')) return {{ok: false, e: 'El portal no permite consultar el año {anio} (solo ' + anios.join(', ') + ')'}};
      D.marcar();
      D.elegir(g('ano'), '{anio}'); D.elegir(g('mes'), '{mes}'); D.elegir(g('dia'), '0');
      if (!D.elegir(g('cmbTipoComprobante'), '{tipo}')) return {{ok: false, e: 'Tipo {tipo} no disponible'}};
      g('btnBuscar').click();
      return {{ok: true}};
    """)


def consultar_emitidos(fecha_ddmmaaaa: str, tipo: str) -> str:
    return js(f"""
      const g = D.g;
      if (!g('btnConsultar') || !g('calendarFechaDesde_input')) return {{ok: false, e: 'No estoy en la página de comprobantes emitidos'}};
      D.marcar();
      D.elegir(g('cmbEstadoAutorizacion'), 'AUT');
      if (!D.elegir(g('cmbTipoComprobante'), '{tipo}')) return {{ok: false, e: 'Tipo {tipo} no disponible'}};
      D.elegir(g('cmbEstablecimiento'), '');
      const p = g('txtPuntoEmision'); if (p) p.value = '';
      const c = g('calendarFechaDesde_input'); c.value = '{fecha_ddmmaaaa}';
      c.dispatchEvent(new Event('change', {{bubbles: true}}));
      g('btnConsultar').click();
      return {{ok: true}};
    """)


def estado(sufijo_tabla: str, entera: bool = True) -> str:
    return js(f"return D.estado({json.dumps(sufijo_tabla)}, {'true' if entera else 'false'});")


def total(sufijo_tabla: str) -> str:
    return js(f"return D.total({json.dumps(sufijo_tabla)});")


def poner_filas_por_pagina(sufijo_tabla: str, n: int = FILAS_POR_PAGINA) -> str:
    return js(f"""
      const w = D.widget({json.dumps(sufijo_tabla)});
      if (!w || !w.paginator) return {{ok: false}};
      document.querySelectorAll('.ui-datatable tbody tr').forEach(e => e.setAttribute('data-dds-viejo', '1'));
      w.paginator.setRowsPerPage({int(n)});
      return {{ok: true}};
    """)


def ir_a_pagina(sufijo_tabla: str, pagina: int) -> str:
    return js(f"""
      const w = D.widget({json.dumps(sufijo_tabla)});
      if (!w || !w.paginator) return {{ok: false}};
      document.querySelectorAll('.ui-datatable tbody tr').forEach(e => e.setAttribute('data-dds-viejo', '1'));
      w.paginator.setPage({int(pagina)});
      return {{ok: true}};
    """)


def leer_tabla(sufijo_tabla: str) -> str:
    return js(f"return D.leer({json.dumps(sufijo_tabla)});")


def bajar(link_id: str, ident: str) -> str:
    return js_async(f"return await D.bajar({json.dumps(link_id)});", ident)


def hay(selector_js: str) -> str:
    """Condición booleana evaluada en la página."""
    return js(f"return !!({selector_js});")


def clic_texto(texto: str) -> str:
    return js(f"return D.clicTexto({json.dumps(texto)});")


def consultar_anulados(anio: int, mes: int) -> str:
    return js(f"""
      const f = document.getElementById('frmPrincipal') || document.forms[document.forms.length - 1];
      const sels = [...f.querySelectorAll('select')];
      const sa = sels.find(s => [...s.options].some(o => o.text.trim() === '{anio}'));
      const sm = sels.find(s => [...s.options].some(o => /^enero$/i.test(o.text.trim())));
      if (!sa || !sm) return {{ok: false, e: 'No encontré los filtros de año y mes en anulados'}};
      if (!D.elegir(sa, '{anio}')) return {{ok: false, e: 'El portal no permite el año {anio} en anulados'}};
      const meses = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'];
      if (!D.elegir(sm, meses[{mes} - 1]) && !D.elegir(sm, '{mes}')) return {{ok: false, e: 'Mes no disponible'}};
      D.marcar();
      const b = [...f.querySelectorAll('button, input[type=submit]')].find(x => /consultar/i.test(x.innerText || x.value || ''));
      if (!b) return {{ok: false, e: 'No encontré el botón Consultar en anulados'}};
      b.click();
      return {{ok: true}};
    """)


def estado_anulados() -> str:
    return js("""
      const ts = [...document.querySelectorAll('.ui-datatable')];
      const nuevas = ts.filter(t => !t.hasAttribute('data-dds-viejo'));
      return {libre: D.ajaxLibre(), nuevas: nuevas.length, total: ts.length, mensajes: D.mensajes()};
    """)


def leer_anulados() -> str:
    return js("""
      const out = [];
      for (const t of document.querySelectorAll('.ui-datatable')) {
        const cols = [...t.querySelectorAll('thead th')].map(h => h.innerText.replace(/\\s+/g, ' ').trim());
        const lado = cols.join(' ').toLowerCase().includes('receptor') ? 'emitidos' : 'recibidos';
        const w = t.id ? D.widget(t.id) : null;
        const pag = w && w.cfg && w.cfg.paginator ? {total: w.cfg.paginator.rowCount, porPagina: w.cfg.paginator.rows} : null;
        const filas = [...t.querySelectorAll('tbody tr')].filter(r => r.cells.length > 3)
          .map(r => [...r.cells].map(c => c.innerText.replace(/\\s+/g, ' ').trim()));
        out.push({id: t.id, lado, cols, filas, pag});
      }
      return out;
    """)


def anulados_filas_por_pagina(n: int = FILAS_POR_PAGINA) -> str:
    return js(f"""
      let k = 0;
      for (const t of document.querySelectorAll('.ui-datatable')) {{
        const w = t.id ? D.widget(t.id) : null;
        if (w && w.paginator && w.cfg.paginator.rowCount > w.cfg.paginator.rows) {{
          t.querySelectorAll('tbody tr').forEach(e => e.setAttribute('data-dds-viejo', '1'));
          w.paginator.setRowsPerPage({int(n)}); k++;
        }}
      }}
      return k;
    """)
