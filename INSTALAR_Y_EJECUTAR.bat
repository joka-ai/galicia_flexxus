@echo off
chcp 65001 >nul
title Galicia - Consultas Home Banking

echo.
echo  ============================================
echo    Galicia - Consultas Home Banking
echo  ============================================
echo.

cd /d "%~dp0"

:: ── 1. Verificar Python ───────────────────────────────────────────────────────
echo  [1/5] Verificando Python...
py --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] Python no esta instalado.
    echo.
    echo  Descargandolo de python.org...
    echo  IMPORTANTE: durante la instalacion tilda
    echo  "Add Python to PATH" antes de continuar.
    echo.
    start https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('py --version') do echo  [OK] %%v
echo.

:: ── 2. Entorno virtual ───────────────────────────────────────────────────────
echo  [2/5] Preparando entorno virtual...
if not exist "venv\" (
    py -m venv venv
    echo  [OK] Entorno creado
) else (
    echo  [OK] Entorno ya existe
)
echo.

call venv\Scripts\activate.bat

:: ── 3. Instalar librerias ────────────────────────────────────────────────────
echo  [3/5] Instalando librerias (solo la primera vez, puede tardar 2-3 min)...
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo  [ERROR] Fallo la instalacion de librerias.
    echo  Revisa tu conexion a internet e intentalo de nuevo.
    pause
    exit /b 1
)
echo  [OK] Librerias listas
echo.

:: ── 4. Chromium para Playwright ──────────────────────────────────────────────
echo  [4/5] Instalando Chromium (solo la primera vez)...
python -m playwright install chromium >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ADVERTENCIA] No se pudo instalar Chromium automaticamente.
    echo  Ejecuta manualmente: python -m playwright install chromium
) else (
    echo  [OK] Chromium listo
)
echo.

:: ── 5. Lanzar la app ─────────────────────────────────────────────────────────
echo  [5/5] Iniciando la aplicacion...
echo.
echo  ============================================
echo   Usuario por defecto : ADMIN
echo   Contrasena por defecto : admin123
echo.
echo   El browser se abre automaticamente.
echo   NO CIERRES ESTA VENTANA mientras uses la app.
echo   Para cerrar la app, presiona Ctrl+C aqui.
echo  ============================================
echo.

cd backend
python app.py

echo.
echo  ============================================
echo   La aplicacion se cerro.
echo   Si algo fallo, manda el archivo:
echo   logs\debug.log
echo  ============================================
echo.
pause
