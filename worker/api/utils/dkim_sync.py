"""DB -> disk sync for DKIM signing keys: the producer that never existed.

The API stores DKIM keys envelope-encrypted in MySQL (dkim_keys), but rspamd
signs from key FILES at /var/lib/rspamd/dkim/{domain}.{selector}.key plus a
domain->selector map (mailer/rspamd/config/local.d/dkim_signing.conf). Nothing
ever exported the database to that directory, so every API-created domain sent
unsigned mail until an operator hand-ran scripts/generate_dkim.py -- which
itself could not run as documented (the rspamd image has no Python; MySQL is
not published on the host).

This module is the single exporter. It is used from two places:

1. Write-through from worker/api/routes/domains.py (create / rotate /
   activate / update / delete) via try_sync_domain() -- best-effort: an
   export failure is logged loudly but never fails the API request, because
   the DB row is the durable source of truth and reconcile() heals drift.
2. As a CLI inside the api container (``python -m utils.dkim_sync ...``),
   driven by scripts/generate_dkim.py on the host, which relays the desired
   state into the rspamd container (the only service that mounts
   storage/dkim_keys today -- see docker-compose.yml's rspamd volumes).

Filesystem contract (verified against mailer/rspamd/config/local.d/
dkim_signing.conf and docker-compose.yml):
  - key file:   {DKIM_KEY_DIR}/{domain}.{selector}.key  (0640, atomic replace)
  - selector map: one "domain selector" line per active key of an active,
    dkim_enabled domain. Written to {DKIM_SELECTOR_MAP} (default: inside
    DKIM_KEY_DIR so it lives on the shared mount; rspamd's conf reads
    /etc/rspamd/dkim_selectors.map, which the host wrapper pushes to).
  - selectors never contain dots (enforced below), so a filename parses
    unambiguously as name.rsplit -> (domain, selector) -- required for safe
    per-domain removal in a multi-tenant directory.

Private key material is decrypted in memory only, never logged, and written
straight to a 0o600 temp file that is atomically renamed into place.
"""

import contextlib
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_KEY_DIR = "/var/lib/rspamd/dkim"
MAP_FILENAME = "dkim_selectors.map"

# A domain is dot-separated LDH labels; a selector is a single LDH label
# (it becomes one label of {selector}._domainkey.{domain}, and MUST NOT
# contain dots or slashes -- both values end up in a filesystem path, so this
# is also the path-traversal guard for the shared key directory.
_DOMAIN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$", re.I
)
_SELECTOR_RE = re.compile(r"^[a-z0-9]([a-z0-9_-]{0,61}[a-z0-9])?$", re.I)


class KeyDirUnavailableError(RuntimeError):
    """The DKIM key directory is not present/writable in this container.

    Expected whenever the api service does not (yet) bind-mount
    storage/dkim_keys -- the DB write already succeeded; run
    ``scripts/generate_dkim.py sync`` on the docker host to export.
    """


def key_dir() -> Path:
    return Path(os.getenv("DKIM_KEY_DIR", DEFAULT_KEY_DIR))


def selector_map_path() -> Path:
    explicit = os.getenv("DKIM_SELECTOR_MAP", "").strip()
    return Path(explicit) if explicit else key_dir() / MAP_FILENAME


def is_safe_pair(domain: str, selector: str) -> bool:
    return bool(domain and selector and _DOMAIN_RE.match(domain) and _SELECTOR_RE.match(selector))


def key_file_name(domain: str, selector: str) -> str:
    return f"{domain}.{selector}.key"


def parse_key_file_name(name: str):
    """Inverse of key_file_name: '{domain}.{selector}.key' -> (domain, selector).

    Selectors cannot contain dots (enforced on write), so the LAST dot before
    '.key' splits domain from selector unambiguously. Returns None for names
    this exporter did not write.
    """
    if not name.endswith(".key"):
        return None
    body = name[: -len(".key")]
    domain, _, selector = body.rpartition(".")
    # rpartition leaves domain empty when there is no dot at all
    if not domain or not is_safe_pair(domain, selector):
        return None
    return domain, selector


