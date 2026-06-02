#!/usr/bin/env bash
# Script de build para Render (Linux)
set -e

pip install -r requirements.txt
playwright install chromium --with-deps
