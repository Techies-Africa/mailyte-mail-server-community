FROM python:3.11-slim

WORKDIR /app

COPY worker/tracking/requirements.txt .
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r requirements.txt

COPY worker/tracking/ .
COPY shared ./shared
COPY database ./database

# H1 (00-foundation/07-security-model.md): non-root runtime user.
RUN groupadd -r mailyte && useradd -r -g mailyte -u 10001 mailyte \
 && chown -R mailyte:mailyte /app
USER mailyte

EXPOSE 8086

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8086"]
