FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY worker/rate_limiter/requirements.txt .
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r requirements.txt

# Copy application files
COPY worker/rate_limiter/app.py .
COPY worker/rate_limiter/config.py .
COPY worker/rate_limiter/services ./services
COPY shared ./shared
COPY database ./database

EXPOSE 8082

CMD ["python", "app.py"]