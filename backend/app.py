"""
Galicia | Backend — Flask API
"""
import os
import json
import hashlib
import logging
import tempfile
import time
from collections import defaultdict
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

import bcrypt
from flask import Flask, request, jsonify, send_from_directory, session
from flask_cors import CORS

from galicia_client import GaliciaClient
from file_processor import (
    procesar_csv_recaudadora_galicia,
    procesar_csv_cheques_galicia,
    procesar_csv_extracto_galicia,
)
import sucursales_manager

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# ─── App ──────────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder='../frontend', static_url_path='')
app.secret_key = os.environ.get('APP_SECRET_KEY', 'galicia_flexxus_s3cr3t_2026_xK9z')
app.permanent_session_lifetime = timedelta(hours=8)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)
CORS(app, supports_credentials=True)

# ─── Users / passwords ────────────────────────────────────────────────────────
_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(_BASE_DIR, '..', 'users.json')


def _hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _check_password(pw: str, stored: str) -> bool:
    """Verify against bcrypt hash; transparently migrates legacy SHA-256."""
    if stored.startswith(('$2b$', '$2a$')):
        return bcrypt.checkpw(pw.encode(), stored.encode())
    return hashlib.sha256(pw.encode()).hexdigest() == stored


def _load_users() -> dict:
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_users(users: dict):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, indent=2, ensure_ascii=False)


if not os.path.exists(USERS_FILE):
    _save_users({'ADMIN': _hash_password('admin123')})

# ─── Login rate limiting ───────────────────────────────────────────────────────
_failed: dict         = defaultdict(list)
_MAX_ATTEMPTS: int    = 3
_WINDOW_SECS:  int    = 900   # 15 min


def _is_rate_limited(ip: str) -> bool:
    now   = time.time()
    clean = [t for t in _failed[ip] if now - t < _WINDOW_SECS]
    _failed[ip] = clean
    return len(clean) >= _MAX_ATTEMPTS


def _record_failure(ip: str):
    _failed[ip].append(time.time())


def _remaining_attempts(ip: str) -> int:
    now   = time.time()
    used  = len([t for t in _failed[ip] if now - t < _WINDOW_SECS])
    return max(0, _MAX_ATTEMPTS - used)

# ─── Playwright executor ───────────────────────────────────────────────────────
_pw_executor = ThreadPoolExecutor(max_workers=1)

_session: dict = {
    'client':            None,
    'cobros':            [],
    'transferencias':    [],
    'cheques':           [],
    'cheques_a_aceptar': [],
}


def _pw(fn, *args, **kwargs):
    """Run fn in the dedicated Playwright thread."""
    return _pw_executor.submit(fn, *args, **kwargs).result(timeout=180)


def _galicia_client():
    c = _session.get('client')
    return c if (c and c._logged_in) else None

# ─── Auth guard ───────────────────────────────────────────────────────────────
_EXEMPT = {'/api/app/login', '/api/app/logout', '/api/app/check'}


@app.before_request
def _require_login():
    session.permanent = True
    if request.path in _EXEMPT or request.path.startswith('/img/'):
        return None
    if request.path.startswith('/api/') and not session.get('app_logged_in'):
        return jsonify({'ok': False, 'error': 'No autenticado'}), 401
    return None

# ─── Static files ─────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('../frontend', 'index.html')


@app.route('/img/<path:filename>')
def serve_img(filename):
    return send_from_directory('../img', filename)

# ─── App auth ─────────────────────────────────────────────────────────────────
@app.route('/api/app/check')
def app_check():
    if session.get('app_logged_in'):
        return jsonify({'ok': True, 'usuario': session.get('app_user', '')})
    return jsonify({'ok': False})