def public_key_b64(stored: str | None) -> str | None:
    """Normalise dkim_keys.public_key to the bare base64 a DKIM TXT p= tag
    carries. Two writers historically used two formats: the API stores bare
    base64 DER, scripts/generate_dkim.py stored full PEM with armour lines.
    """
    if not stored:
        return None
    lines = [line.strip() for line in stored.strip().splitlines()]
    return "".join(line for line in lines if line and not line.startswith("-----")) or None


def _get_db_connection():
    # Lazy so this module imports (and is unit-testable) without a DB driver
    # or DB_* environment configured.
    from utils.database import get_db_connection

    return get_db_connection()


def _decrypt_row(row) -> str | None:
    """Private key PEM for a dkim_keys row, decrypted in memory only.

    Prefers the envelope-encrypted columns (phase-07 C2); falls back to the
    legacy plaintext column for pre-encryption rows that were never rotated.
    Returns None when the row carries no usable private material.
    """
    ciphertext = row.get("private_key_ciphertext")
    nonce = row.get("private_key_nonce")
    if ciphertext and nonce:
        from shared.envelope_encryption import decrypt_private_key

        return decrypt_private_key(
            bytes(ciphertext), bytes(nonce), int(row.get("key_version") or 1)
        )
    legacy = row.get("private_key")
    return legacy or None


def ensure_key_dir() -> Path:
    d = key_dir()
    if not d.is_dir():
        raise KeyDirUnavailableError(
            f"DKIM key directory {d} does not exist in this container. The key row is safe in "
            "MySQL, but rspamd cannot sign until it is exported: run "
            "`scripts/generate_dkim.py sync` on the docker host (or mount ./storage/dkim_keys "
            "into this service and re-run `python -m utils.dkim_sync reconcile`)."
        )
    if not os.access(d, os.W_OK):
        raise KeyDirUnavailableError(
            f"DKIM key directory {d} is not writable by uid {os.getuid()}. rspamd's entrypoint "
            "chowns the mount to _rspamd on start; run `scripts/generate_dkim.py sync` on the "
            "docker host, which writes via the rspamd container instead."
        )
    return d


def _atomic_write(path: Path, content: str, mode: int) -> None:
    """Write content to path atomically (temp file + rename), never leaving a
    partially written key visible to rspamd. The temp file is created 0600 by
    mkstemp, chmod'ed to the final mode before the rename."""
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _key_file_mode() -> int:
    # 0640 (not 0600): the api process (uid 10001) owns what it writes, and
    # group-read is what lets rspamd's _rspamd user read the key once the
    # rspamd entrypoint's `chown -R _rspamd` (or the host wrapper's chown)
    # has run. Override with DKIM_KEY_FILE_MODE=0600 where the writer and
    # reader are the same user.
    raw = os.getenv("DKIM_KEY_FILE_MODE", "0640")
    try:
        return int(raw, 8)
    except ValueError:
        logger.warning(f"DKIM_KEY_FILE_MODE={raw!r} is not octal; using 0640")
        return 0o640


def write_key_file(domain: str, selector: str, private_pem: str) -> bool:
    """Write one private key file. Returns True if the file was (re)written,
    False when it already held exactly this key."""
    if not is_safe_pair(domain, selector):
        raise ValueError(f"refusing unsafe domain/selector pair: {domain!r} / {selector!r}")
    directory = ensure_key_dir()
    if not private_pem.endswith("\n"):
        private_pem += "\n"
    path = directory / key_file_name(domain, selector)
    try:
        if path.read_text() == private_pem:
            return False
    except OSError:
        pass  # missing, or unreadable to us (e.g. chowned to _rspamd) -- rewrite
    _atomic_write(path, private_pem, _key_file_mode())
    return True


