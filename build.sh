#!/usr/bin/env bash
# Script de build para Render (Linux)
set -e

pip install -r requirements.txt

# Instalar dependencias del sistema de Chromium (build tiene acceso root)
playwright install-deps chromium

# Instalar el browser en una ruta dentro del proyecto (persiste entre builds)
export PLAYWRIGHT_BROWSERS_PATH=/opt/render/project/src/.playwright-browsers
playwright install chromium
