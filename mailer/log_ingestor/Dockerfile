FROM python:3.11-slim

WORKDIR /app

COPY mailer/log_ingestor/requirements.txt .
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r requirements.txt

# shared/ ships its own package list. Installing it separately is what keeps a
# service from crash-looping when shared/ gains a dependency the service's own
# list never heard about -- see the note in shared/requirements.txt.
COPY shared/requirements.txt ./shared-requirements.txt
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r shared-requirements.txt

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
