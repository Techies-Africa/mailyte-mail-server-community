#!/usr/bin/env bash
# Generates the Key Encryption Key (KEK) that protects DKIM/PGP/S-MIME
# private keys at rest (phase-07 C2). Run once per environment, before the
# api service first needs to encrypt or rotate a key.
#
# The KEK lives as a mounted file, never an env var and never a DB row
# (H6) -- mount secrets/encryption_kek into the services that need it,
# read-only, at /run/secrets/encryption_kek (or point ENCRYPTION_KEK_PATH
# somewhere else).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS_DIR="$SCRIPT_DIR/../secrets"
KEK_PATH="$SECRETS_DIR/encryption_kek"

mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

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
