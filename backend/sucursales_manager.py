"""
Sucursales Manager — parse PDFs and manage sucursal lookup data.
Stores parsed data as JSON in the sucursales/ folder.
"""
import os
import re
import json
from datetime import datetime
from typing import List, Dict, Tuple

_BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
SUCURSALES_DIR  = os.path.join(_BASE_DIR, '..', 'sucursales')

_PDF_DEFAULTS = {
    'galicia': 'Sucursales-Galicia.pdf',
    'macro':   'Sucursales-operatoria-20-05-2020.pdf',
}

# Column x-positions from Macro PDF header (pdfplumber word coordinates)
_MACRO_X = dict(suc=20, loc=83, dom=239, cp=447, ciu=511, prov=645, hor=767, tol=30)


# ─────────────────────────────────────────────────────────────────────────────
# JSON storage
# ─────────────────────────────────────────────────────────────────────────────
def _json_path(banco: str) -> str:
    os.makedirs(SUCURSALES_DIR, exist_ok=True)
    return os.path.join(SUCURSALES_DIR, f'{banco}.json')


def cargar(banco: str) -> dict:
    path = _json_path(banco)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'updated': None, 'source_file': None, 'total': 0, 'sucursales': []}


def guardar(banco: str, sucursales: List[dict], source_file: str = '') -> dict:
    data = {
        'updated':     datetime.now().strftime('%d/%m/%Y %H:%M'),
        'source_file': source_file,
        'total':       len(sucursales),
        'sucursales':  sucursales,
    }
    with open(_json_path(banco), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def as_lookup(banco: str) -> Dict[str, str]:
    """
    Return {numero_str: localidad} lookup dict.
    Adds both zero-padded and stripped variants so '002' and '2' both work.
    """
    data = cargar(banco)
    result = {}
    for s in data.get('sucursales', []):
        num = str(s.get('numero', '')).strip()
        loc = s.get('localidad', '') or s.get('ciudad', '')
        if not num or not loc:
            continue
        result[num] = loc
        stripped = num.lstrip('0') or '0'
        if stripped != num:
            result[stripped] = loc
    return result


def get_all_lookups() -> Dict[str, Dict[str, str]]:
    return {
        'galicia': as_lookup('galicia'),
        'macro':   as_lookup('macro'),
    }


# ─────────────────────────────────────────────────────────────────────────────
# PDF parsers
# ─────────────────────────────────────────────────────────────────────────────
def _gcol(words, x0: float, x1: float, tol: float) -> str:
    return ' '.join(w['text'] for w in words if x0 - tol <= w['x0'] < x1)


def parsear_pdf_macro(path: str) -> Tuple[List[dict], str]:
    """Parse Macro sucursales PDF (text-based). Returns (list, message)."""
    try:
        import pdfplumber
    except ImportError:
        return [], "pdfplumber no instalado. Ejecutá: pip install pdfplumber"

    X = _MACRO_X
    sucursales: List[dict] = []
    seen: set = set()

    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                ws = page.extract_words()
                if not ws:
                    continue
                lines: Dict[int, list] = {}
                for w in ws:
                    t = round(w['top'])
                    lines.setdefault(t, []).append(w)

                for _, lw in sorted(lines.items()):
                    first = lw[0]
                    if not re.match(r'^\d{2,4}$', first['text']):
                        continue
                    if first['x0'] > X['loc'] - X['tol']:
                        continue
                    num = first['text']
                    if num in seen:
                        continue
                    seen.add(num)

                    localidad = _gcol(lw, X['loc'], X['dom'], X['tol']).title().strip()
                    ciudad    = _gcol(lw, X['ciu'], X['prov'], X['tol']).title().strip()
                    provincia = _gcol(lw, X['prov'], X['hor'], X['tol']).title().strip()
                    cp        = _gcol(lw, X['cp'],  X['ciu'], X['tol']).strip()
                    domicilio = _gcol(lw, X['dom'], X['cp'],  X['tol']).title().strip()

                    sucursales.append({
                        'numero':    num,
                        'localidad': localidad,
                        'ciudad':    ciudad,
                        'provincia': provincia,
                        'cp':        cp,
                        'domicilio': domicilio,
                    })

        return sucursales, f"{len(sucursales)} sucursales extraídas del PDF Macro"
    except Exception as e:
        return [], f"Error al parsear PDF Macro: {e}"


def parsear_pdf_galicia(path: str) -> Tuple[List[dict], str]:
    """
    Parse Galicia sucursales PDF.
    Formato de tabla: PROVINCIA/REGIÓN | N° | NOMBRE | DIRECCIÓN | LOCALIDAD | C.P.
    Usa posiciones x de palabras (pdfplumber extract_words) para identificar columnas.
    """
    try:
        import pdfplumber
    except ImportError:
        return [], "pdfplumber no instalado. Ejecutá: pip install pdfplumber"

    sucursales: List[dict] = []
    seen: set = set()

    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                words = page.extract_words(keep_blank_chars=False)
                if not words:
                    continue

                pw = page.width or 595  # ancho de página (A4 = 595 pt)

                # Umbrales de columnas (proporciones sobre el ancho de página)
                # N°: empieza en ~27% del ancho
                # LOCALIDAD: empieza en ~73%
                # C.P.: empieza en ~88%
                x_num_min = pw * 0.24
                x_num_max = pw * 0.36
                x_loc     = pw * 0.72
                x_cp      = pw * 0.87

                # Agrupar palabras por fila (top redondeado a 4 pt)
                rows: Dict[int, list] = {}
                for w in words:
                    y = round(w['top'] / 4) * 4
                    rows.setdefault(y, []).append(w)

                for y_key in sorted(rows.keys()):
                    row_words = sorted(rows[y_key], key=lambda w: w['x0'])

                    # Buscar el N° — número de 1-4 dígitos en la columna N°
                    num_word = None
                    for w in row_words:
                        if re.match(r'^\d{1,4}$', w['text']) and x_num_min <= w['x0'] <= x_num_max:
                            num_word = w
                            break
                    if not num_word:
                        continue
                    num = num_word['text']
                    if num in seen:
                        continue

                    # Palabras después del N°
                    rest = [w for w in row_words if w['x0'] > x_num_max]
                    if not rest:
                        continue

                    # C.P. — último token si es número de 4-5 dígitos en columna CP
                    cp = ''
                    if rest and re.match(r'^\d{4,5}$', rest[-1]['text']) and rest[-1]['x0'] >= x_cp:
                        cp = rest[-1]['text']
                        rest = rest[:-1]

                    # LOCALIDAD — palabras en columna LOCALIDAD
                    loc_words   = [w['text'] for w in rest if w['x0'] >= x_loc]
                    other_words = [w['text'] for w in rest if w['x0'] < x_loc]

                    localidad = ' '.join(loc_words).title().strip()

                    # Fallback: si no se detectó LOCALIDAD por columna,
                    # buscar palabras en mayúsculas al final de la fila
                    if not localidad and other_words:
                        loc_start = len(other_words)
                        for i in range(len(other_words) - 1, -1, -1):
                            tok = other_words[i]
                            if re.match(r'^[A-ZÁÉÍÓÚÑÜ\-\.]+$', tok) and len(tok) > 1:
                                loc_start = i
                            else:
                                break
                        if loc_start < len(other_words):
                            localidad   = ' '.join(other_words[loc_start:]).title().strip()
                            other_words = other_words[:loc_start]

                    if not localidad:
                        continue

                    seen.add(num)
                    sucursales.append({
                        'numero':    num,
                        'nombre':    '',
                        'localidad': localidad,
                        'ciudad':    localidad,
                        'provincia': '',
                        'cp':        cp,
                        'domicilio': ' '.join(other_words).strip(),
                    })

        if sucursales:
            return sucursales, f"{len(sucursales)} sucursales extraídas del PDF Galicia"
        return [], (
            "No se encontraron sucursales en el PDF. "
            "Verificá que sea el formato correcto: PROVINCIA/REGIÓN | N° | NOMBRE | DIRECCIÓN | LOCALIDAD | C.P."
        )
    except Exception as e:
        return [], f"Error al parsear PDF Galicia: {e}"


def parsear_csv_sucursales(path: str) -> Tuple[List[dict], str]:
    """
    Parse a manual CSV of sucursales.
    Required column: numero. Optional: localidad, ciudad, provincia, cp, domicilio.
    """
    try:
        import pandas as pd
        df = pd.read_csv(path, dtype=str, sep=None, engine='python')
        df.columns = [c.strip().lower() for c in df.columns]
        df = df.dropna(how='all')

        CAMPOS = ['numero', 'localidad', 'ciudad', 'provincia', 'cp', 'domicilio']
        rows: List[dict] = []
        seen: set = set()

        for _, row in df.iterrows():
            num = str(row.get('numero', '') or '').strip().split('.')[0]
            if not num or not num.isdigit() or num in seen:
                continue
            seen.add(num)
            rows.append({
                c: str(row.get(c, '') or '').strip()
                for c in CAMPOS
            } | {'numero': num})

        return rows, f"{len(rows)} sucursales del CSV"
    except Exception as e:
        return [], f"Error al parsear CSV: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# Auto-init from bundled PDFs
# ─────────────────────────────────────────────────────────────────────────────
def _init_banco(banco: str):
    """Parse the bundled PDF if no JSON exists yet or if JSON is empty."""
    if os.path.exists(_json_path(banco)):
        existing = cargar(banco)
        if existing.get('total', 0) > 0:
            return
    pdf_name = _PDF_DEFAULTS.get(banco, '')
    pdf_path = os.path.join(SUCURSALES_DIR, pdf_name)
    if not os.path.exists(pdf_path):
        return
    fn = parsear_pdf_macro if banco == 'macro' else parsear_pdf_galicia
    sucursales, msg = fn(pdf_path)
    print(f"[SUCURSALES] {banco}: {msg}")
    if sucursales:
        guardar(banco, sucursales, pdf_name)


def init():
    """Called once at app startup."""
    for banco in ('galicia', 'macro'):
        try:
            _init_banco(banco)
        except Exception as e:
            print(f"[SUCURSALES] init {banco} error: {e}")
    _init_bancos()


# ─────────────────────────────────────────────────────────────────────────────
# Bancos BCRA
# ─────────────────────────────────────────────────────────────────────────────
_BANCOS_JSON = os.path.join(SUCURSALES_DIR, 'bancos.json')

_BANCOS_DEFAULT: List[dict] = [
    {"codigo":"007","nombre":"Banco de Galicia y Buenos Aires S.A.U.","grupo":"A"},
    {"codigo":"011","nombre":"Banco de la Nación Argentina","grupo":"A"},
    {"codigo":"014","nombre":"Banco de la Provincia de Buenos Aires","grupo":"A"},
    {"codigo":"015","nombre":"Industrial and Commercial Bank of China (Argentina) S.A.U.","grupo":"A"},
    {"codigo":"016","nombre":"Citibank N.A.","grupo":"A"},
    {"codigo":"017","nombre":"Banco BBVA Argentina S.A.","grupo":"A"},
    {"codigo":"020","nombre":"Banco de la Provincia de Córdoba S.A.","grupo":"A"},
    {"codigo":"027","nombre":"Banco Supervielle S.A.","grupo":"A"},
    {"codigo":"029","nombre":"Banco de la Ciudad de Buenos Aires","grupo":"A"},
    {"codigo":"034","nombre":"Banco Patagonia S.A.","grupo":"A"},
    {"codigo":"044","nombre":"Banco Hipotecario S.A.","grupo":"A"},
    {"codigo":"045","nombre":"Banco de San Juan S.A.","grupo":"A"},
    {"codigo":"072","nombre":"Banco Santander Argentina S.A.","grupo":"A"},
    {"codigo":"150","nombre":"HSBC Bank Argentina S.A.","grupo":"A"},
    {"codigo":"191","nombre":"Banco Credicoop Cooperativo Limitado","grupo":"A"},
    {"codigo":"259","nombre":"Banco Itau Argentina S.A.","grupo":"A"},
    {"codigo":"285","nombre":"Banco Macro S.A.","grupo":"A"},
    {"codigo":"330","nombre":"Nuevo Banco de Santa Fe S.A.","grupo":"A"},
    {"codigo":"083","nombre":"Banco del Chubut S.A.","grupo":"B"},
    {"codigo":"086","nombre":"Banco de Santa Cruz S.A.","grupo":"B"},
    {"codigo":"093","nombre":"Banco de la Pampa S.E.M.","grupo":"B"},
    {"codigo":"094","nombre":"Banco de Corrientes S.A.","grupo":"B"},
    {"codigo":"097","nombre":"Banco Provincia del Neuquén S.A.","grupo":"B"},
    {"codigo":"198","nombre":"Banco de Valores S.A.","grupo":"B"},
    {"codigo":"299","nombre":"Banco Comafi S.A.","grupo":"B"},
    {"codigo":"300","nombre":"Banco de Inversión y Comercio Exterior S.A.","grupo":"B"},
    {"codigo":"311","nombre":"Nuevo Banco del Chaco S.A.","grupo":"B"},
    {"codigo":"315","nombre":"Banco de Formosa S.A.","grupo":"B"},
    {"codigo":"319","nombre":"Banco CMF S.A.","grupo":"B"},
    {"codigo":"321","nombre":"Banco de Santiago del Estero S.A.","grupo":"B"},
    {"codigo":"322","nombre":"Banco Industrial S.A.","grupo":"B"},
    {"codigo":"386","nombre":"Nuevo Banco de Entre Ríos S.A.","grupo":"B"},
    {"codigo":"065","nombre":"Banco Municipal de Rosario","grupo":"C"},
    {"codigo":"131","nombre":"Bank of China Limited, Sucursal Buenos Aires","grupo":"C"},
    {"codigo":"143","nombre":"Brubank S.A.U.","grupo":"C"},
    {"codigo":"147","nombre":"Bibank S.A.","grupo":"C"},
    {"codigo":"158","nombre":"Open Bank Argentina S.A.","grupo":"C"},
    {"codigo":"165","nombre":"JPMorgan Chase Bank N.A. (Sucursal Buenos Aires)","grupo":"C"},
    {"codigo":"247","nombre":"Banco Roela S.A.","grupo":"C"},
    {"codigo":"254","nombre":"Banco Mariva S.A.","grupo":"C"},
    {"codigo":"266","nombre":"BNP Paribas","grupo":"C"},
    {"codigo":"268","nombre":"Banco Provincia de Tierra del Fuego","grupo":"C"},
    {"codigo":"269","nombre":"Banco de la República Oriental del Uruguay","grupo":"C"},
    {"codigo":"277","nombre":"Banco Sáenz S.A.","grupo":"C"},
    {"codigo":"281","nombre":"Banco Meridian S.A.","grupo":"C"},
    {"codigo":"301","nombre":"Banco Piano S.A.","grupo":"C"},
    {"codigo":"305","nombre":"Banco Julio S.A.","grupo":"C"},
    {"codigo":"309","nombre":"Banco Rioja S.A.U.","grupo":"C"},
    {"codigo":"310","nombre":"Banco del Sol S.A.","grupo":"C"},
    {"codigo":"312","nombre":"Banco VOII S.A.","grupo":"C"},
    {"codigo":"331","nombre":"Banco Cetelem Argentina S.A.","grupo":"C"},
    {"codigo":"332","nombre":"Banco de Servicios Financieros S.A.","grupo":"C"},
    {"codigo":"338","nombre":"Banco de Servicios y Transacciones S.A.","grupo":"C"},
    {"codigo":"339","nombre":"RCI Banque S.A.","grupo":"C"},
    {"codigo":"340","nombre":"BACS Banco de Crédito y Securitización S.A.","grupo":"C"},
    {"codigo":"341","nombre":"Banco Masventas S.A.","grupo":"C"},
    {"codigo":"384","nombre":"Wilobank S.A.U.","grupo":"C"},
    {"codigo":"389","nombre":"Banco Columbia S.A.","grupo":"C"},
    {"codigo":"426","nombre":"Banco Bica S.A.","grupo":"C"},
    {"codigo":"431","nombre":"Banco Coinag S.A.","grupo":"C"},
    {"codigo":"432","nombre":"Banco de Comercio S.A.","grupo":"C"},
    {"codigo":"435","nombre":"Banco Súcredito Regional S.A.U.","grupo":"C"},
    {"codigo":"448","nombre":"Banco Dino S.A.","grupo":"C"},
]


def cargar_bancos() -> dict:
    os.makedirs(SUCURSALES_DIR, exist_ok=True)
    if os.path.exists(_BANCOS_JSON):
        try:
            with open(_BANCOS_JSON, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'updated': None, 'source_file': 'BCRA Comunicación A 7896',
            'total': len(_BANCOS_DEFAULT), 'bancos': _BANCOS_DEFAULT}


def guardar_bancos(bancos: List[dict], source_file: str = '') -> dict:
    os.makedirs(SUCURSALES_DIR, exist_ok=True)
    data = {
        'updated':     datetime.now().strftime('%d/%m/%Y %H:%M'),
        'source_file': source_file,
        'total':       len(bancos),
        'bancos':      bancos,
    }
    with open(_BANCOS_JSON, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def parsear_csv_bancos(path: str) -> Tuple[List[dict], str]:
    """CSV con columnas: codigo, nombre, grupo"""
    try:
        import pandas as pd
        df = pd.read_csv(path, dtype=str, sep=None, engine='python')
        df.columns = [c.strip().lower() for c in df.columns]
        df = df.dropna(how='all')
        rows: List[dict] = []
        seen: set = set()
        for _, row in df.iterrows():
            cod = str(row.get('codigo', '') or '').strip()
            if not cod or cod in seen:
                continue
            seen.add(cod)
            rows.append({
                'codigo': cod.zfill(3),
                'nombre': str(row.get('nombre', '') or '').strip(),
                'grupo':  str(row.get('grupo',  'A') or 'A').strip().upper(),
            })
        return rows, f"{len(rows)} bancos del CSV"
    except Exception as e:
        return [], f"Error al parsear CSV bancos: {e}"


def parsear_pdf_bcra(path: str) -> Tuple[List[dict], str]:
    """Parsea el PDF de listado BCRA (Comunicación 'A' — texto, no imagen)."""
    try:
        import pdfplumber
    except ImportError:
        return [], "pdfplumber no instalado."
    try:
        bancos: List[dict] = []
        seen: set = set()
        grupo = 'A'
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ''
                for line in text.split('\n'):
                    line = line.strip()
                    if re.search(r'Grupo\s+A', line, re.I):
                        grupo = 'A'
                    elif re.search(r'Grupo\s+B', line, re.I):
                        grupo = 'B'
                    elif re.search(r'Grupo\s+C', line, re.I):
                        grupo = 'C'
                    m = re.match(r'^(\d{1,5})\s+(.+)$', line)
                    if m:
                        cod = m.group(1).zfill(3)
                        if cod not in seen:
                            seen.add(cod)
                            bancos.append({'codigo': cod, 'nombre': m.group(2).strip(), 'grupo': grupo})
        if bancos:
            return bancos, f"{len(bancos)} bancos del PDF BCRA"
        return [], "No se encontraron bancos en el PDF."
    except Exception as e:
        return [], f"Error al parsear PDF BCRA: {e}"


def _init_bancos():
    if not os.path.exists(_BANCOS_JSON):
        guardar_bancos(_BANCOS_DEFAULT, 'BCRA Comunicación A 7896 (carga inicial)')
        print(f"[BANCOS] {len(_BANCOS_DEFAULT)} bancos guardados desde datos iniciales")
