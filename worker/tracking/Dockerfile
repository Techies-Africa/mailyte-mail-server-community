FROM python:3.9-slim

WORKDIR /app

COPY worker/tracking/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY worker/tracking/ .
COPY shared ./shared
COPY database ./database

EXPOSE 8086

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8086"]