FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt /tmp/token-bot-requirements.txt
RUN pip install --no-cache-dir -r /tmp/token-bot-requirements.txt

COPY app /app/app
COPY scripts /app/scripts

RUN mkdir -p /app/var/encrypted

EXPOSE 8090

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
