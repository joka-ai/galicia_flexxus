FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    wget gnupg ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install chromium
RUN playwright install-deps chromium

COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY img/ ./img/
COPY sucursales/ ./sucursales/

RUN mkdir -p logs uploads

ENV HEADLESS=true
ENV SERVER_MODE=true
ENV PORT=5000

WORKDIR /app/backend
EXPOSE 5000

CMD ["python", "app.py"]
