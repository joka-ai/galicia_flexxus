@echo off
chcp 65001 >nul
title Galicia - Consultas Home Banking

:: Pedir permisos de administrador si no los tiene
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Solicitando permisos de administrador...
    powershell -Command "Start-Process cmd -ArgumentList '/c cd /d ""%~dp0"" && ""%~f0""' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

echo.
echo  ============================================
echo    Galicia - Consultas Home Banking
echo  ============================================
echo.

:: ── 1. Verificar Python ──────────────────────────────────────────────────────
echo  [1/5] Verificando Python...
py --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: Python no esta instalado.
    echo  Descargalo de https://www.python.org/downloads/
    echo  IMPORTANTE: tilda "Add Python to PATH" al instalar.
    echo.
    start https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('py --version') do echo  OK: %%v
echo.

:: ── 2. Entorno virtual ───────────────────────────────────────────────────────
echo  [2/5] Preparando entorno virtual...
if not exist "venv\" (
    py -m venv venv
    if %errorlevel% neq 0 (
        echo  ERROR: No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
    echo  OK: Entorno creado
) else (
    echo  OK: Entorno ya existe
)
echo.

:: ── 3. Instalar librerias ────────────────────────────────────────────────────
echo  [3/5] Instalando librerias (puede tardar 2-3 min la primera vez)...
call "%~dp0venv\Scripts\activate.bat"
python -m pip install --upgrade pip -q
python -m pip install -r "%~dp0requirements.txt"
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: Fallo la instalacion de librerias.
    echo  Revisa tu conexion a internet e intentalo de nuevo.
    pause
    exit /b 1
)
echo.
echo  OK: Librerias instaladas
echo.

:: ── 4. Chromium ──────────────────────────────────────────────────────────────
echo  [4/5] Instalando Chromium (solo la primera vez)...
python -m playwright install chromium
if %errorlevel% neq 0 (
    echo  ADVERTENCIA: No se pudo instalar Chromium.
    echo  Ejecuta manualmente: python -m playwright install chromium
) else (
    echo  OK: Chromium listo
)
echo.

:: ── 5. Iniciar app ───────────────────────────────────────────────────────────
echo  [5/5] Iniciando la aplicacion...
echo.
echo  ============================================
echo   Usuario por defecto  : ADMIN
echo   Contrasena por defecto: admin123
echo.
echo   El browser se abre solo.
echo   NO CIERRES ESTA VENTANA mientras uses la app.
echo   Para cerrar: presiona Ctrl+C en esta ventana.
echo  ============================================
echo.

cd /d "%~dp0backend"
python app.py

echo.
echo  La aplicacion se cerro.
echo  Si algo fallo, revisa el archivo: logs\debug.log
echo.
pause
