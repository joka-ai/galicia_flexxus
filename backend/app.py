"""
Galicia | Backend — Flask API
"""
import os
import sys
import json
import socket
import hashlib
import logging
import logging.handlers
import platform
import tempfile
import threading
import time
import webbrowser
from collections import defaultdict
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

import bcrypt
from flask import Flask, request, jsonify, send_from_directory, session
from flask_cors import CORS

from galicia_client import GaliciaClient
from macro_client import MacroClient
from file_processor import (
    procesar_csv_recaudadora_galicia,
    procesar_csv_cheques_galicia,
)
import sucursales_manager

# ─── Logging ──────────────────────────────────────────────────────────────────
_BASE_DIR_LOG = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR      = os.path.join(_BASE_DIR_LOG, '..', 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_FILE = os.path.join(_LOG_DIR, 'debug.log')

_fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s',
                          datefmt='%Y-%m-%d %H:%M:%S')
_fh  = logging.handlers.RotatingFileHandler(
    _LOG_FILE, maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
_fh.setFormatter(_fmt)
_fh.setLevel(logging.DEBUG)

logging.getLogger().setLevel(logging.DEBUG)
logging.getLogger().addHandler(_fh)
logging.getLogger('werkzeug').setLevel(logging.WARNING)

_log = logging.getLogger('galicia_app')

# ─── App ──────────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder='../frontend', static_url_path='')
app.secret_key = os.environ.get('APP_SECRET_KEY', 'galicia_flexxus_s3cr3t_2026_xK9z')
app.permanent_session_lifetime = timedelta(hours=8)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)
CORS(app, supports_credentials=True)