@app.route('/api/app/login', methods=['POST'])
def app_login():
    ip = request.remote_addr or 'unknown'
    if _is_rate_limited(ip):
        return jsonify({'ok': False, 'error': 'Demasiados intentos. Esperá 15 minutos.'}), 429

    body     = request.json or {}
    usuario  = body.get('usuario', '').strip().upper()
    password = body.get('password', '').strip()

    if not usuario or not password:
        return jsonify({'ok': False, 'error': 'Completá usuario y contraseña'}), 400

    users  = _load_users()
    stored = users.get(usuario)

    if not stored or not _check_password(password, stored):
        _record_failure(ip)
        remaining = _remaining_attempts(ip)
        if remaining == 0:
            msg = 'Cuenta bloqueada temporalmente. Esperá 15 minutos e intentá de nuevo.'
            return jsonify({'ok': False, 'error': msg}), 429
        intento = 'intento' if remaining == 1 else 'intentos'
        return jsonify({'ok': False, 'error': f'Usuario o contraseña incorrectos. Te queda{"n" if remaining > 1 else ""} {remaining} {intento}.'}), 401

    # Migrate legacy SHA-256 hash to bcrypt on first successful login
    if not stored.startswith(('$2b$', '$2a$')):
        users[usuario] = _hash_password(password)
        _save_users(users)

    session['app_logged_in'] = True
    session['app_user']      = usuario
    return jsonify({'ok': True, 'usuario': usuario})


@app.route('/api/app/logout', methods=['POST'])
def app_logout():
    session.clear()
    return jsonify({'ok': True})

# ─── Galicia bank session ─────────────────────────────────────────────────────
def _need_galicia():
    c = _galicia_client()
    if not c:
        return None, (jsonify({'ok': False, 'error': 'Primero iniciá sesión en Galicia'}), 401)
    return c, None


@app.route('/api/galicia/status')
def galicia_status():
    c = _galicia_client()
    empresa = c._empresa_activa if c else ''
    return jsonify({'conectado': c is not None, 'empresa': empresa})


@app.route('/api/galicia/login', methods=['POST'])
def galicia_login():
    body     = request.json or {}
    usuario  = body.get('usuario', '').strip()
    password = body.get('password', '').strip()
    if not usuario or not password:
        return jsonify({'ok': False, 'error': 'Falta usuario o contraseña'}), 400

    def _do():
        prev = _session.get('client')
        if prev:
            try: prev.logout()
            except Exception: pass
        _session['client'] = None
        client = GaliciaClient()
        ok, msg = client.login(usuario, password)
        if ok:
            _session['client'] = client
        return ok, msg

    try:
        ok, msg = _pw(_do)
        if ok:
            empresa = _session['client']._empresa_activa if _session.get('client') else ''
            return jsonify({'ok': True, 'mensaje': msg, 'empresa': empresa})
        return jsonify({'ok': False, 'error': msg}), 401
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/galicia/cambiar_empresa', methods=['POST'])
def galicia_cambiar_empresa():
    client, err = _need_galicia()
    if err: return err
    empresa = (request.json or {}).get('empresa', '').strip()
    if not empresa:
        return jsonify({'ok': False, 'error': 'Falta el nombre de empresa'}), 400
    try:
        ok, msg = _pw(client.cambiar_empresa, empresa)
        return jsonify({'ok': ok, 'mensaje': msg} if ok else {'ok': False, 'error': msg})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/galicia/logout', methods=['POST'])
def galicia_logout():
    def _do():
        c = _session.get('client')
        if c:
            try: c.logout()
            except Exception: pass
        _session['client'] = None
    try: _pw(_do)
    except Exception: pass
    return jsonify({'ok': True})

# ─── Galicia data queries ─────────────────────────────────────────────────────
def _galicia_query(method: str, result_key: str, store_key: str):
    client, err = _need_galicia()
    if err: return err
    body        = request.json or {}
    fecha_desde = body.get('fecha_desde', '')
    fecha_hasta = body.get('fecha_hasta', '')
    try:
        datos, msg = _pw(getattr(client, method), fecha_desde, fecha_hasta)
        _session[store_key] = datos
        return jsonify({'ok': True, result_key: datos, 'total': len(datos), 'mensaje': msg})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/galicia/recaudadora',     methods=['POST'])
def galicia_recaudadora():
    return _galicia_query('obtener_recaudadora',     'cobros',        'cobros')

@app.route('/api/galicia/transferencias',  methods=['POST'])
def galicia_transferencias():
    return _galicia_query('obtener_transferencias',  'transferencias', 'transferencias')

@app.route('/api/galicia/cheques',         methods=['POST'])
def galicia_cheques():
    return _galicia_query('obtener_cheques',         'cheques',       'cheques')

@app.route('/api/galicia/cheques_a_aceptar', methods=['POST'])
def galicia_cheques_a_aceptar():
    return _galicia_query('obtener_cheques_a_aceptar', 'cheques',     'cheques_a_aceptar')

