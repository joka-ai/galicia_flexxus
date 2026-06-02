@echo off
chcp 65001 >nul
title Galicia - Flexxus ECHEQs

echo.
echo  ============================================
echo    Galicia - Flexxus  ECHEQs
echo  ============================================
echo.

cd /d "%~dp0"

:: Verificar Python
echo  [1/5] Verificando Python...
py --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] No se encontro Python.
    echo  Descargalo de https://www.python.org/downloads/
    start https://www.python.org/downloads/
    pause
    exit /b 1
)
py --version
echo  [OK] Python encontrado
echo.

:: Crear entorno virtual
echo  [2/5] Preparando entorno...
if not exist "venv\" (
    py -m venv venv
    echo  [OK] Entorno creado
) else (
    echo  [OK] Entorno ya existe
)
echo.

:: Activar entorno (a partir de aqui usar "python", no "py")
call venv\Scripts\activate.bat

:: Instalar librerias en el entorno virtual
echo  [3/5] Instalando librerias (solo la primera vez, puede tardar 2-3 min)...
python -m pip install --upgrade pip --quiet
python -m pip install flask flask-cors requests pandas openpyxl xlrd playwright bcrypt pdfplumber --quiet
echo  [OK] Librerias listas
echo.

:: Chromium para Playwright
echo  [4/5] Verificando Chromium...
python -m playwright install chromium >nul 2>&1
echo  [OK] Chromium listo
echo.

:: Lanzar servidor
echo  [5/5] Iniciando servidor...
echo.
echo  ============================================
echo   Abriendo http://localhost:5000
echo   NO CIERRES ESTA VENTANA mientras lo uses!
echo  ============================================
echo.

timeout /t 2 /nobreak >nul
start "" "http://localhost:5000"

cd backend
python app.py

echo.
echo  Servidor cerrado. Presiona cualquier tecla.
pause
