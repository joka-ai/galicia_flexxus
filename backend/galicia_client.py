"""
GaliciaClient — automatización Office Banking Empresas de Banco Galicia
URL base: https://empresas.bancogalicia.com.ar
"""
import os
import tempfile
import time
from datetime import date, timedelta, datetime as dt
from typing import Tuple, List, Dict, Optional

try:
    from playwright.sync_api import sync_playwright, Browser, BrowserContext
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

from file_processor import (
    procesar_csv_recaudadora_galicia,
    procesar_csv_cheques_galicia,
)

LOGIN_URL   = "https://empresas.bancogalicia.com.ar/login"
COBROS_URL  = "https://empresas.bancogalicia.com.ar/cobros"
CHEQUES_URL = "https://empresas.bancogalicia.com.ar/cheques/recibidos"


class GaliciaClient:
    def __init__(self, headless: bool = False):
        self.headless        = headless
        self._playwright     = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page           = None
        self._logged_in      = False
        self._empresa_activa = ''

    # ═════════════════════════════════════════════════════════════════════════
    # LOGIN / LOGOUT
    # ═════════════════════════════════════════════════════════════════════════
    def login(self, usuario: str, password: str) -> Tuple[bool, str]:
        if not PLAYWRIGHT_OK:
            return False, "Playwright no instalado."
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            )
            self._page = self._context.new_page()
            self._page.on('close', self._on_close)
            self._browser.on('disconnected', self._on_close)

            self._page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=60000)

            self._page.locator("#userInput").wait_for(state="visible", timeout=60000)
            self._page.locator("#userInput").fill(usuario)
            self._page.locator("#userPassword").fill(password)
            self._page.locator("button[aria-label='Ingresar']:not([disabled])").wait_for(timeout=5000)
            self._page.locator("button[aria-label='Ingresar']").click()

            try:
                self._page.wait_for_url("**/inicio**", timeout=60000)
            except Exception:
                err = self._page.evaluate("""() => {
                    const m = document.querySelector('[data-automation-id="modalComponent"]');
                    return m && m.textContent.toLowerCase().includes('clave');
                }""")
                if err:
                    self._dismiss_modal()
                    return False, "La clave tiene algún error. Revisá la contraseña."
                return False, "Login fallido. Verificá usuario y contraseña."

            self._dismiss_modal()
            self._logged_in = True

            # Esperar que el header con la empresa esté listo
            for _ in range(30):
                self._empresa_activa = self._leer_empresa()
                if self._empresa_activa:
                    break
                time.sleep(0.1)

            return True, f"Login exitoso — empresa: {self._empresa_activa}"

        except Exception as e:
            self._cleanup()
            return False, f"Error de conexión: {e}"

    def logout(self):
        self._logged_in = False
        self._cleanup()

    # ═════════════════════════════════════════════════════════════════════════
    # CAMBIAR EMPRESA
    # ═════════════════════════════════════════════════════════════════════════
    def cambiar_empresa(self, empresa: str) -> Tuple[bool, str]:
        if not self._logged_in or not self._page:
            return False, "No hay sesión activa"
        try:
            self._dismiss_modal()
            self._dismiss_modal()
            self._page.locator('.content-drop').first.click(force=True)
            self._page.locator(f'li[title="{empresa}"]').first.wait_for(state="visible", timeout=8000)
            self._page.locator(f'li[title="{empresa}"]').first.click(force=True)

            for _ in range(150):
                nueva = self._leer_empresa()
                if nueva and nueva.lower() != self._empresa_activa.lower():
                    self._dismiss_modal()
                    self._empresa_activa = nueva
                    return True, f"Empresa cambiada a {nueva}"
                time.sleep(0.1)

            self._dismiss_modal()
            self._empresa_activa = empresa
            return True, f"Empresa cambiada a {empresa}"
        except Exception as e:
            return False, f"Error al cambiar empresa: {e}"

    # ═════════════════════════════════════════════════════════════════════════
    # RECAUDADORA
    # ═════════════════════════════════════════════════════════════════════════
    def obtener_recaudadora(self, fecha_desde: str, fecha_hasta: str) -> Tuple[List[Dict], str]:
        if not self._logged_in or not self._page:
            return [], "No hay sesión activa"
        try:
            self._navegar(COBROS_URL)
            if self._sesion_expirada():
                return [], "La sesión expiró. Iniciá sesión nuevamente."

            card1 = self._page.locator('[data-component="Card"]:has(h3:has-text("Cobranza Integrada"))').first
            card1.wait_for(state="visible", timeout=8000)
            card1.click(force=True)

            card2 = self._page.locator('.brk-card:has(h3:has-text("Consultar cobros recibidos"))').first
            card2.wait_for(state="visible", timeout=8000)
            card2.click(force=True)

            self._page.locator('input[placeholder="Desde"], table').first.wait_for(state="visible", timeout=10000)

            self._filtrar_fecha_recaudadora(fecha_desde, fecha_hasta)
            return self._descargar_csv_recaudadora()
        except Exception as e:
            return [], str(e)

    def _filtrar_fecha_recaudadora(self, fecha_desde: str, fecha_hasta: str):
        if not fecha_desde and not fecha_hasta:
            return
        try:
            date_input = self._page.locator('input[placeholder="Desde"]').first
            date_input.wait_for(state="visible", timeout=5000)
            date_input.click()
            if fecha_desde:
                self._click_dia_datepicker(fecha_desde)
            if fecha_hasta:
                self._click_dia_datepicker(fecha_hasta)
            self._page.keyboard.press("Escape")

            for texto in ["Buscar", "Aplicar", "Filtrar", "Consultar"]:
                btn = self._page.locator(f'button:has-text("{texto}")').first
                if btn.is_visible():
                    btn.click()
                    try:
                        self._page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass
                    break
        except Exception:
            pass

    def _descargar_csv_recaudadora(self) -> Tuple[List[Dict], str]:
        SIN_DATOS = "0 cobros para el período seleccionado"
        try:
            dl_btn = self._page.locator('button[aria-haspopup="true"][class*="button--icon-only"]').first
            dl_btn.wait_for(state="visible", timeout=5000)
        except Exception:
            return [], SIN_DATOS
        if dl_btn.is_disabled():
            return [], SIN_DATOS

        dl_btn.click()
        try:
            csv_item = self._page.locator('[aria-label=".CSV"]').first
            csv_item.wait_for(state="visible", timeout=8000)
        except Exception:
            return [], SIN_DATOS

        try:
            with self._page.expect_download(timeout=30000) as dl_info:
                csv_item.click()
            tmp = os.path.join(tempfile.gettempdir(), 'galicia_recaudadora_latest.csv')
            dl_info.value.save_as(tmp)
            cobros, msg = procesar_csv_recaudadora_galicia(tmp)
            return (cobros, msg) if cobros else ([], SIN_DATOS)
        except Exception as e:
            return [], f"Error al descargar el CSV: {e}"

    # ═════════════════════════════════════════════════════════════════════════
    # CHEQUES A ACEPTAR
    # ═════════════════════════════════════════════════════════════════════════
    def obtener_cheques_a_aceptar(self, fecha_desde: str, fecha_hasta: str) -> Tuple[List[Dict], str]:
        if not self._logged_in or not self._page:
            return [], "No hay sesión activa"
        try:
            self._navegar(CHEQUES_URL)
            if self._sesion_expirada():
                return [], "La sesión expiró. Iniciá sesión nuevamente."

            card = self._page.locator('[data-component="Card"]:has(h3:has-text("Aceptar"))').first
            card.wait_for(state="visible", timeout=8000)
            card.click(force=True)

            self._page.locator('button[aria-label="Filter2"]').first.wait_for(state="visible", timeout=8000)

            self._filtrar_fecha_cheques(fecha_desde, fecha_hasta)
            return self._descargar_csv_cheques()
        except Exception as e:
            return [], str(e)

    def _filtrar_fecha_cheques(self, fecha_desde: str, fecha_hasta: str):
        if not fecha_desde and not fecha_hasta:
            return
        try:
            self._page.locator('button[aria-label="Filter2"]').first.click()
            drawer = self._page.locator('[data-automation-id="sideDrawerComponent"]:has(label[aria-label="Fecha de emisión"])').first
            em_input = drawer.locator('xpath=.//label[@aria-label="Fecha de emisión"]/../..//input[@placeholder="Desde - Hasta"]').first
            em_input.wait_for(state="visible", timeout=8000)
            em_input.click()

            if fecha_desde:
                self._click_dia_datepicker(fecha_desde)
            if fecha_hasta:
                self._click_dia_datepicker(fecha_hasta)

            self._page.keyboard.press("Escape")
            aplicar = self._page.locator('button[aria-label="Aplicar"]').first
            aplicar.wait_for(state="visible", timeout=8000)
            aplicar.click()
            # Esperar que el drawer se cierre = filtro aplicado
            try:
                drawer.wait_for(state="hidden", timeout=5000)
            except Exception:
                pass
        except Exception:
            pass

    def _descargar_csv_cheques(self) -> Tuple[List[Dict], str]:
        SIN_DATOS = "0 cheques a aceptar para el período seleccionado"
        dl_btn = self._page.locator('button[aria-haspopup="true"][title="Descargar"], button[aria-haspopup="true"][aria-label="Menuitem"]').first
        if not dl_btn.is_visible() or dl_btn.is_disabled():
            return [], SIN_DATOS

        dl_btn.click()
        csv_item = self._page.locator('[aria-label="Detalle de cheques en .CSV"]').first
        if not csv_item.is_visible():
            try:
                csv_item.wait_for(state="visible", timeout=8000)
            except Exception:
                return [], SIN_DATOS

        try:
            tmp = os.path.join(tempfile.gettempdir(), 'galicia_cheques_aceptar_latest.csv')
            with self._page.expect_download(timeout=30000) as dl_info:
                csv_item.click()
                # Si hay varias páginas aparece el modal de selección
                try:
                    modal = self._page.locator('[data-automation-id="modalComponent"]:has-text("tipo de descarga")').first
                    modal.wait_for(state="visible", timeout=8000)
                    self._page.locator('label:has-text("Todas las páginas")').first.click()
                    self._page.locator('button[aria-label="Continuar"]').first.click()
                except Exception:
                    pass
            dl_info.value.save_as(tmp)
            cheques, msg = procesar_csv_cheques_galicia(tmp)
            return (cheques, msg) if cheques else ([], SIN_DATOS)
        except Exception as e:
            return [], f"Error al descargar: {e}"

    # ═════════════════════════════════════════════════════════════════════════
    # INTERNALS
    # ═════════════════════════════════════════════════════════════════════════
    def _navegar(self, url: str):
        self._page.goto(url, wait_until='domcontentloaded', timeout=60000)

    def _sesion_expirada(self) -> bool:
        if "login" in self._page.url.lower():
            self._logged_in = False
            return True
        return False

    def _leer_empresa(self) -> str:
        try:
            txt = self._page.evaluate("""() => {
                const p = document.querySelector('.content-drop p[color="#666666"]');
                if (p) { const t = p.textContent.trim(); if (t.length > 2) return t; }
                const ps = document.querySelectorAll('.content-drop p[aria-label="body"]');
                if (ps.length >= 2) { const t = ps[1].textContent.trim(); if (t.length > 2) return t; }
                for (const el of document.querySelectorAll('.content-drop p')) {
                    const t = el.textContent.trim();
                    if (t.length > 3 && t === t.toUpperCase()) return t;
                }
                return '';
            }""")
            return txt.title() if txt and txt == txt.upper() else (txt or '')
        except Exception:
            return ''

    def _dismiss_modal(self):
        try:
            self._page.evaluate("""() => {
                // Tour modal (aria-modal alertdialog)
                const tour = document.querySelector('[role="alertdialog"] button[title="Cerrar"]');
                if (tour) { tour.click(); return; }
                // Close banner
                const close = document.querySelector('[data-testid="close-button"]');
                if (close) { close.click(); }
                // Modales del portal (modalComponent)
                const modals = document.querySelectorAll('[data-automation-id="modalComponent"]');
                for (const m of modals) {
                    if (m.offsetParent === null) continue;
                    const btns = m.querySelectorAll('button');
                    for (const b of btns) {
                        const txt = b.textContent.trim().toLowerCase();
                        if (['recorrer más tarde','continuar','entendido','aceptar'].some(k => txt.includes(k))) {
                            b.click(); return;
                        }
                    }
                    const primary = m.querySelector('button[class*="primary"]');
                    if (primary) { primary.click(); return; }
                }
                // Side drawer de novedades
                const drawerClose = document.querySelector('[data-automation-id="closeSideDrawerComponent"]');
                if (drawerClose && drawerClose.offsetParent !== null) { drawerClose.click(); }
            }""")
        except Exception:
            pass

    def _click_dia_datepicker(self, fecha_iso: str):
        MESES_ABR = {
            "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
            "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
            "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
        }
        MESES_ES = {v: k for k, v in MESES_ABR.items()}
        target = dt.strptime(fecha_iso, "%Y-%m-%d")

        for _ in range(24):
            try:
                header = self._page.locator('.MonthLabel').first.inner_text(timeout=1000).strip()
                partes = header.split()
                mes_actual = MESES_ABR.get(partes[0].lower(), 0)
                anio_actual = int(partes[1]) if len(partes) > 1 else 0
                if mes_actual == target.month and anio_actual == target.year:
                    break
                cur = anio_actual * 12 + mes_actual
                tgt = target.year * 12 + target.month
                btn = 'button[aria-label="arrowfoward"]' if cur < tgt else 'button[aria-label="arrowback"]'
                self._page.locator(btn).first.click()
                time.sleep(0.2)
            except Exception:
                break

        aria_fragment = f"{target.day} de {MESES_ES[target.month]} de {target.year}"
        try:
            day_el = self._page.locator(
                f'.react-datepicker__day[aria-label*="{aria_fragment}"]:not(.react-datepicker__day--outside-month)'
            ).first
            day_el.click(timeout=2000)
            return
        except Exception:
            pass
        day_class = f"react-datepicker__day--{target.day:03d}"
        try:
            self._page.locator(f'.{day_class}:not(.react-datepicker__day--outside-month)').first.click(timeout=2000)
        except Exception:
            pass

    @staticmethod
    def _limitar_90_dias(fecha_desde: str) -> str:
        limite = (date.today() - timedelta(days=89)).isoformat()
        if not fecha_desde or fecha_desde < limite:
            return limite
        return fecha_desde

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