# ─── Manual CSV upload ────────────────────────────────────────────────────────
@app.route('/api/upload/<tipo>', methods=['POST'])
def upload_csv(tipo):
    file = request.files.get('file')
    if not file:
        return jsonify({'ok': False, 'error': 'No se recibió ningún archivo'}), 400

    tmp = os.path.join(tempfile.gettempdir(), f'galicia_{tipo}_manual.csv')
    file.save(tmp)

    try:
        if tipo == 'recaudadora':
            datos, msg = procesar_csv_recaudadora_galicia(tmp)
            return jsonify({'ok': True, 'cobros': datos, 'total': len(datos), 'mensaje': msg})

        if tipo == 'cheques_aceptar':
            datos, msg = procesar_csv_cheques_galicia(tmp)
            return jsonify({'ok': True, 'cheques': datos, 'total': len(datos), 'mensaje': msg})

        if tipo == 'movimientos':
            try:
                datos, msg = procesar_csv_recaudadora_galicia(tmp)
                if datos:
                    return jsonify({'ok': True, 'tipo_detectado': 'recaudadora',
                                    'cobros': datos, 'total': len(datos), 'mensaje': msg})
            except Exception:
                pass
            datos, msg = procesar_csv_extracto_galicia(tmp)
            return jsonify({'ok': True, 'tipo_detectado': 'movimientos',
                            'transferencias': datos, 'total': len(datos), 'mensaje': msg})

        return jsonify({'ok': False, 'error': f'Tipo desconocido: {tipo}'}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

# ─── Sucursales ───────────────────────────────────────────────────────────────
@app.route('/api/bancos', methods=['GET'])
def api_bancos_get():
    return jsonify({'ok': True, **sucursales_manager.cargar_bancos()})


@app.route('/api/bancos/upload', methods=['POST'])
def api_bancos_upload():
    file = request.files.get('file')
    if not file:
        return jsonify({'ok': False, 'error': 'No se recibió archivo'}), 400
    ext = os.path.splitext(file.filename or '')[1].lower()
    tmp = os.path.join(tempfile.gettempdir(), f'bancos_upload{ext}')
    file.save(tmp)
    try:
        if ext == '.csv':
            bancos, msg = sucursales_manager.parsear_csv_bancos(tmp)
        else:
            bancos, msg = sucursales_manager.parsear_pdf_bcra(tmp)
        if bancos:
            data = sucursales_manager.guardar_bancos(bancos, file.filename or '')
            return jsonify({'ok': True, 'mensaje': msg, **data})
        return jsonify({'ok': False, 'error': msg}), 422
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/sucursales/<banco>', methods=['GET'])
def api_sucursales_get(banco):
    if banco not in ('galicia', 'macro'):
        return jsonify({'ok': False, 'error': 'Banco inválido'}), 400
    return jsonify({'ok': True, **sucursales_manager.cargar(banco)})


@app.route('/api/sucursales/<banco>/upload', methods=['POST'])
def api_sucursales_upload(banco):
    if banco not in ('galicia', 'macro'):
        return jsonify({'ok': False, 'error': 'Banco inválido'}), 400
    file = request.files.get('file')
    if not file:
        return jsonify({'ok': False, 'error': 'No se recibió archivo'}), 400

    ext = os.path.splitext(file.filename or '')[1].lower()
    tmp = os.path.join(tempfile.gettempdir(), f'sucursales_{banco}_upload{ext}')
    file.save(tmp)

    try:
        if ext == '.csv':
            rows, msg = sucursales_manager.parsear_csv_sucursales(tmp)
        elif banco == 'macro':
            rows, msg = sucursales_manager.parsear_pdf_macro(tmp)
        else:
            rows, msg = sucursales_manager.parsear_pdf_galicia(tmp)

        if rows:
            data = sucursales_manager.guardar(banco, rows, file.filename or '')
            return jsonify({'ok': True, 'mensaje': msg, **data})
        return jsonify({'ok': False, 'error': msg}), 422
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    sucursales_manager.init()
    print(f"\n{'='*50}\n  Galicia | Consultas — http://localhost:5000\n{'='*50}\n")
    app.run(debug=False, port=5000, threaded=True)
