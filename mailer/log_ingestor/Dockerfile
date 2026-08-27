FROM python:3.11-slim

WORKDIR /app

COPY mailer/log_ingestor/requirements.txt .
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r requirements.txt

# CE's shared/ carries no requirements.txt of its own -- its imports here
# (webhook_dispatcher: requests + mysql-connector) are already covered by
# this service's list above.
COPY mailer/log_ingestor/ .
COPY shared/ ./shared/

# State lives on a named volume so the read offset survives a recreate; without
# it a restart would re-read the whole log. That is not a correctness problem
# (rows are keyed deterministically and inserted with INSERT IGNORE) but it is
# a pointless burst of work on every deploy.
RUN groupadd -r mailyte && useradd -r -g mailyte -u 10001 mailyte \
 && mkdir -p /var/lib/log_ingestor \
 && chown -R mailyte:mailyte /app /var/lib/log_ingestor
USER mailyte

CMD ["python", "app.py"]
