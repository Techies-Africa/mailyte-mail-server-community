FROM python:3.11-slim

WORKDIR /app

COPY worker/autoconfig/requirements.txt .
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r requirements.txt

COPY worker/autoconfig/ .
COPY shared ./shared
COPY database ./database

EXPOSE 8100

CMD ["python", "app.py"]
