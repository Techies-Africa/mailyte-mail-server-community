FROM python:3.11-slim

WORKDIR /app

COPY worker/autoconfig/requirements.txt .
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r requirements.txt

COPY worker/autoconfig/ .
COPY shared ./shared
COPY database ./database

RUN groupadd -r mailyte && useradd -r -g mailyte -u 10001 mailyte \
 && chown -R mailyte:mailyte /app
USER mailyte

EXPOSE 8100

CMD ["python", "app.py"]