def render_selector_map(pairs) -> str:
    """The rspamd selector map content for [(domain, selector), ...]."""
    lines = [
        "# DKIM selector map -- domain selector",
        "# Auto-generated from MySQL (dkim_keys) by utils/dkim_sync.py; do not edit.",
        f"# Updated: {datetime.now().isoformat()}",
    ]
    for domain, selector in sorted(set(pairs)):
        lines.append(f"{domain} {selector}")
    return "\n".join(lines) + "\n"


def write_selector_map(pairs, path: Path | None = None) -> Path:
    target = path or selector_map_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # The map holds no secrets and rspamd must read it: 0644.
    _atomic_write(target, render_selector_map(pairs), 0o644)
    return target


# ---------------------------------------------------------------------------
# Desired state from the database
# ---------------------------------------------------------------------------

_ROWS_SQL = """
    SELECT d.domain, d.active AS domain_active, d.dkim_enabled, d.dkim_selector,
           dk.selector, dk.active AS key_active, dk.private_key,
           dk.private_key_ciphertext, dk.private_key_nonce, dk.key_version,
           dk.public_key
    FROM dkim_keys dk
    JOIN domains d ON dk.domain_id = d.id
"""


def fetch_key_rows(conn, domain: str | None = None):
    cursor = conn.cursor(dictionary=True)
    try:
        if domain:
            cursor.execute(_ROWS_SQL + " WHERE d.domain = %s", (domain,))
        else:
            cursor.execute(_ROWS_SQL)
        return cursor.fetchall()
    finally:
        cursor.close()


def active_map_pairs(rows):
    """(domain, selector) pairs the selector map should carry: the active key
    of every active, dkim_enabled domain. dkim_keys.active is the signing
    truth the rotate/activate flow maintains (domains.dkim_selector is kept
    in step by the activate endpoint); disagreements are logged, not hidden.
    """
    pairs = []
    for row in rows:
        if not (row["domain_active"] and row["dkim_enabled"] and row["key_active"]):
            continue
        domain, selector = row["domain"], row["selector"]
        if not is_safe_pair(domain, selector):
            logger.warning(f"selector map: skipping unsafe pair {domain!r}/{selector!r}")
            continue
        if row.get("dkim_selector") and row["dkim_selector"] != selector:
            logger.warning(
                f"DKIM state disagreement for {domain}: domains.dkim_selector="
                f"{row['dkim_selector']!r} but active dkim_keys.selector={selector!r}; "
                "the map follows the active key"
            )
        pairs.append((domain, selector))
    return pairs


def _desired_key_files(rows):
    """{(domain, selector): private_pem} for every key row (active AND
    inactive) of active, dkim_enabled domains.

    Inactive keys are deliberately included: the rotate endpoint mints the
    new key inactive so its DNS record can propagate first, and the file must
    already be on disk when the activate cutover happens. Rows without usable
    private material (or with unsafe names) are reported, not written.
    """
    desired, skipped = {}, []
    for row in rows:
        domain, selector = row["domain"], row["selector"]
        if not (row["domain_active"] and row["dkim_enabled"]):
            continue
        if not is_safe_pair(domain, selector):
            skipped.append((domain, selector, "unsafe domain/selector name"))
            continue
        try:
            pem = _decrypt_row(row)
        except Exception as exc:  # KEK missing, tampered ciphertext, ...
            skipped.append((domain, selector, f"decrypt failed: {exc}"))
            continue
        if not pem:
            skipped.append((domain, selector, "no private key material stored"))
            continue
        desired[(domain, selector)] = pem
    return desired, skipped


