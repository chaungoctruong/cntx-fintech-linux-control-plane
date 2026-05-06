FROM python:3.10-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc python3-dev && rm -rf /var/lib/apt/lists/*
# Copy requirements từ đúng thư mục backend hoặc hubbot tùy cấu trúc của bạn
COPY hubbot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir uvicorn python-multipart psycopg2-binary google-genai
COPY . .
ENV PYTHONPATH=/app
CMD ["python", "hubbot/main.py"]
