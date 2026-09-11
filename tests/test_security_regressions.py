#!/usr/bin/env python3
"""
Security regression tests (phase-07 7.13, security-model.md Part C).

Security fixes rot without tests -- these guard the release-blocker (C1-C4)
and high-severity (H1-H8) findings phase-07 fixed so a later change can't
silently reopen one. Several of these are adapted from the plan's own
example test bodies rather than copied verbatim -- each adaptation is
explained inline, at the point reality diverged from the sketch.
"""

import glob
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "worker" / "api"))

import os as _os

_os.chdir(PROJECT_ROOT)  # every check below reads repo-relative paths

pytestmark = pytest.mark.security


# --- C3: fail-closed secrets -------------------------------------------


def test_no_weak_default_secrets():
    """No required secret may have a `${VAR:-...}` compose-level fallback.

    The plan's own example does a whole-file substring scan for literal
    weak values (`rootpassword`, `:-admin}`, ...). Run as-is against the
    current file, that also flags this module's own explanatory comment
    quoting the old `rootpassword` default in prose, and
    `${GRAFANA_ADMIN_USER:-admin}` -- a default *username*, not a secret;
    the actual credential, GRAFANA_ADMIN_PASSWORD, has no default and is
    covered by the assertion below. Scoping to `${VAR:-...}` syntax and
    the specific secret-var names avoids both false positives.
    """
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text()
    secret_vars = {
        "DB_ROOT_PASSWORD",
        "DB_PASSWORD",
        "WEBHOOK_SECRET",
        "OAUTH_TOKEN_SECRET",
        "URL_HMAC_SECRET",
        "ADMIN_PASSWORD",
        "ADMIN_TOKEN_SECRET",
        "GRAFANA_ADMIN_PASSWORD",
    }
    defaulted = {m for m in re.findall(r"\$\{([A-Z_]+):-", compose) if m in secret_vars}
    assert not defaulted, f"secret(s) with a compose-level default: {defaulted}"


def test_secrets_check_catches_placeholder_prefixes():
    """Regression guard for the gap found live while building H6: the
    running dev stack had WEBHOOK_SECRET=your-webhook-secret-key for its
    entire uptime -- 23 characters, not a literal in the old WEAK set, so
    verify_secrets() passed it despite generate-secrets.sh's bash-side
    `your-*`/`changeme*` prefix check existing for exactly this reason.
    Stdlib-only module (see its own docstring) -- no fastapi needed."""
    from startup_checks import verify_secrets

    with pytest.raises(SystemExit):
        verify_secrets(env={"X": "your-webhook-secret-key"}, required=["X"])
    with pytest.raises(SystemExit):
        verify_secrets(env={"X": "changeme_root_db_password"}, required=["X"])
    # a real strong secret must still pass
    verify_secrets(env={"X": "kiDwmbKLIFCbSGlYKBfoqUL2KVHJSIqWYLp8R33L9eI="}, required=["X"])


def test_secrets_check_honors_file_convention(tmp_path):
    """H6: `<KEY>_FILE` must resolve to the file's content, mounted-file
    delivery being the whole point of moving DB_ROOT_PASSWORD off a plain
    env var."""
    from startup_checks import verify_secrets

    secret_file = tmp_path / "db_root_password"
    secret_file.write_text("XuWhhKJ/MScPKzhvcrG9RzlgVSExtxzI7yFhqRITLBg=\n")
    verify_secrets(env={"X_FILE": str(secret_file)}, required=["X"])  # no raise


# --- C1: Docker socket ---------------------------------------------------


def test_docker_socket_not_writable():
    """The plan's own example (`if 'docker.sock' in line`) also matches
    prose comments that mention docker.sock without mounting it (this
    file has several, explaining why docker-proxy exists) -- scoped here
    to actual bind-mount list items instead."""
    mount_re = re.compile(r"^\s*-\s*\S*docker\.sock:\S*docker\.sock(:\S+)?\s*$")
    compose_lines = (PROJECT_ROOT / "docker-compose.yml").read_text().splitlines()
    mounts = [line for line in compose_lines if mount_re.match(line)]
    assert mounts, "expected at least one docker.sock mount (docker-proxy) -- none found"
    for line in mounts:
        assert line.rstrip().endswith(":ro"), f"writable docker.sock mount: {line.strip()}"


