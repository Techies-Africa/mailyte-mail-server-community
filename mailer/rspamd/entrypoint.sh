#!/bin/bash
set -e

echo "=== Rspamd Entrypoint ==="

# /var/log/rspamd and /var/lib/rspamd are bind-mounted from the host
# (./logs/mailer/rspamd, ./storage/dkim_keys via /var/lib/rspamd/dkim) --
# the Dockerfile's own `chown -R _rspamd:_rspamd` only affects the image's
# baked-in filesystem at build time, which the bind mount then shadows at
# run time with whatever ownership the host directory actually has
# (root:root, if Docker auto-created it). rspamd itself runs as the
# unprivileged _rspamd user (supervisord's `-u _rspamd -g _rspamd`) and
# cannot chown its own log file into existence -- confirmed live:
# "cannot chown desired log file: /var/log/rspamd/rspamd.log, Operation
# not permitted", exiting 1 in a crash loop. Same fix as dovecot's own
# entrypoint.sh (chown -R vmail:vmail /var/mail/vhosts) -- self-correct
# ownership at container startup, while still root, rather than depending
# on the host side getting it right first.
chown -R _rspamd:_rspamd /var/log/rspamd /var/lib/rspamd 2>/dev/null || true

echo "=== Rspamd configuration complete ==="

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
