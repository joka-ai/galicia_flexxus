@echo off
cd /d "%~dp0"
title Galicia - Consultas Home Banking

echo.
echo  ============================================
echo    Galicia - Consultas Home Banking
echo  ============================================
echo.

:: ── 1. Verificar Python ──────────────────────────────────────────────────────
echo  [1/5] Verificando Python...
py --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python no esta instalado.
    echo  Descargalo de: https://www.python.org/downloads/
    echo  Al instalar, tildar la opcion Add Python to PATH
    start https://www.python.org/downloads/
    pause
    exit /b 1
)
echo  OK: Python encontrado
echo.

:: ── 2. Entorno virtual ───────────────────────────────────────────────────────
echo  [2/5] Preparando entorno virtual...
if not exist "%~dp0venv\Scripts\python.exe" (
    py -m venv "%~dp0venv"
    echo  OK: Entorno creado
) else (
    echo  OK: Entorno ya existe
)
echo.

:: ── 3. Instalar librerias ────────────────────────────────────────────────────
echo  [3/5] Instalando librerias (puede tardar 2-3 min la primera vez)...
"%~dp0venv\Scripts\python.exe" -m pip install --upgrade pip -q
"%~dp0venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
echo  OK: Librerias instaladas
echo.

:: ── 4. Chromium ──────────────────────────────────────────────────────────────
echo  [4/5] Instalando Chromium...
"%~dp0venv\Scripts\python.exe" -m playwright install chromium
echo  OK: Chromium listo
echo.

:: ── 5. Iniciar app ───────────────────────────────────────────────────────────
echo  [5/5] Iniciando la aplicacion...
echo.
echo  ============================================
echo   Usuario por defecto  : ADMIN
echo   Contrasena por defecto: admin123
echo.
echo   El browser se abre solo.
echo   NO CIERRES ESTA VENTANA mientras usas la app.
echo   Para cerrar: presiona Ctrl+C en esta ventana.
echo  ============================================
echo.

"%~dp0venv\Scripts\python.exe" "%~dp0backend\app.py"

echo.
echo  La aplicacion se cerro.
echo  Si algo fallo, revisa: %~dp0logs\debug.log
echo.
pause
