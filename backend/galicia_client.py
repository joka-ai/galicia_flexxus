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

LOGIN_URL       = "https://empresas.bancogalicia.com.ar/login"
COBROS_URL      = "https://empresas.bancogalicia.com.ar/cobros/CobranzasInformadas"
CHEQUES_URL     = "https://empresas.bancogalicia.com.ar/cheques/recibidos"


class GaliciaClient:
    def __init__(self, headless: bool = False):
        self.headless         = headless
        self._playwright      = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page            = None
        self._logged_in       = False
        self._empresa_activa  = ''

    # ─────────────────────────────────────────────────────────────────────────
    # LIFECYCLE
    # ─────────────────────────────────────────────────────────────────────────
    def login(self, usuario: str, password: str) -> Tuple[bool, str]:
        if not PLAYWRIGHT_OK:
            return False, "Playwright no instalado. Ejecutá: pip install playwright && playwright install chromium"
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

            # Intenta cargar la página con reintento automático en caso de timeout
            self._goto_con_retry(LOGIN_URL)
            self._cerrar_banners()

            user_input = self._page.locator("#userInput")
            user_input.wait_for(state="visible", timeout=15000)
            user_input.click()
            user_input.fill(usuario)

            pwd_input = self._page.locator("#userPassword")
            pwd_input.wait_for(state="visible", timeout=5000)
            pwd_input.click()
            pwd_input.fill(password)

            self._page.wait_for_selector("button[aria-label='Ingresar']:not([disabled])", timeout=10000)
            self._page.locator("button[aria-label='Ingresar']").click()

            # Esperar a que redirija a /inicio O aparezca el modal de clave incorrecta
            try:
                self._page.wait_for_function(
                    """() => {
                        const ok = window.location.href.includes('/inicio');
                        const modal = document.querySelector('[data-automation-id="modalComponent"]');
                        const errModal = modal && modal.textContent.toLowerCase().includes('clave');
                        return ok || errModal;
                    }""",
                    timeout=30000,
                )
            except Exception:
                pass

            # Detectar modal "La clave tiene algún error"
            try:
                modal = self._page.locator('[data-automation-id="modalComponent"]').first
                if modal.is_visible(timeout=800):
                    txt = modal.inner_text(timeout=1000).lower()
                    if 'clave' in txt or 'error' in txt:
                        try:
                            self._page.locator(
                                '[data-automation-id="modalComponent"] button[aria-label="Aceptar"]'
                            ).first.click(timeout=2000)
                            time.sleep(0.4)
                        except Exception:
                            pass
                        return False, "La clave tiene algún error. Revisá la contraseña antes de volver a intentar."
            except Exception:
                pass

            self._cerrar_banners()

            from urllib.parse import urlparse
            path = urlparse(self._page.url).path.rstrip('/')
            if path in ('/login', '/login/step2') or path.startswith('/login/'):
                return False, "Login fallido. Verificá usuario y contraseña."

            self._logged_in = True
            self._empresa_activa = self._leer_empresa_activa()
            return True, "Login exitoso en Banco Galicia"

        except Exception as e:
            self._cleanup()
            return False, f"Error de conexión: {e}"

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
        """Llamado cuando el usuario cierra la ventana del browser Playwright.
        Playwright pasa el objeto page/browser como argumento — se acepta con *args."""
        self._logged_in = False
        # No tocar _page/_context acá: puede haber una consulta activa en el hilo de Playwright

    def _leer_empresa_activa(self) -> str:
        """Lee el nombre de la empresa activa del header del portal."""
        time.sleep(0.5)
        # Estrategia 1: el <p color="#666666"> es la empresa (el usuario tiene color="#333333")
        try:
            p = self._page.locator('.content-drop p[color="#666666"]').first
            txt = (p.text_content(timeout=3000) or '').strip()
            if txt and len(txt) > 2:
                return txt.title() if txt.isupper() else txt
        except Exception:
            pass
        # Estrategia 2: segundo <p aria-label="body"> dentro del header
        try:
            ps = self._page.locator('.content-drop p[aria-label="body"]').all()
            if len(ps) >= 2:
                txt = (ps[1].text_content(timeout=2000) or '').strip()
                if txt and len(txt) > 2:
                    return txt.title() if txt.isupper() else txt
        except Exception:
            pass
        # Estrategia 3 (fallback): primer p en mayúsculas de más de 3 chars
        try:
            for p in self._page.locator('.content-drop p').all():
                txt = (p.inner_text(timeout=1000) or '').strip()
                if txt and txt.isupper() and len(txt) > 3:
                    return txt.title()
        except Exception:
            pass
        return ''

    # ─────────────────────────────────────────────────────────────────────────
    # CAMBIAR EMPRESA
    # ─────────────────────────────────────────────────────────────────────────
    def cambiar_empresa(self, empresa: str) -> Tuple[bool, str]:
        if not self._logged_in or not self._page:
            return False, "No hay sesión activa"
        try:
            avatar = self._page.locator('.content-drop').first
            avatar.wait_for(state="visible", timeout=8000)
            avatar.click()
            time.sleep(0.6)

            item = self._page.locator(f'li[title="{empresa}"]').first
            item.wait_for(state="visible", timeout=5000)
            item.click()
            time.sleep(0.5)

            # Esperar modal de carga y que cierre — eso es suficiente señal
            try:
                modal = self._page.locator('[data-automation-id="modalComponent"]').first
                modal.wait_for(state="visible", timeout=4000)
                modal.wait_for(state="hidden", timeout=25000)
            except Exception:
                pass  # No apareció modal o ya cerró
            time.sleep(0.5)

            self._cerrar_banners()
            self._empresa_activa = empresa
            return True, f"Empresa cambiada a {empresa}"
        except Exception as e:
            return False, f"Error al cambiar empresa: {e}"

    # ─────────────────────────────────────────────────────────────────────────
    # RECAUDADORA / COBRANZA INFORMADA
    # ─────────────────────────────────────────────────────────────────────────
    def obtener_recaudadora(self, fecha_desde: str, fecha_hasta: str) -> Tuple[List[Dict], str]:
        if not self._logged_in or not self._page:
            return [], "No hay sesión activa"
        try:
            self._goto_con_retry(COBROS_URL)
            try:
                self._page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            time.sleep(1.5)
            self._cerrar_banners()

            if "login" in self._page.url.lower():
                self._logged_in = False
                return [], "La sesión expiró. Iniciá sesión nuevamente."

            try:
                self._page.wait_for_selector(
                    'input[placeholder="Desde"], table, [class*="table"]',
                    timeout=15000,
                )
            except Exception:
                pass
            time.sleep(0.5)

            self._filtrar_fecha_recaudadora(fecha_desde, fecha_hasta)
            time.sleep(1)
            return self._descargar_csv_recaudadora()

        except Exception as e:
            return [], str(e)

    def _filtrar_fecha_recaudadora(self, fecha_desde: str, fecha_hasta: str):
        if not fecha_desde and not fecha_hasta:
            return
        try:
            date_input = self._page.locator('input[placeholder="Desde"]').first
            date_input.wait_for(state="visible", timeout=8000)
            date_input.click()
            time.sleep(0.6)

            if fecha_desde:
                self._click_dia_datepicker(fecha_desde)
                time.sleep(0.4)
            if fecha_hasta:
                self._click_dia_datepicker(fecha_hasta)
                time.sleep(0.4)

            try:
                self._page.keyboard.press("Escape")
            except Exception:
                pass
            time.sleep(0.3)

            for texto in ["Buscar", "Aplicar", "Filtrar", "Consultar"]:
                try:
                    btn = self._page.locator(f'button:has-text("{texto}")').first
                    if btn.is_visible(timeout=800):
                        btn.click()
                        try:
                            self._page.wait_for_load_state("networkidle", timeout=10000)
                        except Exception:
                            pass
                        time.sleep(1)
                        break
                except Exception:
                    continue
        except Exception:
            pass

    def _descargar_csv_recaudadora(self) -> Tuple[List[Dict], str]:
        SIN_DATOS = "0 cobros para el período seleccionado"
        try:
            dl_btn = self._page.locator(
                'button[aria-haspopup="true"][class*="button--icon-only"]'
            ).first
            dl_btn.wait_for(state="visible", timeout=8000)
        except Exception:
            return [], SIN_DATOS

        # Botón deshabilitado = no hay resultados para descargar
        try:
            if dl_btn.is_disabled():
                return [], SIN_DATOS
        except Exception:
            pass

        dl_btn.click()
        time.sleep(0.4)

        try:
            csv_item = self._page.locator('[aria-label=".CSV"]').first
            csv_item.wait_for(state="visible", timeout=3000)
        except Exception:
            return [], SIN_DATOS

        try:
            with self._page.expect_download(timeout=30000) as dl_info:
                csv_item.click()
            dl  = dl_info.value
            tmp = os.path.join(tempfile.gettempdir(), 'galicia_recaudadora_latest.csv')
            dl.save_as(tmp)
            time.sleep(0.5)
            cobros, msg = procesar_csv_recaudadora_galicia(tmp)
            return (cobros, msg) if cobros else ([], SIN_DATOS)
        except Exception as e:
            return [], f"Error al descargar el CSV: {e}"

    # ─────────────────────────────────────────────────────────────────────────
    # CHEQUES A ACEPTAR — helpers compartidos
    # ─────────────────────────────────────────────────────────────────────────
    def _filtrar_fecha_cheques(self, fecha_desde: str, fecha_hasta: str):
        if not fecha_desde and not fecha_hasta:
            return
        try:
            filtros = self._page.locator('button[aria-label="Filter2"]').first
            filtros.wait_for(state="visible", timeout=8000)
            filtros.click()
            time.sleep(0.8)

            drawer = self._page.locator(
                '[data-automation-id="sideDrawerComponent"]:has(label[aria-label="Fecha de emisión"])'
            ).first
            em_input = drawer.locator(
                'xpath=.//label[@aria-label="Fecha de emisión"]'
                '/../..//input[@placeholder="Desde - Hasta"]'
            ).first
            em_input.wait_for(state="visible", timeout=6000)
            em_input.click()
            time.sleep(0.6)

            if fecha_desde:
                self._click_dia_datepicker(fecha_desde)
                time.sleep(0.4)
            if fecha_hasta:
                self._click_dia_datepicker(fecha_hasta)
                time.sleep(0.4)

            try:
                self._page.keyboard.press("Escape")
            except Exception:
                pass
            time.sleep(0.4)

            aplicar = self._page.locator('button[aria-label="Aplicar"]').first
            for _ in range(10):
                try:
                    if aplicar.is_visible(timeout=300) and aplicar.is_enabled(timeout=300):
                        break
                except Exception:
                    pass
                time.sleep(0.4)
            try:
                aplicar.click(timeout=3000)
                try:
                    self._page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                time.sleep(2)
            except Exception:
                pass
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # CHEQUES A ACEPTAR
    # ─────────────────────────────────────────────────────────────────────────
    def obtener_cheques_a_aceptar(self, fecha_desde: str, fecha_hasta: str) -> Tuple[List[Dict], str]:
        if not self._logged_in or not self._page:
            return [], "No hay sesión activa"
        try:
            self._goto_con_retry(CHEQUES_URL, timeout=30000)
            try:
                self._page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            time.sleep(0.8)
            self._cerrar_banners()

            if "login" in self._page.url.lower():
                self._logged_in = False
                return [], "La sesión expiró. Iniciá sesión nuevamente."

            try:
                aceptar_card = self._page.locator('[data-component="Card"]:has(h3:has-text("Aceptar"))').first
                aceptar_card.wait_for(state="visible", timeout=10000)
                aceptar_card.click()
                try:
                    self._page.wait_for_url("**/aceptar**", timeout=10000)
                except Exception:
                    pass
                try:
                    self._page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                time.sleep(1.5)
            except Exception as e:
                return [], f"No se encontró la card Aceptar: {e}"

            # El portal puede mostrar un modal de bienvenida/tour durante la transición
            self._cerrar_banners()
            time.sleep(0.5)
            self._cerrar_banners()  # segundo intento por si el modal tardó en aparecer

            if "login" in self._page.url.lower():
                self._logged_in = False
                return [], "La sesión expiró. Iniciá sesión nuevamente."

            try:
                self._page.wait_for_selector(
                    'input[placeholder="Desde - Hasta"], table, [class*="table"]',
                    timeout=15000,
                )
            except Exception:
                pass
            time.sleep(0.5)

            self._filtrar_fecha_cheques(fecha_desde, fecha_hasta)
            time.sleep(1)
            return self._descargar_csv_cheques_a_aceptar()
        except Exception as e:
            return [], str(e)

    def _descargar_csv_cheques_a_aceptar(self) -> Tuple[List[Dict], str]:
        SIN_DATOS = "0 cheques a aceptar para el período seleccionado"
        try:
            dl_btn = self._page.locator(
                'button[aria-haspopup="true"][title="Descargar"],'
                'button[aria-haspopup="true"][aria-label="Menuitem"]'
            ).first
            dl_btn.wait_for(state="visible", timeout=8000)
        except Exception:
            return [], SIN_DATOS

        # Botón deshabilitado = no hay cheques para descargar
        try:
            if dl_btn.is_disabled():
                return [], SIN_DATOS
        except Exception:
            pass

        dl_btn.click()
        time.sleep(0.4)

        try:
            csv_item = self._page.locator('[aria-label="Detalle de cheques en .CSV"]').first
            csv_item.wait_for(state="visible", timeout=8000)
        except Exception:
            return [], SIN_DATOS

        try:
            tmp = os.path.join(tempfile.gettempdir(), 'galicia_cheques_aceptar_latest.csv')
            with self._page.expect_download(timeout=30000) as dl_info:
                csv_item.click()
                time.sleep(0.8)
                try:
                    modal = self._page.locator('[data-automation-id="modalComponent"]').first
                    modal.wait_for(state="visible", timeout=8000)
                    try:
                        self._page.locator('label:has-text("Todas las páginas")').first.click()
                        time.sleep(0.5)
                    except Exception:
                        pass
                    continuar = self._page.locator('button[aria-label="Continuar"]').first
                    if continuar.is_visible(timeout=5000):
                        continuar.click()
                except Exception:
                    pass
            dl = dl_info.value
            dl.save_as(tmp)
            time.sleep(0.5)
            cheques, msg = procesar_csv_cheques_galicia(tmp)
            return (cheques, msg) if cheques else ([], SIN_DATOS)
        except Exception as e:
            return [], f"Error al descargar: {e}"

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _limitar_90_dias(fecha_desde: str) -> str:
        limite = (date.today() - timedelta(days=89)).isoformat()
        if not fecha_desde or fecha_desde < limite:
            return limite
        return fecha_desde

    def _goto_con_retry(self, url: str, timeout: int = 60000, reintentos: int = 2):
        """Navega a la URL con reintentos automáticos en caso de timeout."""
        ultimo_error = None
        for intento in range(reintentos):
            try:
                self._page.goto(url, timeout=timeout)
                self._page.wait_for_load_state("domcontentloaded", timeout=30000)
                return
            except Exception as e:
                ultimo_error = e
                if intento < reintentos - 1 and 'timeout' in str(e).lower():
                    time.sleep(3)
                    continue
                break
        raise ultimo_error

    def _cerrar_banners(self):
        selectores = [
            '[data-testid="close-button"]',
            'button[aria-label="cerrar"]', 'button[aria-label="Cerrar"]',
            'button[aria-label="close"]',  'button[aria-label="Close"]',
            'button:has-text("×")',         'button:has-text("✕")',
            '[class*="banner"] button[class*="close"]',
            '[class*="modal"] button[class*="close"]',
            '[class*="dismiss"]:visible',
        ]
        for sel in selectores:
            try:
                btn = self._page.locator(sel).first
                if btn.is_visible(timeout=300):
                    btn.click()
                    time.sleep(0.2)
            except Exception:
                pass
        # Cerrar modales con botón de acción (Continuar, Entendido, Aceptar)
        try:
            modal = self._page.locator('[data-automation-id="modalComponent"]').first
            if modal.is_visible(timeout=400):
                for lbl in ['Recorrer más tarde', 'Continuar', 'Entendido', 'Aceptar']:
                    try:
                        btn = modal.locator(f'button:has-text("{lbl}")').first
                        if btn.is_visible(timeout=200):
                            btn.click()
                            time.sleep(0.3)
                            break
                    except Exception:
                        pass
        except Exception:
            pass

    def _click_dia_datepicker(self, fecha_iso: str):
        """Navega el react-datepicker al mes correcto y hace click en el día."""
        MESES_ABR = {
            "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
            "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
            "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
        }
        MESES_ES = {v: k for k, v in MESES_ABR.items()}
        target = dt.strptime(fecha_iso, "%Y-%m-%d")

        for _ in range(24):
            try:
                header = self._page.locator('.MonthLabel').first.inner_text(timeout=2000).strip()
                partes = header.split()
                mes_actual  = MESES_ABR.get(partes[0].lower())
                anio_actual = int(partes[1]) if len(partes) > 1 else 0
                if mes_actual == target.month and anio_actual == target.year:
                    break
                cur = anio_actual * 12 + (mes_actual or 0)
                tgt = target.year * 12 + target.month
                # Typo intencional del portal: "arrowfoward"
                btn = 'button[aria-label="arrowfoward"]' if cur < tgt else 'button[aria-label="arrowback"]'
                self._page.locator(btn).first.click()
                time.sleep(0.3)
            except Exception:
                break

        aria_fragment = f"{target.day} de {MESES_ES[target.month]} de {target.year}"
        try:
            day_el = self._page.locator(
                f'.react-datepicker__day[aria-label*="{aria_fragment}"]'
                ':not(.react-datepicker__day--outside-month)'
            ).first
            day_el.wait_for(state="visible", timeout=2000)
            day_el.click()
            return
        except Exception:
            pass

        # Fallback: por clase CSS
        day_class = f"react-datepicker__day--{target.day:03d}"
        try:
            for cell in self._page.locator(
                f'.{day_class}:not(.react-datepicker__day--outside-month)'
            ).all():
                if cell.get_attribute("aria-disabled") != "true" and cell.is_visible(timeout=500):
                    cell.click()
                    return
        except Exception:
            pass
