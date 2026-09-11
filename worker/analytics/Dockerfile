# 3.11-slim to match every other Python service (and pyproject's
# requires-python >= 3.11); the pinned scientific deps (numpy 1.25.2,
# pandas 2.1.3, scipy 1.11.4) all ship cp311 wheels. This was the last
# 3.9 straggler besides Dockerfile.migrate.
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    gfortran \
    libopenblas-dev \
    && rm -rf /var/lib/apt/lists/*

COPY worker/analytics/requirements.txt .
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r requirements.txt

COPY worker/analytics/ .
COPY shared ./shared
COPY database ./database

RUN groupadd -r mailyte && useradd -r -g mailyte -u 10001 mailyte \
 && chown -R mailyte:mailyte /app
USER mailyte

EXPOSE 8085

CMD ["python", "app.py"]