# --- H1: non-root containers ---------------------------------------------


def test_all_dockerfiles_have_user():
    """Every Dockerfile runs as non-root, except the 6 mail/cert daemons
    that keep their own internal privilege-separation model instead
    (postfix, dovecot, rspamd, cert_manager, activesync, docs) -- each
    dropped to cap_drop: ALL + a minimal, per-service cap_add in
    docker-compose.yml rather than USER, a documented trade-off
    (security-model.md H1), not an oversight this test should flag."""
    ROOT_BY_DESIGN = {
        "docs/Dockerfile",
        "mailer/rspamd/Dockerfile",
        "mailer/dovecot/Dockerfile",
        "mailer/cert_manager/Dockerfile",
        "mailer/postfix/Dockerfile",
        "worker/activesync/Dockerfile",
    }
    all_dockerfiles = glob.glob("**/Dockerfile*", recursive=True)
    assert all_dockerfiles, "glob found no Dockerfiles -- check cwd/pattern"
    bad = [
        f
        for f in all_dockerfiles
        if f not in ROOT_BY_DESIGN and not re.search(r"^USER ", Path(f).read_text(), re.M)
    ]
    assert not bad, f"running as root, not in the documented allowlist: {bad}"


# --- C2: no plaintext DKIM/PGP/S-MIME private keys -----------------------


def test_no_plaintext_dkim_private_key():
    """The plan's own example checks database/migrations/sql/001_init_schema.sql
    for `private_key TEXT NOT NULL`. phase-08 turned that file into a frozen
    historical record -- the source the Alembic baseline was generated from,
    never reapplied or edited again -- so it still and always will contain
    the original NOT NULL column; checking it would be testing history, not
    the live schema. dkim_keys.private_key's actual nullability is owned by
    the Alembic migration chain now; 0003_encrypt_private_keys.py is where
    C2 made it nullable and added the encrypted-storage columns."""
    # Located by name, not by revision number: the Community Edition keeps its
    # own append-only Alembic chain (this migration is 0007 there), so a
    # hardcoded 0003_ makes a shared security test fail on CE for a reason that
    # has nothing to do with security. The invariant is that the migration
    # exists and does these things, not what number it carries.
    candidates = sorted((PROJECT_ROOT / "alembic/versions").glob("*_encrypt_private_keys.py"))
    assert candidates, "no *_encrypt_private_keys.py migration in the Alembic chain"
    migration = candidates[0].read_text()
    assert "op.alter_column('dkim_keys', 'private_key'" in migration
    assert "nullable=True" in migration
    for column in ("private_key_ciphertext", "private_key_nonce", "key_version"):
        assert column in migration, f"missing encrypted-storage column: {column}"


@pytest.mark.integration
def test_dkim_private_key_actually_null_in_db(request):
    """Live counterpart to the static check above -- same assertion the
    plan's own manual verification section runs
    (`SELECT COUNT(*) FROM dkim_keys WHERE private_key IS NOT NULL`).
    Skips cleanly if no reachable MySQL (e.g. plain `pytest tests/` with no
    Docker stack up) rather than failing the whole run."""
    try:
        import mysql.connector
    except ImportError:
        pytest.skip("mysql-connector-python not installed")
    try:
        conn = mysql.connector.connect(
            host=_os.getenv("DB_HOST", "127.0.0.1"),
            port=int(_os.getenv("DB_PORT", 3306)),
            database=_os.getenv("DB_NAME", "mailserver"),
            user=_os.getenv("DB_USER", "root"),
            password=_os.getenv("DB_ROOT_PASSWORD", ""),
        )
    except Exception as exc:
        pytest.skip(f"cannot connect to MySQL: {exc}")
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM dkim_keys WHERE private_key IS NOT NULL")
        (count,) = cursor.fetchone()
        assert count == 0, f"{count} dkim_keys row(s) still have a plaintext private_key"
    finally:
        conn.close()


# --- H2: secrets never logged ---------------------------------------------


