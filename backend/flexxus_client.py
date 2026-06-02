"""
FlexxusClient — cliente para la API REST de Flexxus v5
Endpoints verificados desde: https://apiapp.flexxus.com.ar/v5/docs/
"""

import requests
import logging
from datetime import date
from typing import Tuple, List, Dict, Optional

logger = logging.getLogger(__name__)


class FlexxusClient:
    def __init__(self, url_base: str = "https://apiapp.flexxus.com.ar/v5"):
        self.url_base = url_base.rstrip('/')
        self._token: Optional[str] = None
        self._user_info: Dict = {}
        self._session = requests.Session()
        self._session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        })

    # ── POST /auth/login ──────────────────────────────────────────────────────
    def login(self, usuario: str, password: str, empresa_id: str = "") -> Tuple[bool, str, str]:
        """
        Retorna (ok, token, mensaje)
        Documentación: POST /auth/login
        Body: { username, password, deviceinfo{...} }
        Response 200: { token, expireIn, refreshToken, user{...}, ... }
        """
        payload = {
            "username": usuario,
            "password": password,
            "deviceinfo": {
                "model": "PC",
                "platform": "Windows",
                "uuid": "galicia-flexxus-integration",
                "version": "1.0",
                "manufacturer": "Custom"
            }
        }

        try:
            resp = self._session.post(
                f"{self.url_base}/auth/login",
                json=payload,
                timeout=15
            )
            if resp.status_code == 200:
                data = resp.json()
                token = data.get('token', '')
                if token:
                    self._token = token
                    self._user_info = data.get('user', {})
                    self._session.headers['Authorization'] = f'Bearer {token}'
                    nombre = self._user_info.get('full_name', usuario)
                    empresa = self._user_info.get('company', '')
                    logger.info(f"Flexxus login OK — {nombre} ({empresa})")
                    return True, token, f"Conectado como {nombre} — {empresa}"
                else:
                    msg = data.get('message', 'No se recibió token')
                    return False, "", msg
            elif resp.status_code == 500:
                try:
                    msg = resp.json().get('message', 'Parámetros inválidos')
                except Exception:
                    msg = 'Error interno del servidor'
                return False, "", f"Error Flexxus: {msg}"
            else:
                return False, "", f"Error HTTP {resp.status_code}"
        except requests.RequestException as e:
            return False, "", f"Error de conexión: {str(e)}"

    def set_token(self, token: str):
        self._token = token
        self._session.headers['Authorization'] = f'Bearer {token}'

    # ── GET /cuentasbancos ────────────────────────────────────────────────────
    def obtener_cuentas_bancarias(self, empresa_id: str = "") -> List[Dict]:
        """
        Documentación: GET /cuentasbancos
        Retorna listado de cuentas de bancos.
        """
        try:
            resp = self._session.get(f"{self.url_base}/cuentasbancos", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                return data.get('data', data.get('cuentas', []))
        except Exception as e:
            logger.warning(f"Error obteniendo cuentas: {e}")
        return []

    # ── GET /bancos ───────────────────────────────────────────────────────────
    def obtener_bancos(self) -> List[Dict]:
        """Documentación: GET /bancos"""
        try:
            resp = self._session.get(f"{self.url_base}/bancos", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get('data', [])
        except Exception as e:
            logger.warning(f"Error obteniendo bancos: {e}")
        return []

    # ── POST /fondos/cajas/ingreso — cargar cheque ────────────────────────────
    def cargar_cheque(
        self,
        cheque: Dict,
        tipo_comprobante: str,
        condicion_pago: str,
        empresa_id: str,
        cuenta_id: str,
    ) -> Tuple[bool, str, str]:
        """
        Carga un ECHEQ en Flexxus como ingreso de fondos.

        Estrategia según la API documentada:
        1. POST /fondos/cajas/ingreso  — ingreso de caja con cheque
        2. POST /ventas/pagos          — pago en cuenta corriente (fallback)

        Documentación:
          POST /fondos/cajas/ingreso → Creación de un Ingreso Caja
          POST /ventas/pagos         → Creación de pago en cuenta corriente
        """
        fecha_pago     = self._normalizar_fecha(cheque.get('fecha_pago', ''))
        fecha_emision  = self._normalizar_fecha(cheque.get('fecha_emision', ''))
        cuit           = cheque.get('cuit', '').replace('-', '').replace(' ', '')
        numero_cheque  = str(cheque.get('id', cheque.get('numero_cheque', '')))
        importe        = cheque.get('importe', 0)
        banco          = cheque.get('banco', '')
        librador       = cheque.get('librador', '')

        # ── Payload 1: ingreso de caja ────────────────────────────────────
        payload_ingreso = {
            "codigocaja": cuenta_id,
            "importe": importe,
            "concepto": f"ECHEQ Galicia Nº {numero_cheque} - {librador}",
            "fecha": fecha_emision or self._hoy(),
            "formapago": "cheque",
            "cheque": {
                "numero": numero_cheque,
                "banco": banco,
                "cuit": cuit,
                "razonsocial": librador,
                "fechaemision": fecha_emision,
                "fechacobro": fecha_pago,
                "importe": importe,
                "diferido": condicion_pago == "2",
                "electronico": True,
                "cmc7": cheque.get('cmc7', ''),
            }
        }

        # ── Payload 2: pago cuenta corriente (fallback) ───────────────────
        payload_pago = {
            "tipocomprobante": tipo_comprobante,
            "fecha": fecha_emision or self._hoy(),
            "importe": importe,
            "observaciones": f"ECHEQ Galicia Nº {numero_cheque} - {librador}",
            "formaspago": [
                {
                    "tipo": "cheque",
                    "importe": importe,
                    "numero": numero_cheque,
                    "banco": banco,
                    "cuit": cuit,
                    "razonsocial": librador,
                    "fechaemision": fecha_emision,
                    "fechacobro": fecha_pago,
                    "diferido": condicion_pago == "2",
                    "electronico": True,
                }
            ]
        }

        intentos = [
            (f"{self.url_base}/fondos/cajas/ingreso", payload_ingreso),
            (f"{self.url_base}/ventas/pagos",         payload_pago),
        ]

        ultimo_error = ""
        for endpoint, body in intentos:
            try:
                resp = self._session.post(endpoint, json=body, timeout=15)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    rec_id = str(
                        data.get('id') or
                        data.get('numero') or
                        data.get('numerocomprobante') or
                        data.get('message', 'OK')
                    )
                    logger.info(f"Cheque {numero_cheque} cargado OK — ID Flexxus: {rec_id}")
                    return True, rec_id, f"Cargado en Flexxus (ID: {rec_id})"

                elif resp.status_code == 500:
                    try:
                        msg = resp.json().get('message', resp.text[:150])
                    except Exception:
                        msg = resp.text[:150]
                    ultimo_error = f"Error Flexxus: {msg}"
                    logger.warning(f"Endpoint {endpoint} → 500: {msg}")
                    continue

                elif resp.status_code == 401:
                    return False, "", "Token expirado. Volvé a conectarte a Flexxus."

                else:
                    ultimo_error = f"HTTP {resp.status_code}: {resp.text[:100]}"
                    continue

            except requests.RequestException as e:
                ultimo_error = str(e)
                continue

        return False, "", f"No se pudo cargar: {ultimo_error}"

    # ── GET /ventas/cheques/{codigocliente} ───────────────────────────────────
    def obtener_cheques_cliente(self, codigo_cliente: str) -> List[Dict]:
        """Documentación: GET /ventas/cheques/{codigocliente}"""
        try:
            resp = self._session.get(
                f"{self.url_base}/ventas/cheques/{codigo_cliente}",
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get('data', [])
        except Exception as e:
            logger.warning(f"Error obteniendo cheques cliente: {e}")
        return []

    # ── GET /fondos/movimientosbancarios ──────────────────────────────────────
    def obtener_movimientos_bancarios(self) -> List[Dict]:
        """Documentación: GET /fondos/movimientosbancarios"""
        try:
            resp = self._session.get(
                f"{self.url_base}/fondos/movimientosbancarios",
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get('data', [])
        except Exception as e:
            logger.warning(f"Error obteniendo movimientos: {e}")
        return []

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _normalizar_fecha(fecha: str) -> str:
        """Convierte dd/mm/yyyy → yyyy-mm-dd para la API."""
        if not fecha:
            return ""
        s = str(fecha).strip()
        if '/' in s:
            partes = s.split('/')
            if len(partes) == 3:
                d, m, a = partes
                return f"{a}-{m.zfill(2)}-{d.zfill(2)}"
        return s

    @staticmethod
    def _hoy() -> str:
        return date.today().isoformat()
