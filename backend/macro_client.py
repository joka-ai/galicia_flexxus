"""
MacroClient — automatización Banca Internet Empresas de Banco Macro
"""
import time
from typing import Tuple, List, Optional

try:
    from playwright.sync_api import sync_playwright, Browser, BrowserContext
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

MACRO_DEFAULT_URL = "https://banca.macro.com.ar/Techbank/BIEmpresasLogin"


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
        self._login_url           = MACRO_DEFAULT_URL
        self._usuario             = ''
        self._password            = ''

    # ─── LOGIN ────────────────────────────────────────────────────────────────
    def login(self, usuario: str, password: str, url: str = '') -> Tuple[bool, str]:
        if not PLAYWRIGHT_OK:
            return False, "Playwright no instalado. Ejecutá: pip install playwright && playwright install chromium"
        self._login_url = url or MACRO_DEFAULT_URL
        self._usuario   = usuario
        self._password  = password
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

            # Leer empresas disponibles
            self._empresas = self._leer_empresas()

            # Si no hay pantalla de selección, puede que ya entró directamente
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

    # ─── SELECCIONAR EMPRESA ──────────────────────────────────────────────────
    def seleccionar_empresa(self, empresa: str) -> Tuple[bool, str]:
        """Clickea la empresa en la pantalla de selección post-login."""
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
                    try:
                        inst = self._page.locator('.widget_institutionName').first
                        if inst.is_visible(timeout=2000):
                            self._empresa_activa = inst.inner_text(timeout=1000).strip()
                    except Exception:
                        self._empresa_activa = empresa
                    return True, f"Empresa {self._empresa_activa} seleccionada"
            return False, f"Empresa '{empresa}' no encontrada. Disponibles: {', '.join(self._empresas)}"
        except Exception as e:
            return False, f"Error al seleccionar empresa: {e}"

    # ─── CAMBIAR EMPRESA ──────────────────────────────────────────────────────
    def cambiar_empresa(self, empresa: str) -> Tuple[bool, str]:
        """Vuelve a la pantalla de selección de empresa y selecciona la nueva."""
        if not self._logged_in:
            return False, "No hay sesión activa"
        try:
            # Si ya estamos en pantalla de selección, intentar directo
            spans = self._page.locator('span[caption="name"]').all()
            if spans:
                return self.seleccionar_empresa(empresa)

            # Navegar al URL de login para volver a la selección
            self._page.goto(self._login_url, wait_until='domcontentloaded', timeout=20000)
            time.sleep(0.5)

            # Esperar si aparece directamente la selección de empresa (sesión activa)
            try:
                self._page.wait_for_function(
                    """() => document.querySelector('[id$="_actionButtonVerify"]') !== null""",
                    timeout=6000,
                )
                self._empresas = self._leer_empresas()
                return self.seleccionar_empresa(empresa)
            except Exception:
                pass

            # Si aparece la pantalla de usuario, re-login completo
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
