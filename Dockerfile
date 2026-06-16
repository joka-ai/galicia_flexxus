FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema para Playwright/Chromium
RUN apt-get update && apt-get install -y \
    wget gnupg ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instalar Chromium y sus dependencias de sistema
RUN playwright install chromium
RUN playwright install-deps chromium

# Copiar archivos de la app
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY img/ ./img/
COPY sucursales/ ./sucursales/

# Carpetas necesarias en runtime
RUN mkdir -p logs uploads

ENV HEADLESS=true
ENV SERVER_MODE=true
ENV PORT=8080

WORKDIR /app/backend
EXPOSE 8080

CMD ["python", "app.py"]