def reconcile(conn=None, prune: bool = False) -> dict:
    """Export the full DB state to the key directory + selector map.

    With prune=True, key files whose (domain, selector) no longer exists on
    an active, dkim_enabled domain are deleted -- covers deleted domains,
    disabled DKIM, and files this exporter would not have written.
    """
    directory = ensure_key_dir()
    own_conn = conn is None
    if own_conn:
        conn = _get_db_connection()
        if conn is None:
            raise RuntimeError("database connection failed")
    try:
        rows = fetch_key_rows(conn)
        desired, skipped = _desired_key_files(rows)

        written, unchanged, errors = [], 0, []
        for (domain, selector), pem in desired.items():
            try:
                if write_key_file(domain, selector, pem):
                    written.append(key_file_name(domain, selector))
                else:
                    unchanged += 1
            except Exception as exc:
                errors.append(f"{key_file_name(domain, selector)}: {exc}")

        removed = []
        if prune:
            for entry in sorted(directory.iterdir()):
                parsed = parse_key_file_name(entry.name)
                if parsed is None or parsed in desired:
                    continue
                try:
                    entry.unlink()
                    removed.append(entry.name)
                except OSError as exc:
                    errors.append(f"prune {entry.name}: {exc}")

        pairs = active_map_pairs(rows)
        map_target = write_selector_map(pairs)

        return {
            "key_dir": str(directory),
            "written": written,
            "unchanged": unchanged,
            "removed": removed,
            "skipped": [f"{d}.{s}: {reason}" for d, s, reason in skipped],
            "errors": errors,
            "map_path": str(map_target),
            "map_entries": len(set(pairs)),
        }
    finally:
        if own_conn and conn is not None:
            conn.close()


def sync_domain(conn, domain: str) -> dict:
    """Export one domain's keys (and refresh the global selector map).

    A domain that is missing, inactive, or dkim-disabled has its key files
    REMOVED -- this is what makes dkim_enabled=false and domain deletion
    actually stop rspamd signing (try_fallback signs any domain whose
    default-selector file exists, so a leftover file IS a signing decision).
    """
    directory = ensure_key_dir()
    rows = fetch_key_rows(conn, domain=domain)
    desired, skipped = _desired_key_files(rows)

    written, unchanged, removed, errors = [], 0, [], []
    for (row_domain, selector), pem in desired.items():
        try:
            if write_key_file(row_domain, selector, pem):
                written.append(key_file_name(row_domain, selector))
            else:
                unchanged += 1
        except Exception as exc:
            errors.append(f"{key_file_name(row_domain, selector)}: {exc}")

    # Remove this domain's stale files (rotation leftovers stay -- only files
    # for a now-disabled/deleted domain, i.e. desired is empty, or files no
    # row backs any more after a DB-level cleanup).
    for entry in sorted(directory.iterdir()):
        parsed = parse_key_file_name(entry.name)
        if parsed is None or parsed[0] != domain or parsed in desired:
            continue
        try:
            entry.unlink()
            removed.append(entry.name)
        except OSError as exc:
            errors.append(f"remove {entry.name}: {exc}")

    map_pairs = active_map_pairs(fetch_key_rows(conn))
    map_target = write_selector_map(map_pairs)

    return {
        "domain": domain,
        "written": written,
        "unchanged": unchanged,
        "removed": removed,
        "skipped": [f"{d}.{s}: {reason}" for d, s, reason in skipped],
        "errors": errors,
        "map_path": str(map_target),
        "map_entries": len(set(map_pairs)),
    }


def try_sync_domain(domain: str) -> bool:
    """Best-effort write-through for the API routes. Never raises.

    Returns True when the export ran cleanly. False means the DB is still
    correct but disk was not updated -- the log line says exactly why and
    what to run; reconcile()/`scripts/generate_dkim.py sync` heals it.
    """
    conn = None
    try:
        conn = _get_db_connection()
        if conn is None:
            logger.error(
                f"DKIM sync for {domain}: database connection failed; run "
                "`scripts/generate_dkim.py sync` to export keys to rspamd"
            )
            return False
        result = sync_domain(conn, domain)
        if result["errors"]:
            logger.error(f"DKIM sync for {domain} finished with errors: {result['errors']}")
            return False
        if result["skipped"]:
            logger.warning(f"DKIM sync for {domain} skipped keys: {result['skipped']}")
        logger.info(
            f"DKIM sync for {domain}: wrote {len(result['written'])} key file(s), "
            f"removed {len(result['removed'])}, map now {result['map_entries']} entr(y/ies)"
        )
        return True
    except KeyDirUnavailableError as exc:
        logger.error(f"DKIM sync for {domain}: {exc}")
        return False
    except Exception as exc:
        logger.error(f"DKIM sync for {domain} failed: {exc}")
        return False
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()


