#!/usr/bin/env bash
set -e

pip install -r requirements.txt

# Instalar solo el browser (sin dependencias del sistema — Render ya las tiene)
export PLAYWRIGHT_BROWSERS_PATH=/opt/render/project/src/.playwright-browsers
playwright install chromium
