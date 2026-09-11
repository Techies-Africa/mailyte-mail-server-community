#!/usr/bin/env bash
# Generates the Key Encryption Key (KEK) that protects DKIM/PGP/S-MIME
# private keys at rest (phase-07 C2). Run once per environment, before the
# api or migrate services first need to encrypt or rotate a key.
#
# The KEK lives as a mounted file, never an env var and never a DB row
# (H6) -- docker-compose.yml mounts secrets/encryption_kek into the
# services that need it, read-only.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS_DIR="$SCRIPT_DIR/../secrets"
KEK_PATH="$SECRETS_DIR/encryption_kek"

mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

# Docker creates a DIRECTORY at any bind-mount source that does not exist yet.
# So `docker compose up` before this script has run leaves secrets/encryption_kek
# as an empty directory, and the `-f` guard below does not catch it -- a
# directory is not a regular file. The script then died on the redirect with
# "Is a directory", which says nothing about what happened or how to fix it.
if [ -d "$KEK_PATH" ]; then
  if [ -n "$(ls -A "$KEK_PATH" 2>/dev/null)" ]; then
    echo "FATAL: $KEK_PATH is a non-empty directory." >&2
    echo "Expected a file. Inspect it and move it aside before re-running." >&2
    exit 1
  fi
  echo "Note: $KEK_PATH was an empty directory -- Docker creates one at a bind-mount"
  echo "      source that does not exist yet, which happens when \`docker compose up\`"
  echo "      runs before this script. Removing it and continuing."
  rmdir "$KEK_PATH"
fi

if [ -f "$KEK_PATH" ]; then
  echo "FATAL: $KEK_PATH already exists -- refusing to overwrite a live KEK." >&2
  echo "Overwriting it makes every private key currently encrypted under it permanently undecryptable." >&2
  echo "If you actually mean to rotate the KEK, that's a supported-but-different operation" >&2
  echo "(load both old and new KEKs, re-encrypt every row, then retire the old file) -- not a plain overwrite." >&2
  exit 1
fi

openssl rand -base64 32 > "$KEK_PATH"
chmod 400 "$KEK_PATH"

echo "Generated $KEK_PATH (0400, base64-encoded 32 random bytes -- AES-256 key)."
echo "Back this file up somewhere separate from your database backups:"
echo "a DB backup plus this file together are as good as plaintext keys; either alone is not."