# ---------------------------------------------------------------------------
# Key generation (backfill for domains that predate the API's DKIM path)
# ---------------------------------------------------------------------------


def _generate_keypair(key_size: int = 2048):
    """Same key shape create_domain() writes: TraditionalOpenSSL private PEM,
    bare-base64 DER SubjectPublicKeyInfo public."""
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private_pem, base64.b64encode(public_der).decode()


def generate_missing(conn) -> list:
    """Mint a key for every active, dkim_enabled domain with NO dkim_keys row.

    Deliberately does NOT touch domains that already have a key in any state:
    same-selector re-keying invalidates in-flight signatures, and rotation
    has a safe two-step API flow (POST /dkim/rotate then .../activate).
    Returns [{domain, selector, record_name, record_value}, ...] for the rows
    it created.
    """
    from shared.envelope_encryption import encrypt_private_key
    from shared.ulid_utils import generate_ulid

    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT d.id, d.domain, d.dkim_selector
            FROM domains d
            WHERE d.active = 1 AND d.dkim_enabled = 1
              AND NOT EXISTS (SELECT 1 FROM dkim_keys dk WHERE dk.domain_id = d.id)
            """
        )
        targets = cursor.fetchall()

        created = []
        for row in targets:
            domain = row["domain"]
            selector = row["dkim_selector"] or "default"
            if not is_safe_pair(domain, selector):
                logger.warning(f"generate_missing: skipping unsafe pair {domain!r}/{selector!r}")
                continue
            private_pem, public_b64 = _generate_keypair()
            encrypted = encrypt_private_key(private_pem)
            now = datetime.now()
            cursor.execute(
                "INSERT INTO dkim_keys "
                "(id, domain_id, selector, private_key, private_key_ciphertext, "
                " private_key_nonce, key_version, public_key, active, created_at, updated_at) "
                "VALUES (%s, %s, %s, NULL, %s, %s, %s, %s, 1, %s, %s)",
                (
                    generate_ulid(),
                    row["id"],
                    selector,
                    encrypted.ciphertext,
                    encrypted.nonce,
                    encrypted.key_version,
                    public_b64,
                    now,
                    now,
                ),
            )
            created.append(
                {
                    "domain": domain,
                    "selector": selector,
                    "record_name": f"{selector}._domainkey.{domain}",
                    "record_type": "TXT",
                    "record_value": f"v=DKIM1; k=rsa; p={public_b64}",
                }
            )
        conn.commit()
        return created
    finally:
        cursor.close()


def export_state(conn) -> dict:
    """Everything the host-side wrapper needs to materialise the files inside
    the rspamd container: decrypted keys + rendered selector map.

    CONTAINS PRIVATE KEY MATERIAL -- only ever print this to a pipe consumed
    by scripts/generate_dkim.py; the CLI refuses to emit it without the
    explicit --with-private-keys flag.
    """
    rows = fetch_key_rows(conn)
    desired, skipped = _desired_key_files(rows)
    pairs = active_map_pairs(rows)
    return {
        "keys": [
            {
                "domain": domain,
                "selector": selector,
                "file": key_file_name(domain, selector),
                "private_key_pem": pem,
            }
            for (domain, selector), pem in sorted(desired.items())
        ],
        "skipped": [f"{d}.{s}: {reason}" for d, s, reason in skipped],
        "map_content": render_selector_map(pairs),
        "map_entries": len(set(pairs)),
    }


def dns_record(conn, domain: str):
    """The TXT record for a domain's active key, or None."""
    for row in fetch_key_rows(conn, domain=domain):
        if not row["key_active"]:
            continue
        b64 = public_key_b64(row.get("public_key"))
        if not b64:
            continue
        return {
            "record_name": f"{row['selector']}._domainkey.{domain}",
            "record_type": "TXT",
            "record_value": f"v=DKIM1; k=rsa; p={b64}",
        }
    return None