@app.errorhandler(Exception)
def handle_any_exception(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e   # 404, 403, etc. se manejan normalmente
    import traceback
    traceback.print_exc()
    return jsonify({'ok': False, 'error': str(e)}), 500

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

# ─── Playwright executors ─────────────────────────────────────────────────────
_pw_executor       = ThreadPoolExecutor(max_workers=1)
_pw_macro_executor = ThreadPoolExecutor(max_workers=1)

_session: dict = {
    'client':            None,
    'cobros':            [],
    'cheques_a_aceptar': [],
}

_macro_session: dict = {
    'client': None,
}


def _pw(fn, *args, **kwargs):
    """Run fn in the dedicated Galicia Playwright thread."""
    return _pw_executor.submit(fn, *args, **kwargs).result(timeout=180)


def _pw_macro(fn, *args, **kwargs):
    """Run fn in the dedicated Macro Playwright thread."""
    return _pw_macro_executor.submit(fn, *args, **kwargs).result(timeout=180)


def _galicia_client():
    c = _session.get('client')
    if not c or not c._logged_in:
        return None
    # Verificación activa: si el browser fue cerrado por el usuario
    try:
        if c._page and c._page.is_closed():
            c._logged_in = False
            return None
        if c._browser and not c._browser.is_connected():
            c._logged_in = False
            return None
    except Exception:
        # Si no podemos verificar el estado, asumir que está cerrado
        c._logged_in = False
        return None
    return c


def _macro_client():
    c = _macro_session.get('client')
    if not c or not c._logged_in:
        return None
    try:
        if c._page and c._page.is_closed():
            c._logged_in = False
            return None
        if c._browser and not c._browser.is_connected():
            c._logged_in = False
            return None
    except Exception:
        c._logged_in = False
        return None
    return c

# ─── Auth guard ───────────────────────────────────────────────────────────────
_EXEMPT = {'/api/app/login', '/api/app/logout', '/api/app/check', '/api/debug/log'}


@app.before_request
def _require_login():
    session.permanent = True
    if request.path in _EXEMPT or request.path.startswith('/img/'):
        return None
    if request.path.startswith('/api/') and not session.get('app_logged_in'):
        return jsonify({'ok': False, 'error': 'No autenticado'}), 401
    return None

# ─── Static files ─────────────────────────────────────────────────────────────
@app.route('/api/debug/log', methods=['POST'])
def debug_log_frontend():
    data = request.json or {}
    msg  = data.get('message', '')
    lvl  = data.get('level', 'error').lower()
    logger = logging.getLogger('frontend')
    if lvl == 'warn':
        logger.warning(msg)
    elif lvl == 'info':
        logger.info(msg)
    else:
        logger.error(msg)
    return jsonify({'ok': True})

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
    try:
        c = _galicia_client()
        empresa = c._empresa_activa if c else ''
        return jsonify({'conectado': c is not None, 'empresa': empresa})
    except Exception as e:
        return jsonify({'conectado': False, 'empresa': '', 'error': str(e)})


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
        client = GaliciaClient(headless=False)
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

@app.route('/api/galicia/cheques_a_aceptar', methods=['POST'])
def galicia_cheques_a_aceptar():
    return _galicia_query('obtener_cheques_a_aceptar', 'cheques',     'cheques_a_aceptar')

# ─── Banco Macro session ─────────────────────────────────────────────────────
@app.route('/api/macro/status')
def macro_status():
    try:
        c = _macro_client()
        return jsonify({
            'conectado': c is not None,
            'empresa':   c._empresa_activa if c else '',
            'empresas':  c._empresas       if c else [],
        })
    except Exception as e:
        return jsonify({'conectado': False, 'empresa': '', 'empresas': [], 'error': str(e)})


@app.route('/api/macro/login', methods=['POST'])
def macro_login():
    body     = request.json or {}
    usuario  = body.get('usuario', '').strip()
    password = body.get('password', '').strip()
    url      = body.get('url', '').strip()
    if not usuario or not password:
        return jsonify({'ok': False, 'error': 'Falta usuario o contraseña'}), 400

    def _do():
        prev = _macro_session.get('client')
        if prev:
            try: prev.logout()
            except Exception: pass
        _macro_session['client'] = None
        client = MacroClient(headless=False)
        ok, msg = client.login(usuario, password, url)
        if ok:
            _macro_session['client'] = client
        return ok, msg

    try:
        ok, msg = _pw_macro(_do)
        if ok:
            c = _macro_session.get('client')
            return jsonify({
                'ok':      True,
                'mensaje': msg,
                'empresas': c._empresas       if c else [],
                'empresa':  c._empresa_activa if c else '',
            })
        return jsonify({'ok': False, 'error': msg}), 401
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/macro/logout', methods=['POST'])
def macro_logout():
    def _do():
        c = _macro_session.get('client')
        if c:
            try: c.logout()
            except Exception: pass
        _macro_session['client'] = None
    try: _pw_macro(_do)
    except Exception: pass
    return jsonify({'ok': True})


@app.route('/api/macro/cambiar_empresa', methods=['POST'])
def macro_cambiar_empresa():
    c = _macro_client()
    if not c:
        return jsonify({'ok': False, 'error': 'Primero iniciá sesión en Macro'}), 401
    empresa = (request.json or {}).get('empresa', '').strip()
    if not empresa:
        return jsonify({'ok': False, 'error': 'Falta el nombre de empresa'}), 400

    def _do():
        if c._empresas and not c._empresa_activa:
            return c.seleccionar_empresa(empresa)
        return c.cambiar_empresa(empresa)

    try:
        ok, msg = _pw_macro(_do)
        return jsonify(
            {'ok': ok, 'mensaje': msg, 'empresa': c._empresa_activa}
            if ok else {'ok': False, 'error': msg}
        )
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


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
def _puerto_libre(inicio=5000, fin=5020) -> int:
    """Devuelve el primer puerto disponible en el rango dado."""
    for p in range(inicio, fin):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', p))
                return p
            except OSError:
                continue
    return inicio  # fallback (si todos están ocupados igual intenta)


if __name__ == '__main__':
    sucursales_manager.init()
    PORT = _puerto_libre()
    URL  = f'http://localhost:{PORT}'

    _log.info('=' * 60)
    _log.info('Galicia | Consultas — iniciando')
    _log.info(f'Python   : {sys.version}')
    _log.info(f'SO       : {platform.system()} {platform.version()}')
    _log.info(f'Maquina  : {platform.node()}')
    _log.info(f'Puerto   : {PORT}')
    _log.info(f'Log file : {os.path.abspath(_LOG_FILE)}')
    _log.info('=' * 60)

    print(f"\n{'='*50}\n  Galicia | Consultas — {URL}\n{'='*50}\n")
    print(f"  Debug log: {os.path.abspath(_LOG_FILE)}\n")

    threading.Timer(1.5, lambda: webbrowser.open(URL)).start()
    app.run(debug=False, host='127.0.0.1', port=PORT, threaded=True)