def test_secrets_never_logged():
    import subprocess

    hits = subprocess.run(
        [
            "grep",
            "-rnE",
            r"logger\.(info|debug|warning).*\{self\.(admin_token|secret|password)",
            "worker/",
        ],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    ).stdout
    assert not hits, f"secret logging: {hits}"


# --- H7: one password policy, not three -----------------------------------


def test_password_policy_behavior():
    """The canonical validator itself: 12 chars minimum, letters + numbers,
    and a common-password blocklist (security-model.md H7)."""
    from utils.auth import validate_password_strength

    valid, _ = validate_password_strength("Correct-Horse-Battery-42")
    assert valid
    valid, message = validate_password_strength("short1")
    assert not valid and "12 characters" in message
    valid, message = validate_password_strength("password1234")
    assert not valid and "too common" in message


def test_password_policy_single_source_of_truth():
    """mailboxes.py, auth.py, and bootstrap.py used to enforce password
    strength three different, inconsistent ways (8 chars in one place, bare
    min_length=8 with no letters/numbers/blocklist check in the other two).
    Guards against a future edit re-forking the check.

    Not a live import of routes.mailboxes: importing that module pulls in
    routes/__init__.py's full router set (analytics, rag, ...) and this
    repo's worker/api/requirements.txt pins SQLAlchemy==2.0.23, which is
    incompatible with Python >=3.13 (a documented SQLAlchemy issue,
    unrelated to phase-07) -- fixing that pin is out of scope here. A
    source-text check is what the sibling tests in this file already do
    for the same kind of structural regression (test_all_dockerfiles_have_user,
    test_cors_*) and needs no app import at all."""
    mailboxes_py = (PROJECT_ROOT / "worker/api/routes/mailboxes.py").read_text()
    assert "validate_password = validate_password_strength" in mailboxes_py
    assert re.search(r"^def validate_password\(", mailboxes_py, re.M) is None, (
        "mailboxes.py has its own validate_password() again -- "
        "should be an alias for utils.auth.validate_password_strength"
    )
    # auth.py and bootstrap.py are Enterprise-only routers (tenant browser
    # sessions), so they are absent from a Community tree. Each is checked only
    # where it exists: the invariant is "every consumer of the password policy
    # uses the shared one", which stays meaningful with fewer consumers, whereas
    # reading a file that the edition does not ship is just a path assumption.
    for router in ("auth.py", "bootstrap.py"):
        path = PROJECT_ROOT / "worker/api/routes" / router
        if not path.exists():
            continue
        assert "validate_password_strength" in path.read_text(), (
            f"routes/{router} no longer uses the shared password policy"
        )


# --- H8: CORS --------------------------------------------------------------


def test_cors_rejects_wildcard_origin_at_startup():
    """app.py must sys.exit if CORS_ALLOWED_ORIGINS contains "*" --
    allow_credentials=True (required for the session cookie, routes/auth.py)
    makes Starlette reflect any Origin back as allowed once a wildcard is in
    the list, which defeats the restriction entirely. Verified live during
    H8 via `docker compose run -e CORS_ALLOWED_ORIGINS=*`; re-running a full
    process spawn per test is expensive, so this checks the guard itself
    rather than re-deriving Starlette's own reflection behavior."""
    app_py = (PROJECT_ROOT / "worker/api/app.py").read_text()
    guard_match = re.search(
        r'if\s+"\*"\s+in\s+(\w+):\s*\n\s*sys\.exit\(',
        app_py,
    )
    assert guard_match, 'no `if "*" in <origins>: sys.exit(...)` guard found in app.py'


def test_cors_methods_and_headers_are_enumerated():
    """allow_methods/allow_headers must not be ["*"] -- a wildcard here
    would silently accept whatever a future endpoint starts requiring,
    correct or not (security-model.md H8)."""
    app_py = (PROJECT_ROOT / "worker/api/app.py").read_text()
    cors_block = app_py[app_py.index("CORSMiddleware") : app_py.index("CORSMiddleware") + 800]
    assert 'allow_methods=["*"]' not in cors_block
    assert 'allow_headers=["*"]' not in cors_block
