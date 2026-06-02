"""
file_processor — lee y normaliza archivos de Banco Galicia
Soporta:
  • listadoChequesRecibidos*.xlsx  (export ECHEQ Galicia Home Banking)
  • *Devolucion Cobranza Integrada*.txt  (Galicia cobranza recaudadora)
  • Consulta_de_cobranzas_MACRO*.xls  (Banco Macro — referencia)
  • extracto_CC*.csv  (extracto de cuenta corriente Galicia)
"""

import os
import re
import logging
from datetime import datetime
from typing import Tuple, List, Dict

import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# XLSX — listado ECHEQ
# ─────────────────────────────────────────────────────────────────────────────
def procesar_xlsx_echeq(ruta: str) -> Tuple[List[Dict], str]:
    """
    Lee el Excel descargado desde Home Banking Galicia (listadoChequesRecibidos).
    El archivo tiene 2 filas de encabezado.
    """
    try:
        df_raw = pd.read_excel(ruta, header=None, dtype=str)

        # ── Detectar fila de encabezado real ─────────────────────────────
        header_row = 1  # fila 1 tiene los nombres de columna
        df = pd.read_excel(ruta, header=header_row, dtype=str)
        df.columns = [str(c).strip() for c in df.columns]

        # Mapeo flexible de columnas
        COL = {
            'numero':    ['Nº de cheque', 'numero', 'N° de cheque', 'nro cheque'],
            'librador':  ['Recibido de', 'recibido de', 'Razón social', 'librador'],
            'cuit':      ['CUIT/CUIL/CDI', 'cuit', 'CUIT'],
            'fecha_pago':['Fecha de pago', 'fecha de pago', 'Fecha pago'],
            'fecha_em':  ['Fecha de emisión', 'Fecha de emision', 'fecha emision'],
            'importe':   ['Importe', 'importe', 'Monto'],
            'estado':    ['Estado', 'estado'],
            'banco':     ['Banco emisor', 'banco emisor', 'Banco'],
            'id_cheque': ['ID del cheque', 'ID cheque', 'id cheque'],
            'cmc7':      ['CMC7', 'cmc7'],
        }

        def buscar_col(df, opciones):
            for op in opciones:
                for c in df.columns:
                    if c.strip().lower() == op.lower():
                        return c
            return None

        cheques = []
        for _, row in df.iterrows():
            n = _get(row, buscar_col(df, COL['numero']))
            if not n or str(n) in ('nan', 'None', 'Nº de cheque'):
                continue

            imp_raw = _get(row, buscar_col(df, COL['importe']))
            importe = _parse_importe(imp_raw)

            fecha_pago_raw = _get(row, buscar_col(df, COL['fecha_pago']))
            fecha_em_raw   = _get(row, buscar_col(df, COL['fecha_em']))

            cheque = {
                'id':           str(n).strip().split('.')[0],
                'numero_cheque':str(n).strip().split('.')[0],
                'librador':     _get(row, buscar_col(df, COL['librador'])),
                'cuit':         _fmt_cuit(_get(row, buscar_col(df, COL['cuit']))),
                'banco':        _get(row, buscar_col(df, COL['banco'])),
                'fecha_pago':   _fmt_fecha(fecha_pago_raw),
                'fecha_emision':_fmt_fecha(fecha_em_raw),
                'importe':      importe,
                'estado':       _get(row, buscar_col(df, COL['estado'])),
                'tipo':         'DIFERIDO',
                'cmc7':         _get(row, buscar_col(df, COL['cmc7'])),
                'seleccionado': True,
                'subido':       False,
            }
            cheques.append(cheque)

        return cheques, f"{len(cheques)} cheques leídos de {os.path.basename(ruta)}"

    except Exception as e:
        logger.exception("Error procesando XLSX ECHEQ")
        raise ValueError(f"Error al leer el Excel: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# TXT — Devolución Cobranza Integrada Galicia
# ─────────────────────────────────────────────────────────────────────────────
def procesar_txt_galicia(ruta: str) -> Tuple[List[Dict], str]:
    """
    Procesa el archivo TXT de cobranza recaudadora de Galicia.
    Formato: campos de ancho fijo o delimitado por pipes.
    """
    try:
        with open(ruta, 'r', encoding='latin-1', errors='replace') as f:
            lineas = f.readlines()

        cheques = []
        for linea in lineas:
            linea = linea.rstrip('\n\r')
            if not linea.strip():
                continue

            # Intentar parsear por '|' (pipe-separated)
            if '|' in linea:
                partes = [p.strip() for p in linea.split('|')]
                if len(partes) >= 5:
                    importe = _parse_importe(partes[4] if len(partes) > 4 else '0')
                    if importe <= 0:
                        continue
                    cheque = {
                        'id':           partes[2] if len(partes) > 2 else f'TXT_{len(cheques)}',
                        'numero_cheque':partes[2] if len(partes) > 2 else '',
                        'librador':     partes[6] if len(partes) > 6 else '',
                        'cuit':         _fmt_cuit(partes[3] if len(partes) > 3 else ''),
                        'banco':        partes[7] if len(partes) > 7 else 'BANCO GALICIA',
                        'fecha_pago':   _fmt_fecha(partes[1] if len(partes) > 1 else ''),
                        'fecha_emision':_fmt_fecha(partes[0] if len(partes) > 0 else ''),
                        'importe':      importe,
                        'estado':       'CHEQUE_DIFERIDO',
                        'tipo':         'DIFERIDO',
                        'cmc7':         '',
                        'seleccionado': True,
                        'subido':       False,
                    }
                    cheques.append(cheque)
            else:
                # Ancho fijo (ajustar offsets según formato real del banco)
                if len(linea) < 20:
                    continue
                try:
                    fecha  = linea[0:8].strip()
                    nro    = linea[8:22].strip()
                    cuit   = linea[22:35].strip()
                    imp_s  = linea[35:50].strip()
                    banco  = linea[50:80].strip() if len(linea) > 50 else ''
                    importe = _parse_importe(imp_s)
                    if not nro or importe <= 0:
                        continue
                    cheques.append({
                        'id':           nro,
                        'numero_cheque':nro,
                        'librador':     banco,
                        'cuit':         _fmt_cuit(cuit),
                        'banco':        'BANCO GALICIA',
                        'fecha_pago':   _fmt_fecha(fecha),
                        'fecha_emision':'',
                        'importe':      importe,
                        'estado':       'CHEQUE_DIFERIDO',
                        'tipo':         'DIFERIDO',
                        'cmc7':         '',
                        'seleccionado': True,
                        'subido':       False,
                    })
                except Exception:
                    continue

        return cheques, f"{len(cheques)} registros leídos del TXT"

    except Exception as e:
        logger.exception("Error procesando TXT Galicia")
        raise ValueError(f"Error al leer el TXT: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# XLS Macro (referencia)
# ─────────────────────────────────────────────────────────────────────────────
def procesar_xls_macro(ruta: str) -> Tuple[List[Dict], str]:
    try:
        df = pd.read_excel(ruta, header=2, dtype=str)
        cobros = []
        for _, row in df.iterrows():
            imp = _parse_importe(str(row.get('Importe', '0')))
            if imp <= 0:
                continue
            cobros.append({
                'id':      str(row.get('Nro. de Cobranza', '')).strip(),
                'fecha':   str(row.get('Fecha de recaudación', '')).strip(),
                'importe': imp,
                'depositante': str(row.get('Denominación depositante', '')).strip(),
                'cuit':    _fmt_cuit(str(row.get('Cuit depositante', '')).strip()),
                'estado':  str(row.get('Estado de la cobranza', '')).strip(),
            })
        return cobros, f"{len(cobros)} cobros Macro"
    except Exception as e:
        raise ValueError(f"Error leyendo XLS Macro: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# CSV — CobranzasInformadas Galicia (recaudadora)
# ─────────────────────────────────────────────────────────────────────────────
def procesar_csv_recaudadora_galicia(ruta: str) -> Tuple[List[Dict], str]:
    """
    Lee el CSV descargado desde CobranzasInformadas de Galicia.
    Mapea encabezados en español a claves internas.
    """
    try:
        enc_ok = 'latin-1'
        for enc in ['utf-8-sig', 'utf-8', 'latin-1', 'cp1252']:
            try:
                with open(ruta, 'r', encoding=enc, errors='strict') as f:
                    f.read(4096)
                enc_ok = enc
                break
            except Exception:
                continue

        with open(ruta, 'r', encoding=enc_ok, errors='replace') as f:
            lineas = f.readlines()

        # Detectar separador por cantidad de columnas en la primera línea con texto
        sep_ok = ';'
        best_n = 0
        for sep in [';', ',', '\t']:
            for linea in lineas[:5]:
                n = len(linea.split(sep))
                if n > best_n:
                    sep_ok = sep
                    best_n = n

        # Detectar fila de header buscando "Fecha de pago" — más robusto que contar columnas
        header_row = 0
        for idx, linea in enumerate(lineas[:10]):
            partes = [p.strip().strip('"').lower() for p in linea.split(sep_ok)]
            if 'fecha de pago' in partes:
                header_row = idx
                break

        df = pd.read_csv(
            ruta, encoding=enc_ok, sep=sep_ok, dtype=str,
            header=header_row, skip_blank_lines=True
        )
        df.columns = [str(c).strip() for c in df.columns]
        df = df.dropna(how='all')

        print(f"[recaudadora] enc={enc_ok} sep={repr(sep_ok)} header_row={header_row} cols={list(df.columns)}", flush=True)

        COL_MAP = {
            'tipo_cliente':       ['Tipo de cliente', 'tipo de cliente'],
            'nro_cliente':        ['Número de cliente', 'Numero de cliente', 'Nro. de cliente', 'Nro cliente'],
            'nombre_cliente':     ['Nombre de cliente', 'Nombre del cliente', 'nombre cliente', 'Nombre'],
            'id_interno_cliente': ['Id interno cliente', 'Id Interno cliente', 'ID Interno cliente'],
            'tipo_doc':           ['Tipo de documento', 'tipo de documento', 'Tipo documento', 'Tipo'],
            'id_documento':       ['Id de documento', 'ID de documento', 'id documento'],
            'id_interno_doc':     ['Id interno documento', 'ID interno documento'],
            'division':           ['División', 'Division', 'division'],
            'moneda':             ['Moneda', 'moneda'],
            'fecha_pago':         ['Fecha de pago', 'fecha de pago', 'Fecha pago'],
            'sucursal':           ['Sucursal de pago', 'sucursal de pago', 'Sucursal'],
            'forma_pago':         ['Forma de pago', 'forma de pago', 'Forma pago'],
            'id_pago':            ['Id pago', 'ID pago', 'id pago'],
            'pago_parcial':       ['Pago parcial', 'pago parcial'],
            'importe_pago':       ['Importe del pago', 'importe del pago', 'Importe pago', 'Importe'],
            'nro_cheque':         ['Nro cheque', 'Nro. cheque', 'Número cheque', 'NRO CHEQUE'],
            'fecha_est_acr':      ['Fecha est. acreditación cheque', 'Fecha est. acreditación',
                                   'Fecha est acreditacion', 'Fecha est. acr', 'Fecha acreditacion'],
            'importe_cheque':     ['Importe del cheque', 'Importe cheque', 'importe cheque'],
            'cod_banco':          ['Código del banco', 'Codigo del banco', 'Código del barcod',
                                   'Codigo del barcod', 'Codigo barcod', 'Cod barcod'],
            'informado':          ['Informado', 'informado'],
            'anulado':            ['Anulado', 'anulado'],
            'nro_documento':      ['Nro documento del pago', 'Nro documento', 'Nro. documento'],
            'id_canal':           ['Id canal', 'ID canal', 'id canal'],
            'desc_canal':         ['Descripción Canal', 'Descripcion Canal', 'Descripcion canal', 'Descripción canal'],
            'id_echeq':           ['Id echeq', 'Id del cheque', 'ID del cheque', 'ID echeq'],
            'fecha_endoso':       ['Fecha de endoso del echeq', 'Fecha de endoso del cheque',
                                   'Fecha endoso', 'fecha endoso'],
        }

        def find_col(options):
            for op in options:
                for c in df.columns:
                    if c.strip().lower() == op.lower():
                        return c
            for op in options:
                for c in df.columns:
                    if op.lower() in c.strip().lower():
                        return c
            return None

        cols = {k: find_col(v) for k, v in COL_MAP.items()}

        cobros = []
        for _, row in df.iterrows():
            fecha_raw = _get(row, cols.get('fecha_pago'))
            if not fecha_raw or fecha_raw.lower() in ('nan', 'none', 'fecha de pago', 'fecha pago'):
                continue

            det = {}
            for key, label in [
                ('tipo_cliente',       'Tipo cliente'),
                ('id_interno_cliente', 'Id interno cliente'),
                ('tipo_doc',           'Tipo doc.'),
                ('id_documento',       'Id documento'),
                ('id_interno_doc',     'Id interno doc.'),
                ('division',           'División'),
                ('moneda',             'Moneda'),
                ('sucursal',           'Sucursal'),
                ('id_pago',            'Id pago'),
                ('pago_parcial',       'Pago parcial'),
                ('fecha_est_acr',      'Fecha acr. est.'),
                ('importe_cheque',     'Importe cheque'),
                ('cod_banco',          'Cód. banco'),
                ('nro_documento',      'Nro documento'),
                ('id_canal',           'Id canal'),
                ('desc_canal',         'Descripción canal'),
                ('id_echeq',           'Id echeq'),
                ('fecha_endoso',       'Fecha endoso'),
            ]:
                v = _get(row, cols.get(key))
                if v and v.lower() not in ('nan', 'none', ''):
                    if 'fecha' in key:
                        v = _fmt_fecha(v) or v
                    elif key == 'importe_cheque':
                        imp = _parse_importe(v)
                        if imp > 0:
                            v = f"${imp:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
                    det[label] = v

            cobros.append({
                'id':             f'COB_{len(cobros)+1:04d}',
                'fecha_pago':     _fmt_fecha(fecha_raw),
                'nombre_cliente': _get(row, cols.get('nombre_cliente')),
                'nro_cliente':    _fmt_cuit_largo(_get(row, cols.get('nro_cliente'))),
                'forma_pago':     _get(row, cols.get('forma_pago')),
                'importe_pago':   _parse_importe(_get(row, cols.get('importe_pago'))),
                'nro_cheque':     _get(row, cols.get('nro_cheque')),
                'informado':      _get(row, cols.get('informado')),
                'anulado':        _get(row, cols.get('anulado')),
                'detalle':        det,
            })

        return cobros, f"{len(cobros)} cobros del CSV recaudadora"

    except Exception as e:
        logger.exception("Error procesando CSV recaudadora Galicia")
        raise ValueError(f"Error al leer el CSV recaudadora: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# CSV — Cheques electrónicos recibidos (cheques/recibidos)
# ─────────────────────────────────────────────────────────────────────────────
def procesar_csv_cheques_galicia(ruta: str) -> Tuple[List[Dict], str]:
    """
    Lee el CSV de cheques electrónicos recibidos de Galicia.
    Estructura del archivo:
      fila 0 → títulos de sección (Datos del cheque, Datos del librador, ...)
      fila 1 → nombres de columna reales  (Nº de cheque, Recibido de, CUIT, ...)
      fila 2+ → datos
    """
    try:
        enc_ok = 'latin-1'
        for enc in ['utf-8-sig', 'utf-8', 'latin-1', 'cp1252']:
            try:
                with open(ruta, 'r', encoding=enc, errors='strict') as f:
                    f.read(4096)
                enc_ok = enc
                break
            except Exception:
                continue

        # Detectar separador usando cualquiera de las primeras filas
        with open(ruta, 'r', encoding=enc_ok, errors='replace') as f:
            lineas = f.readlines()

        sep_ok = ';'
        best_n = 0
        for sep in [';', ',', '\t']:
            for linea in lineas[:5]:
                n = len(linea.split(sep))
                if n > best_n:
                    sep_ok = sep
                    best_n = n

        # Forzar header=1: la fila 0 son agrupaciones, la fila 1 tiene los nombres reales
        df = pd.read_csv(ruta, encoding=enc_ok, sep=sep_ok, dtype=str, header=1, skip_blank_lines=True)
        df.columns = [str(c).strip() for c in df.columns]
        df = df.dropna(how='all')

        print(f"[cheques] enc={enc_ok} sep={repr(sep_ok)} header_row=1 cols={list(df.columns)}", flush=True)

        # Nombres exactos de la fila 1 del CSV (después de strip)
        COL_MAP = {
            'numero':     ['Nº de cheque', 'N° de cheque', 'Nro. de cheque', 'Nro cheque',
                           'Número de cheque', 'Numero de cheque', 'N de cheque'],
            'clausula':   ['Cláusula', 'Clausula'],
            'librador':   ['Recibido de', 'recibido de'],
            'cuit':       ['CUIT/CUIL/CDI'],   # primera ocurrencia = del librador
            'fecha_pago': ['Fecha de pago'],
            'fecha_em':   ['Fecha de emisión', 'Fecha de emision', 'Fecha emisión'],
            'importe':    ['Importe'],
            'estado':     ['Estado'],
            'banco':      ['Banco emisor'],
            'id_cheque':  ['ID del cheque'],
            'cmc7':       ['CMC7'],
            'motivo':     ['Motivo y descripción', 'Motivo y descripcion'],
            'emitido_a':  ['Emitido a'],
            'cbu_dep':    ['CBU Deposito'],
            'cbu_cus':    ['CBU Custodia'],
            'cant_endosos':   ['Cantidad de endosos'],
            'cant_cesiones':  ['Cantidad de cesiones'],
            'cant_avales':    ['Cantidad de avales'],
        }

        def find_col(options):
            for op in options:
                for c in df.columns:
                    if c.strip().lower() == op.lower():
                        return c
            for op in options:
                for c in df.columns:
                    if op.lower() in c.strip().lower():
                        return c
            return None

        cols      = {k: find_col(v) for k, v in COL_MAP.items()}
        core_cols = set(c for c in cols.values() if c)

        cheques = []
        for _, row in df.iterrows():
            num = _get(row, cols.get('numero'))
            # Saltear filas de encabezado de sección (ej: "Datos del cheque")
            # y filas vacías — el número de cheque debe contener dígitos
            if not num or not re.search(r'\d', str(num)):
                continue
            if num.lower() in ('nan', 'none'):
                continue
            num = str(num).strip().split('.')[0]

            det = {}
            # Campos explícitos con etiquetas amigables
            for key, label in [
                ('clausula',      'Cláusula'),
                ('id_cheque',     'ID Cheque'),
                ('cmc7',          'CMC7'),
                ('motivo',        'Motivo'),
                ('emitido_a',     'Emitido a'),
                ('cbu_dep',       'CBU Depósito'),
                ('cbu_cus',       'CBU Custodia'),
                ('cant_endosos',  'Cant. endosos'),
                ('cant_cesiones', 'Cant. cesiones'),
                ('cant_avales',   'Cant. avales'),
            ]:
                v = _get(row, cols.get(key))
                if v and v.lower() not in ('nan', 'none', ''):
                    det[label] = v
            # TODOS los demás campos del CSV (Razón social, C.P del cheque,
            # CUIT duplicados, historial de endosos/cesiones/avales, etc.)
            # Se incluyen sin filtrar por '-' o '0' para no perder información.
            # Solo se excluyen los campos ya mostrados en la fila principal
            # y los "Unnamed" (columnas sin nombre en el CSV).
            skip = core_cols | {c for c in df.columns if c.startswith('Unnamed')}
            for col in df.columns:
                if col in skip:
                    continue
                v = _get(row, col)
                if v and v.lower() not in ('nan', 'none', ''):
                    det[col] = v  # nombre con sufijo .1 .2 para duplicados

            cheques.append({
                'id':        f'CHQ_{len(cheques)+1:04d}',
                'numero':    num,
                'librador':  _get(row, cols.get('librador')),
                'cuit':      _fmt_cuit(_get(row, cols.get('cuit'))),
                'fecha_pago':_fmt_fecha(_get(row, cols.get('fecha_pago'))),
                'fecha_em':  _fmt_fecha(_get(row, cols.get('fecha_em'))),
                'importe':   _parse_importe(_get(row, cols.get('importe'))),
                'estado':    _get(row, cols.get('estado')),
                'banco':     _get(row, cols.get('banco')),
                'detalle':   det,
            })

        return cheques, f"{len(cheques)} cheques del CSV"

    except Exception as e:
        logger.exception("Error procesando CSV cheques Galicia")
        raise ValueError(f"Error al leer el CSV de cheques: {e}")


def _fmt_cuit_largo(val: str) -> str:
    """Formatea número de cliente que puede venir en notación científica (ej. 3.0711E+10)."""
    if not val or str(val) in ('nan', 'None', ''):
        return ''
    s = str(val).strip()
    s_norm = s.replace(',', '.')
    try:
        n = int(float(s_norm))
        s = str(n)
    except Exception:
        s = re.sub(r'[^\d]', '', s)
    if len(s) == 11:
        return f"{s[0:2]}-{s[2:10]}-{s[10]}"
    return s


# ─────────────────────────────────────────────────────────────────────────────
# CSV — extracto de cuenta corriente Galicia (extracto_CC*.csv)
# ─────────────────────────────────────────────────────────────────────────────
def procesar_csv_extracto_galicia(ruta: str) -> Tuple[List[Dict], str]:
    """
    Lee el CSV de extracto de cuenta de Galicia.
    Detecta encoding, separador y la fila de encabezado real
    (el archivo puede tener filas de metadata antes de los datos).
    """
    try:
        # ── 1. Detectar encoding ──────────────────────────────────────────
        enc_ok = 'latin-1'
        for enc in ['utf-8-sig', 'utf-8', 'latin-1', 'cp1252']:
            try:
                with open(ruta, 'r', encoding=enc, errors='strict') as f:
                    f.read(2048)
                enc_ok = enc
                break
            except Exception:
                continue

        # ── 2. Leer líneas crudas para detectar separador y fila header ──
        with open(ruta, 'r', encoding=enc_ok, errors='replace') as f:
            lineas = f.readlines()

        # Para cada separador, buscar la primera línea que contenga "fecha"
        # y quedarse con el sep que produce más columnas en esa línea.
        best = {'sep': ';', 'row': 0, 'ncols': 0}
        for sep in [';', ',', '\t']:
            for idx, linea in enumerate(lineas[:30]):
                partes = [p.strip() for p in linea.split(sep)]
                if any('fecha' in p.lower() for p in partes) and len(partes) > best['ncols']:
                    best = {'sep': sep, 'row': idx, 'ncols': len(partes)}
                    break   # encontrado para este sep, pasar al siguiente

        sep_ok    = best['sep']
        header_row = best['row']

        # ── 3. Leer con pandas usando el header correcto ──────────────────
        df = pd.read_csv(
            ruta, encoding=enc_ok, sep=sep_ok, dtype=str,
            header=header_row, skip_blank_lines=True, index_col=False
        )
        df.columns = [str(c).strip() for c in df.columns]
        df = df.dropna(how='all')

        print(f"[extracto] enc={enc_ok} sep={repr(sep_ok)} header_row={header_row} cols={list(df.columns)}", flush=True)
        logger.info(f"[extracto] enc={enc_ok} sep={repr(sep_ok)} header_row={header_row} cols={list(df.columns)}")

        # ── 4. Mapear columnas ────────────────────────────────────────────
        col_fecha = col_desc = col_deb = col_cred = col_saldo = col_estado = None
        col_nro_comprobante = col_origen = None
        leyendas_cols = []
        extra_cols    = []

        for col in df.columns:
            cl = col.lower().strip()
            if 'fecha' in cl and col_fecha is None:
                col_fecha = col
            elif ('descrip' in cl) and col_desc is None:
                col_desc = col
            elif ('débit' in cl or 'debit' in cl) and col_deb is None:
                col_deb = col
            elif ('crédit' in cl or 'credit' in cl) and col_cred is None:
                col_cred = col
            elif 'saldo' in cl and col_saldo is None:
                col_saldo = col
            elif ('imput' in cl or 'anula' in cl or 'tipo de movim' in cl) and col_estado is None:
                col_estado = col
            elif 'comprobante' in cl and col_nro_comprobante is None:
                col_nro_comprobante = col
            elif cl == 'origen' and col_origen is None:
                col_origen = col
            elif 'leyenda' in cl or 'adic' in cl:
                leyendas_cols.append(col)
            else:
                extra_cols.append(col)

        # Leyendas: [0]=Nombre, [1]=CUIT, [2]=Banco, [3]=Sucursal
        col_nombre   = leyendas_cols[0] if len(leyendas_cols) > 0 else None
        col_cuit_cte = leyendas_cols[1] if len(leyendas_cols) > 1 else None
        col_banco    = leyendas_cols[2] if len(leyendas_cols) > 2 else None
        col_sucursal = leyendas_cols[3] if len(leyendas_cols) > 3 else None

        core = {col_fecha, col_desc, col_deb, col_cred, col_saldo, col_estado,
                col_nro_comprobante, col_origen,
                col_nombre, col_cuit_cte, col_banco, col_sucursal} - {None}

        # ── 5. Construir movimientos ──────────────────────────────────────
        movimientos = []
        for i, row in df.iterrows():
            fecha_raw = _get(row, col_fecha)
            if not fecha_raw or fecha_raw.lower() in ('fecha', 'nan', 'none'):
                continue

            detalle = {}
            for col in leyendas_cols + extra_cols:
                if col in core:
                    continue
                v = _get(row, col)
                if v:
                    detalle[col] = v

            movimientos.append({
                'id':              f'MOV_{len(movimientos)+1:04d}',
                'fecha':           _fmt_fecha(fecha_raw),
                'descripcion':     _get(row, col_desc),
                'nombre':          _get(row, col_nombre)   if col_nombre   else '',
                'cuit_cte':        _get(row, col_cuit_cte) if col_cuit_cte else '',
                'banco':           _get(row, col_banco)    if col_banco    else '',
                'sucursal':        _get(row, col_sucursal) if col_sucursal else _get(row, col_origen) if col_origen else '',
                'nro_comprobante': _get(row, col_nro_comprobante) if col_nro_comprobante else '',
                'debito':          _parse_importe(_get(row, col_deb)   if col_deb   else ''),
                'credito':         _parse_importe(_get(row, col_cred)  if col_cred  else ''),
                'saldo':           _parse_importe(_get(row, col_saldo) if col_saldo else ''),
                'estado':          _get(row, col_estado),
                'detalle':         detalle,
            })

        return movimientos, f"{len(movimientos)} movimientos del extracto"

    except Exception as e:
        logger.exception("Error procesando CSV extracto Galicia")
        raise ValueError(f"Error al leer el extracto CSV: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _get(row, col) -> str:
    if col is None:
        return ''
    v = row.get(col, '')
    if v is None or str(v) in ('nan', 'None', 'NaT'):
        return ''
    return str(v).strip()


def _parse_importe(txt: str) -> float:
    if not txt:
        return 0.0
    try:
        s = str(txt).replace('$', '').replace(' ', '').strip()
        neg = s.startswith('-')
        if ',' in s and '.' in s:
            # Detectar formato según cuál separador aparece último:
            #   "1.234,56"   → último ',' → formato europeo (miles='.', dec=',')
            #   "1,234.56"   → último '.' → formato US      (miles=',', dec='.')
            if s.rfind('.') > s.rfind(','):
                s = s.replace(',', '')
            else:
                s = s.replace('.', '').replace(',', '.')
        elif ',' in s:
            s = s.replace(',', '.')
        val = float(re.sub(r'[^\d.]', '', s) or '0')
        return -val if neg else val
    except Exception:
        return 0.0


def _fmt_fecha(val) -> str:
    if not val or str(val) in ('nan', 'None', 'NaT', ''):
        return ''
    s = str(val).strip()
    # datetime obj
    try:
        if hasattr(val, 'strftime'):
            return val.strftime('%d/%m/%Y')
    except Exception:
        pass
    # yyyy-mm-dd
    if re.match(r'\d{4}-\d{2}-\d{2}', s):
        p = s[:10].split('-')
        return f"{p[2]}/{p[1]}/{p[0]}"
    # yyyy/mm/dd  (formato Galicia CobranzasInformadas)
    if re.match(r'^\d{4}/\d{2}/\d{2}', s):
        p = s[:10].split('/')
        return f"{p[2]}/{p[1]}/{p[0]}"
    # yyyymmdd
    if re.match(r'^\d{8}$', s):
        return f"{s[6:8]}/{s[4:6]}/{s[0:4]}"
    # dd/mm/yyyy o dd/mm/yy
    if re.match(r'\d{1,2}/\d{1,2}/\d{2,4}', s):
        return s[:10]
    return s[:10]


def _fmt_cuit(val: str) -> str:
    s = re.sub(r'[^\d]', '', str(val) if val else '')
    if len(s) == 11:
        return f"{s[0:2]}-{s[2:10]}-{s[10]}"
    return s
