"""
MacroClient — automatización Banca Internet Empresas de Banco Macro
"""
import os
import tempfile
import time
from datetime import datetime
from typing import Tuple, List, Dict, Optional

try:
    from playwright.sync_api import sync_playwright, Browser, BrowserContext
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

MACRO_LOGIN_URL = "https://www.macro.com.ar/biempresas/#"


class MacroClient:
    def __init__(self, headless: bool = False):
        self.headless             = headless
        self._playwright          = None
        self._browser: Optional[Browser]        = None
        self._context: Optional[BrowserContext] = None
        self._page                = None
        self._logged_in           = False
        self._empresa_activa      = ''
        self._empresas: List[str] = []
        self._login_url           = MACRO_LOGIN_URL
        self._usuario             = ''
        self._password            = ''

    # ─── LOGIN ────────────────────────────────────────────────────────────────
    def login(self, usuario: str, password: str) -> Tuple[bool, str]:
        if not PLAYWRIGHT_OK:
            return False, "Playwright no instalado. Ejecutá: pip install playwright && playwright install chromium"
        self._usuario  = usuario
        self._password = password
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            self._page = self._context.new_page()
            self._page.on('close', self._on_close)
            self._browser.on('disconnected', self._on_close)

            self._page.goto(self._login_url, wait_until='domcontentloaded', timeout=30000)

            # Paso 1: usuario
            user_input = self._page.locator('#textField1')
            user_input.wait_for(state='visible', timeout=15000)
            user_input.click()
            user_input.fill(usuario)
            self._page.locator('#processCustomerLogin').click()

            # Paso 2: clave
            self._page.locator('#login_textField1').wait_for(state='visible', timeout=15000)
            time.sleep(0.3)
            pass_input = self._page.locator('#login_textField1')
            pass_input.click()
            pass_input.fill(password)
            self._page.locator('#processSystem_UserLogin').click()

            # Esperar selección de empresa o error
            try:
                self._page.wait_for_function(
                    """() => {
                        const btn = document.querySelector('[id$="_actionButtonVerify"]');
                        const err = document.querySelector('#errorPanelCollectionContainer');
                        const errVisible = err && err.children.length > 0 && err.offsetParent !== null;
                        return btn !== null || errVisible;
                    }""",
                    timeout=25000,
                )
            except Exception:
                pass

            # Detectar error de credenciales
            try:
                err_panel = self._page.locator('#errorPanelCollectionContainer')
                if err_panel.is_visible(timeout=500):
                    err_text = err_panel.inner_text(timeout=1000).strip()
                    if err_text:
                        return False, f"Error de login: {err_text}"
            except Exception:
                pass

            self._empresas = self._leer_empresas()

            if not self._empresas:
                try:
                    inst = self._page.locator('.widget_institutionName').first
                    if inst.is_visible(timeout=2000):
                        self._empresa_activa = inst.inner_text(timeout=1000).strip()
                        self._logged_in = True
                        return True, f"Login exitoso — empresa: {self._empresa_activa}"
                except Exception:
                    pass

            self._logged_in = True
            n = len(self._empresas)
            return True, f"Login exitoso en Banco Macro — {n} empresa(s) disponible(s)"

        except Exception as e:
            self._cleanup()
            return False, f"Error de conexión: {e}"

    # ─── SELECCIONAR / CAMBIAR EMPRESA ────────────────────────────────────────
    def seleccionar_empresa(self, empresa: str) -> Tuple[bool, str]:
        if not self._logged_in:
            return False, "No hay sesión activa"
        try:
            spans = self._page.locator('span[caption="name"]').all()
            for i, s in enumerate(spans):
                nombre = s.get_attribute('unmasked')
                if not nombre:
                    try:
                        nombre = s.inner_text(timeout=300)
                    except Exception:
                        nombre = ''
                if nombre and nombre.strip().upper() == empresa.strip().upper():
                    btn_id = f'section0_repeat{i}_actionButtonVerify'
                    self._page.locator(f'#{btn_id}').click()
                    try:
                        self._page.wait_for_load_state('networkidle', timeout=20000)
                    except Exception:
                        pass
                    time.sleep(1.5)
                    self._empresa_activa = empresa
                    try:
                        inst = self._page.locator('.widget_institutionName').first
                        if inst.is_visible(timeout=2000):
                            texto = inst.inner_text(timeout=1000).strip()
                            if texto:
                                self._empresa_activa = texto
                    except Exception:
                        pass
                    return True, f"Empresa {self._empresa_activa} seleccionada"
            return False, f"Empresa '{empresa}' no encontrada. Disponibles: {', '.join(self._empresas)}"
        except Exception as e:
            return False, f"Error al seleccionar empresa: {e}"

    def cambiar_empresa(self, empresa: str) -> Tuple[bool, str]:
        if not self._logged_in:
            return False, "No hay sesión activa"
        try:
            spans = self._page.locator('span[caption="name"]').all()
            if spans:
                return self.seleccionar_empresa(empresa)

            self._page.goto(self._login_url, wait_until='domcontentloaded', timeout=20000)
            time.sleep(0.5)

            try:
                self._page.wait_for_function(
                    """() => document.querySelector('[id$="_actionButtonVerify"]') !== null""",
                    timeout=6000,
                )
                self._empresas = self._leer_empresas()
                return self.seleccionar_empresa(empresa)
            except Exception:
                pass

            try:
                user_input = self._page.locator('#textField1')
                if user_input.is_visible(timeout=2000):
                    user_input.click()
                    user_input.fill(self._usuario)
                    self._page.locator('#processCustomerLogin').click()
                    self._page.locator('#login_textField1').wait_for(state='visible', timeout=12000)
                    time.sleep(0.3)
                    pass_input = self._page.locator('#login_textField1')
                    pass_input.click()
                    pass_input.fill(self._password)
                    self._page.locator('#processSystem_UserLogin').click()
                    try:
                        self._page.wait_for_function(
                            """() => document.querySelector('[id$="_actionButtonVerify"]') !== null""",
                            timeout=20000,
                        )
                    except Exception:
                        pass
                    self._empresas = self._leer_empresas()
                    return self.seleccionar_empresa(empresa)
            except Exception as e:
                return False, f"Error al cambiar empresa: {e}"

            return False, "No se pudo volver a la selección de empresas"
        except Exception as e:
            return False, f"Error al cambiar empresa: {e}"

    # ─── TRANSFERENCIAS / MOVIMIENTOS ─────────────────────────────────────────
    def obtener_transferencias(self, fecha_desde: str, fecha_hasta: str,
                               importe_desde: str = '', importe_hasta: str = '',
                               tipo_mov: str = 'Ninguno') -> Tuple[List[Dict], str]:
        if not self._logged_in or not self._page:
            return [], "No hay sesión activa"
        try:
            self._navegar_inicio()

            # Abrir panel de filtros si está cerrado
            btn_buscar = self._page.locator('#searchMoves_arrowButton')
            btn_buscar.wait_for(state='visible', timeout=10000)
            if btn_buscar.get_attribute('aria-expanded') == 'false':
                btn_buscar.click()
                time.sleep(0.5)

            # Fechas en formato DD/MM/YYYY
            fd = self._a_ddmmyyyy(fecha_desde)
            fh = self._a_ddmmyyyy(fecha_hasta)

            fecha_desde_input = self._page.locator('#dateFieldFechaDesde')
            fecha_hasta_input = self._page.locator('#dateFieldFechaHasta')
            fecha_desde_input.wait_for(state='visible', timeout=5000)

            fecha_desde_input.click()
            fecha_desde_input.fill(fd)
            fecha_desde_input.press('Tab')
            time.sleep(0.2)

            fecha_hasta_input.click()
            fecha_hasta_input.fill(fh)
            fecha_hasta_input.press('Tab')
            time.sleep(0.2)

            # Importes opcionales
            if importe_desde:
                self._page.locator('#importeDesde').fill(importe_desde)
            if importe_hasta:
                self._page.locator('#importeHasta').fill(importe_hasta)

            # Tipo de movimiento
            if tipo_mov and tipo_mov != 'Ninguno':
                self._page.locator('#movementType').select_option(tipo_mov)

            # Click Buscar (botón primary, no el Cerrar secondary)
            self._page.locator('button.action-button_primary_contextual').click()
            try:
                self._page.wait_for_load_state('networkidle', timeout=20000)
            except Exception:
                pass
            time.sleep(1)

            # Descargar XLS
            with self._page.expect_download(timeout=30000) as dl_info:
                self._page.locator('#actionButtonDescargaXlsCuentas').click()
            dl = dl_info.value
            tmp = os.path.join(tempfile.gettempdir(), 'macro_movimientos.xls')
            dl.save_as(tmp)

            return self._parsear_xls_movimientos(tmp)

        except Exception as e:
            return [], f"Error al obtener transferencias: {e}"

    def _navegar_inicio(self):
        """Navega al Inicio (home) donde está el panel de movimientos."""
        try:
            home = self._page.locator('[id="home"]').first
            if home.is_visible(timeout=1000):
                home.click()
            else:
                self._page.evaluate(
                    "if(typeof submitSubmenuByAjax==='function')"
                    " submitSubmenuByAjax('TransitorioVerifiedOperatorSecurityInicialApoyo','home')"
                )
        except Exception:
            pass
        try:
            self._page.wait_for_load_state('networkidle', timeout=10000)
        except Exception:
            pass
        time.sleep(1.5)

    def _a_ddmmyyyy(self, date_str: str) -> str:
        if not date_str:
            return ''
        try:
            d = datetime.strptime(date_str, '%Y-%m-%d')
            return d.strftime('%d/%m/%Y')
        except Exception:
            return date_str

    def _parsear_xls_movimientos(self, path: str) -> Tuple[List[Dict], str]:
        try:
            import pandas as pd

            try:
                df = pd.read_excel(path, header=None, engine='xlrd')
            except Exception:
                df = pd.read_excel(path, header=None, engine='openpyxl')

            # Encontrar fila de encabezados (contiene "Fecha")
            header_row = None
            for i, row in df.iterrows():
                vals = [str(v).strip().lower() for v in row.values if str(v).strip()]
                if 'fecha' in vals:
                    header_row = i
                    break

            if header_row is None:
                return [], "No se encontró encabezado en el XLS"

            df.columns = [str(v).strip() for v in df.iloc[header_row].values]
            df = df.iloc[header_row + 1:].reset_index(drop=True)

            def _parse_num(v):
                import math
                if v is None: return 0.0
                try:
                    if isinstance(v, (int, float)):
                        return 0.0 if math.isnan(float(v)) else float(v)
                except Exception:
                    pass
                s = str(v).replace('$', '').replace(' ', '')
                # Formato argentino: punto = miles, coma = decimal
                s = s.replace('.', '').replace(',', '.')
                try:
                    return float(s)
                except Exception:
                    return 0.0

            rows = []
            for _, row in df.iterrows():
                fecha = str(row.get('Fecha', '') or '').strip()
                if not fecha or fecha.lower() in ('nan', 'fecha', ''):
                    continue
                if not any(c.isdigit() for c in fecha):
                    continue
                # Ignorar filas de pie (Fecha de descarga, Empresa, Operador)
                concepto = str(row.get('Concepto', '') or '').strip()
                if any(k in fecha.lower() for k in ('descarga', 'empresa', 'operador')):
                    continue

                rows.append({
                    'fecha':          fecha,
                    'nro_referencia': str(row.get('Nro. de Referencia', '') or '').strip(),
                    'causal':         str(row.get('Causal', '') or '').strip(),
                    'concepto':       concepto,
                    'importe':        _parse_num(row.get('Importe')),
                    'saldo':          _parse_num(row.get('Saldo')),
                })

            return rows, f"{len(rows)} movimiento(s) encontrado(s)"
        except Exception as e:
            return [], f"Error al parsear XLS: {e}"

    # ─── HELPERS ──────────────────────────────────────────────────────────────
    def _leer_empresas(self) -> List[str]:
        empresas = []
        try:
            spans = self._page.locator('span[caption="name"]').all()
            for s in spans:
                nombre = s.get_attribute('unmasked')
                if not nombre:
                    try:
                        nombre = s.inner_text(timeout=300)
                    except Exception:
                        nombre = ''
                if nombre:
                    empresas.append(nombre.strip())
        except Exception:
            pass
        return empresas

    # ─── LIFECYCLE ────────────────────────────────────────────────────────────
    def logout(self):
        if self._page and self._logged_in:
            try:
                if not self._page.is_closed():
                    # Si hay empresa activa, estamos en la app principal
                    # → click Salir para volver a selección de empresa
                    if self._empresa_activa:
                        try:
                            salir_main = self._page.locator('#logoutHeaderWidget a.widget_logout_btn')
                            if salir_main.is_visible(timeout=1000):
                                salir_main.click()
                                time.sleep(2)
                        except Exception:
                            pass

                    # En pantalla de selección de empresa → click Salir (cierra sesión real)
                    try:
                        salir_emp = self._page.locator('#btLogoutPublicWidget a.widget_logout_btn')
                        if not salir_emp.is_visible(timeout=800):
                            salir_emp = self._page.locator('a.widget_logout_btn').first
                        if salir_emp.is_visible(timeout=1000):
                            salir_emp.click()
                            time.sleep(1.5)
                    except Exception:
                        pass
            except Exception:
                pass
        self._logged_in = False
        self._cleanup()

    def _cleanup(self):
        for obj, method in [(self._browser, 'close'), (self._playwright, 'stop')]:
            try:
                if obj:
                    getattr(obj, method)()
            except Exception:
                pass
        self._browser = self._context = self._page = self._playwright = None

    def __del__(self):
        self._cleanup()

    def _on_close(self, *args):
        self._logged_in = False