# ---------------------------------------------------------------------------
# CLI -- runs inside the api container: python -m utils.dkim_sync <command>
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_KEY_DIR_UNAVAILABLE = 3
EXIT_DB_UNAVAILABLE = 4
EXIT_PARTIAL = 5


def _cli(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m utils.dkim_sync",
        description="Export DKIM keys from MySQL to the rspamd key directory/selector map.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("reconcile", help="export every key + rebuild the selector map")
    p.add_argument("--prune", action="store_true", help="also delete key files with no DB row")
    p.add_argument("--json", action="store_true", help="machine-readable summary")

    p = sub.add_parser("sync-domain", help="export one domain (removes files if disabled/deleted)")
    p.add_argument("domain")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser(
        "generate-missing", help="mint keys for active domains with none, then export"
    )
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("export", help="print desired state as JSON for the host-side wrapper")
    p.add_argument(
        "--with-private-keys",
        action="store_true",
        help="required: the output CONTAINS DECRYPTED PRIVATE KEYS -- pipe it, never save it",
    )

    p = sub.add_parser("dns", help="print the DNS TXT record for a domain's active key")
    p.add_argument("domain")

    sub.add_parser("map", help="rebuild only the selector map")

    args = parser.parse_args(argv)

    if args.command == "export" and not args.with_private_keys:
        print(
            "refusing: `export` output contains decrypted private keys; "
            "pass --with-private-keys and pipe it (scripts/generate_dkim.py does)",
            file=sys.stderr,
        )
        return EXIT_USAGE

    conn = _get_db_connection()
    if conn is None:
        print("error: database connection failed (DB_HOST reachable?)", file=sys.stderr)
        return EXIT_DB_UNAVAILABLE

    try:
        if args.command == "export":
            json.dump(export_state(conn), sys.stdout)
            return EXIT_OK

        if args.command == "dns":
            record = dns_record(conn, args.domain)
            if record is None:
                print(f"No active DKIM key with a public key for {args.domain}", file=sys.stderr)
                return EXIT_USAGE
            print(f'{record["record_name"]} IN TXT "{record["record_value"]}"')
            return EXIT_OK

        if args.command == "map":
            target = write_selector_map(active_map_pairs(fetch_key_rows(conn)))
            print(f"selector map written: {target}")
            return EXIT_OK

        if args.command == "generate-missing":
            created = generate_missing(conn)
            summary = None
            try:
                summary = reconcile(conn)
            except KeyDirUnavailableError as exc:
                print(f"warning: keys stored in DB but not exported: {exc}", file=sys.stderr)
            if args.json:
                json.dump({"created": created, "reconcile": summary}, sys.stdout)
            else:
                if not created:
                    print("All active dkim_enabled domains already have DKIM keys")
                for record in created:
                    print(f"{record['domain']} (selector {record['selector']}) -- publish:")
                    print(f'  {record["record_name"]} IN TXT "{record["record_value"]}"')
            return EXIT_OK if summary is not None else EXIT_KEY_DIR_UNAVAILABLE

        if args.command == "reconcile":
            summary = reconcile(conn, prune=args.prune)
        else:  # sync-domain
            summary = sync_domain(conn, args.domain)

        if args.json:
            json.dump(summary, sys.stdout)
        else:
            print(
                f"key dir {summary.get('key_dir', key_dir())}: "
                f"{len(summary['written'])} written, {summary['unchanged']} unchanged, "
                f"{len(summary['removed'])} removed"
            )
            for line in summary["skipped"]:
                print(f"  skipped: {line}")
            for line in summary["errors"]:
                print(f"  ERROR: {line}", file=sys.stderr)
            print(f"selector map: {summary['map_path']} ({summary['map_entries']} entries)")
        return EXIT_PARTIAL if summary["errors"] else EXIT_OK

    except KeyDirUnavailableError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_KEY_DIR_UNAVAILABLE
    finally:
        with contextlib.suppress(Exception):
            conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(_cli())
