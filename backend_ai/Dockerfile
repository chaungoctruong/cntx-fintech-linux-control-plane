FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend_ai/backend:/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend_ai/backend/requirements.txt /tmp/backend-requirements.txt
RUN pip install --no-cache-dir -r /tmp/backend-requirements.txt

COPY backend_ai/backend /app/backend_ai/backend
COPY runner /app/runner
COPY bot-trading /app/bot-trading
COPY ops_telegram_alerts.py /app/ops_telegram_alerts.py

WORKDIR /app/backend_ai/backend

EXPOSE 8001

CMD ["python", "scripts/run_api.py"]
