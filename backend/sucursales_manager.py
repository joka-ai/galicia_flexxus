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
    Try to parse Galicia sucursales PDF.
    Returns ([], message) if image-based.
    """
    try:
        import pdfplumber
    except ImportError:
        return [], "pdfplumber no instalado."

    sucursales: List[dict] = []
    seen: set = set()

    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue
                for line in text.split('\n'):
                    parts = line.strip().split()
                    if not parts or not re.match(r'^\d{1,4}$', parts[0]):
                        continue
                    num = parts[0]
                    if num in seen:
                        continue
                    seen.add(num)
                    sucursales.append({
                        'numero':    num,
                        'localidad': ' '.join(parts[1:4]).title(),
                        'ciudad':    ' '.join(parts[1:3]).title(),
                        'provincia': '',
                        'cp':        '',
                        'domicilio': ' '.join(parts[4:]).title() if len(parts) > 4 else '',
                    })

        if sucursales:
            return sucursales, f"{len(sucursales)} sucursales extraídas del PDF Galicia"
        return [], (
            "El PDF de Galicia parece ser de imagen y no se puede extraer automáticamente. "
            "Podés subir un CSV con encabezado: numero,localidad,ciudad,provincia,cp,domicilio"
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
