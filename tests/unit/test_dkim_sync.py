#!/usr/bin/env python3
"""
Unit tests for worker/api/utils/dkim_sync.py -- the DB -> disk exporter that
finally connects API-generated DKIM keys (envelope-encrypted in MySQL) to the
key files and selector map rspamd actually signs from.

Filesystem is a tmp_path pointed at via DKIM_KEY_DIR; the database is a fake
connection returning canned dkim_keys/domains join rows; envelope encryption
runs for real against a throwaway KEK (ENCRYPTION_KEK_PATH), so the
decrypt-in-memory path is exercised, not mocked away.
"""

import base64
import os
import stat
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "worker" / "api"))

from utils import dkim_sync  # noqa: E402

import shared.envelope_encryption as envelope  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def key_dir(tmp_path, monkeypatch):
    d = tmp_path / "dkim"
    d.mkdir()
    monkeypatch.setenv("DKIM_KEY_DIR", str(d))
    monkeypatch.delenv("DKIM_SELECTOR_MAP", raising=False)
    monkeypatch.delenv("DKIM_KEY_FILE_MODE", raising=False)
    return d


@pytest.fixture
def kek(tmp_path, monkeypatch):
    kek_file = tmp_path / "kek"
    kek_file.write_bytes(base64.b64encode(os.urandom(32)))
    monkeypatch.setenv("ENCRYPTION_KEK_PATH", str(kek_file))
    envelope._kek_cache.clear()
    yield
    envelope._kek_cache.clear()


PEM_A = "-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----\n"
PEM_B = "-----BEGIN RSA PRIVATE KEY-----\nBBBB\n-----END RSA PRIVATE KEY-----\n"


def make_row(
    domain="example.com",
    selector="default",
    *,
    domain_active=1,
    dkim_enabled=1,
    dkim_selector=None,
    key_active=1,
    pem=PEM_A,
    legacy_plaintext=None,
    public_key="cHVibGlj",
):
    """One row of the dkim_keys JOIN domains query, envelope-encrypted like
    create_domain() writes it (or legacy-plaintext, or empty)."""
    ciphertext = nonce = None
    key_version = 1
    if pem is not None and legacy_plaintext is None:
        encrypted = envelope.encrypt_private_key(pem)
        ciphertext, nonce, key_version = (
            encrypted.ciphertext,
            encrypted.nonce,
            encrypted.key_version,
        )
    return {
        "domain": domain,
        "domain_active": domain_active,
        "dkim_enabled": dkim_enabled,
        "dkim_selector": dkim_selector if dkim_selector is not None else selector,
        "selector": selector,
        "key_active": key_active,
        "private_key": legacy_plaintext,
        "private_key_ciphertext": ciphertext,
        "private_key_nonce": nonce,
        "key_version": key_version,
        "public_key": public_key,
    }


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._filter_domain = None

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        self._filter_domain = None
        if params and "WHERE d.domain = %s" in sql:
            self._filter_domain = params[0]

    def fetchall(self):
        rows = self.conn.rows
        if self._filter_domain is not None:
            rows = [r for r in rows if r["domain"] == self._filter_domain]
        return [dict(r) for r in rows]

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []
        self.committed = False

    def cursor(self, dictionary=False):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def close(self):
        pass


def file_mode(path):
    return stat.S_IMODE(path.stat().st_mode)


# ---------------------------------------------------------------------------
# Name safety -- these strings become path components in a shared directory
# ---------------------------------------------------------------------------


def test_safe_pair_accepts_normal_names():
    assert dkim_sync.is_safe_pair("example.com", "default")
    assert dkim_sync.is_safe_pair("sub.example.co.uk", "mailyte20260830120000")
    assert dkim_sync.is_safe_pair("example.com", "s_1")


@pytest.mark.parametrize(
    "domain,selector",
    [
        ("../etc", "default"),  # traversal in domain
        ("example.com", "../../etc/passwd"),  # traversal in selector
        ("example.com", "sel.ector"),  # dot would break filename parsing
        ("example.com", ""),
        ("", "default"),
        ("example.com/x", "default"),
        ("example.com", "a" * 64),  # not a DNS label
    ],
)
def test_safe_pair_rejects_hostile_names(domain, selector):
    assert not dkim_sync.is_safe_pair(domain, selector)


def test_parse_key_file_name_roundtrip():
    name = dkim_sync.key_file_name("example.com", "mailyte2026")
    assert name == "example.com.mailyte2026.key"
    assert dkim_sync.parse_key_file_name(name) == ("example.com", "mailyte2026")


@pytest.mark.parametrize("name", ["notakey.txt", "x.key", ".key"])
def test_parse_key_file_name_rejects_foreign_files(name):
    assert dkim_sync.parse_key_file_name(name) is None


def test_parse_key_file_name_multi_label_domain_is_unambiguous():
    # selectors cannot contain dots, so the LAST dot always splits correctly
    assert dkim_sync.parse_key_file_name("mail.sub.example.com.s1.key") == (
        "mail.sub.example.com",
        "s1",
    )


# ---------------------------------------------------------------------------
# File + map writers
# ---------------------------------------------------------------------------


def test_write_key_file_creates_0640_and_is_idempotent(key_dir):
    assert dkim_sync.write_key_file("example.com", "default", PEM_A) is True
    path = key_dir / "example.com.default.key"
    assert path.read_text() == PEM_A
    assert file_mode(path) == 0o640
    # unchanged content -> not rewritten
    assert dkim_sync.write_key_file("example.com", "default", PEM_A) is False
    # rotated content -> rewritten
    assert dkim_sync.write_key_file("example.com", "default", PEM_B) is True
    assert path.read_text() == PEM_B


def test_write_key_file_refuses_unsafe_names(key_dir):
    with pytest.raises(ValueError):
        dkim_sync.write_key_file("../../evil", "default", PEM_A)
    with pytest.raises(ValueError):
        dkim_sync.write_key_file("example.com", "a/b", PEM_A)
    assert list(key_dir.iterdir()) == []


def test_missing_key_dir_raises_key_dir_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("DKIM_KEY_DIR", str(tmp_path / "not-mounted"))
    with pytest.raises(dkim_sync.KeyDirUnavailableError):
        dkim_sync.ensure_key_dir()


def test_selector_map_content_and_mode(key_dir):
    target = dkim_sync.write_selector_map(
        [("zeta.org", "s2"), ("example.com", "default"), ("zeta.org", "s2")]
    )
    assert target == key_dir / "dkim_selectors.map"
    lines = [ln for ln in target.read_text().splitlines() if ln and not ln.startswith("#")]
    # sorted, deduplicated, "domain selector" format
    assert lines == ["example.com default", "zeta.org s2"]
    assert file_mode(target) == 0o644


# ---------------------------------------------------------------------------
# Decryption -- envelope columns preferred, legacy plaintext honoured
# ---------------------------------------------------------------------------


def test_decrypt_row_envelope_roundtrip(kek):
    row = make_row(pem=PEM_A)
    assert row["private_key"] is None  # phase-07 C2: plaintext column stays NULL
    assert dkim_sync._decrypt_row(row) == PEM_A


def test_decrypt_row_legacy_plaintext_fallback(kek):
    row = make_row(pem=None, legacy_plaintext=PEM_B)
    assert dkim_sync._decrypt_row(row) == PEM_B


def test_decrypt_row_no_material(kek):
    assert dkim_sync._decrypt_row(make_row(pem=None)) is None


# ---------------------------------------------------------------------------
# Desired state / selector map pairs
# ---------------------------------------------------------------------------


def test_active_map_pairs_filters_and_follows_active_key(kek):
    rows = [
        make_row("example.com", "mailyte2026", dkim_selector="default"),  # disagreement
        make_row("inactive.com", "default", domain_active=0),
        make_row("nodkim.com", "default", dkim_enabled=0),
        make_row("rotating.com", "old", key_active=0),
        make_row("rotating.com", "new", key_active=1),
    ]
    pairs = dkim_sync.active_map_pairs(rows)
    # the map follows dkim_keys.active, not the stale domains.dkim_selector
    assert ("example.com", "mailyte2026") in pairs
    assert ("rotating.com", "new") in pairs
    assert not any(d == "inactive.com" for d, _ in pairs)
    assert not any(d == "nodkim.com" for d, _ in pairs)
    assert ("rotating.com", "old") not in pairs


# ---------------------------------------------------------------------------
# reconcile / sync_domain / try_sync_domain
# ---------------------------------------------------------------------------


def test_reconcile_writes_keys_map_and_prunes(key_dir, kek):
    # a stale file from a domain that no longer exists
    stale = key_dir / "gone.example.stale.key"
    stale.write_text("old key")
    # something that is not a key file must never be touched by prune
    bystander = key_dir / "dkim_selectors.map"
    rows = [
        make_row("example.com", "default"),
        make_row("example.com", "mailyte2026", key_active=0),  # rotated-in, pre-activate
        make_row("empty.com", "default", pem=None),  # no material -> skipped
    ]
    summary = dkim_sync.reconcile(FakeConn(rows), prune=True)

    assert (key_dir / "example.com.default.key").read_text() == PEM_A
    # inactive keys are exported too -- the file must exist BEFORE activation
    assert (key_dir / "example.com.mailyte2026.key").exists()
    assert not stale.exists()
    assert bystander.exists()
    assert summary["errors"] == []
    assert any("empty.com" in s for s in summary["skipped"])
    assert summary["removed"] == ["gone.example.stale.key"]

    map_lines = [ln for ln in bystander.read_text().splitlines() if ln and not ln.startswith("#")]
    # only ACTIVE keys of enabled domains appear in the map. empty.com is
    # active-but-unexportable: it stays listed (the DB says it should sign);
    # rspamd finds no file and skips it either way, and `skipped` flags it.
    assert map_lines == ["empty.com default", "example.com default"]
    # the pre-activation selector is NOT in the map yet
    assert not any("mailyte2026" in ln for ln in map_lines)


def test_reconcile_without_prune_keeps_stale_files(key_dir, kek):
    stale = key_dir / "gone.example.stale.key"
    stale.write_text("old key")
    summary = dkim_sync.reconcile(FakeConn([make_row()]), prune=False)
    assert stale.exists()
    assert summary["removed"] == []


def test_sync_domain_removes_files_when_dkim_disabled(key_dir, kek):
    (key_dir / "example.com.default.key").write_text(PEM_A)
    (key_dir / "other.com.default.key").write_text(PEM_B)
    rows = [
        make_row("example.com", "default", dkim_enabled=0),
        make_row("other.com", "default"),
    ]
    result = dkim_sync.sync_domain(FakeConn(rows), "example.com")
    # the disabled domain's file is gone; the other tenant's is untouched
    assert not (key_dir / "example.com.default.key").exists()
    assert (key_dir / "other.com.default.key").exists()
    assert result["removed"] == ["example.com.default.key"]
    map_text = (key_dir / "dkim_selectors.map").read_text()
    assert "example.com" not in map_text
    assert "other.com default" in map_text


def test_sync_domain_removes_files_for_deleted_domain(key_dir, kek):
    (key_dir / "deleted.com.default.key").write_text(PEM_A)
    result = dkim_sync.sync_domain(FakeConn([]), "deleted.com")
    assert not (key_dir / "deleted.com.default.key").exists()
    assert result["removed"] == ["deleted.com.default.key"]


def test_try_sync_domain_never_raises(key_dir, kek, monkeypatch):
    # DB down -> False, no exception
    monkeypatch.setattr(dkim_sync, "_get_db_connection", lambda: None)
    assert dkim_sync.try_sync_domain("example.com") is False

    # key dir not mounted -> False, no exception (the api container today)
    monkeypatch.setattr(dkim_sync, "_get_db_connection", lambda: FakeConn([make_row()]))
    monkeypatch.setenv("DKIM_KEY_DIR", str(key_dir / "not-mounted"))
    assert dkim_sync.try_sync_domain("example.com") is False

    # healthy path -> True and the file exists
    monkeypatch.setenv("DKIM_KEY_DIR", str(key_dir))
    assert dkim_sync.try_sync_domain("example.com") is True
    assert (key_dir / "example.com.default.key").exists()


# ---------------------------------------------------------------------------
# generate_missing (backfill) + export_state (host wrapper feed)
# ---------------------------------------------------------------------------


class BackfillCursor(FakeCursor):
    def fetchall(self):
        # respond to the "domains without any dkim_keys row" SELECT
        return [dict(r) for r in self.conn.missing]


class BackfillConn(FakeConn):
    def __init__(self, missing):
        super().__init__(rows=[])
        self.missing = missing

    def cursor(self, dictionary=False):
        return BackfillCursor(self)


def test_generate_missing_inserts_encrypted_key(kek):
    conn = BackfillConn([{"id": "01ABC", "domain": "new.com", "dkim_selector": None}])
    created = dkim_sync.generate_missing(conn)

    assert len(created) == 1
    assert created[0]["domain"] == "new.com"
    assert created[0]["selector"] == "default"
    assert created[0]["record_name"] == "default._domainkey.new.com"
    assert created[0]["record_value"].startswith("v=DKIM1; k=rsa; p=")
    assert conn.committed

    inserts = [(sql, params) for sql, params in conn.executed if sql.startswith("INSERT")]
    assert len(inserts) == 1
    _, params = inserts[0]
    # (id, domain_id, selector, ciphertext, nonce, key_version, public_key, ...)
    assert params[1] == "01ABC"
    assert params[2] == "default"
    ciphertext, nonce = params[3], params[4]
    # plaintext private key never goes to the DB; the ciphertext round-trips
    pem = envelope.decrypt_private_key(bytes(ciphertext), bytes(nonce), params[5])
    assert pem.startswith("-----BEGIN RSA PRIVATE KEY-----")


def test_export_state_carries_keys_and_map(kek):
    rows = [
        make_row("example.com", "default"),
        make_row("nodkim.com", "default", dkim_enabled=0),
    ]
    state = dkim_sync.export_state(FakeConn(rows))
    assert [k["file"] for k in state["keys"]] == ["example.com.default.key"]
    assert state["keys"][0]["private_key_pem"] == PEM_A
    assert "example.com default" in state["map_content"]
    assert "nodkim.com" not in state["map_content"]
    assert state["map_entries"] == 1


# ---------------------------------------------------------------------------
# public key normalisation (shared with routes/domains.py)
# ---------------------------------------------------------------------------


def test_public_key_b64_collapses_pem_and_passes_bare():
    pem = "-----BEGIN PUBLIC KEY-----\nAAAA\nBBBB\n-----END PUBLIC KEY-----\n"
    assert dkim_sync.public_key_b64(pem) == "AAAABBBB"
    assert dkim_sync.public_key_b64("AAAABBBB") == "AAAABBBB"
    assert dkim_sync.public_key_b64(None) is None
    assert dkim_sync.public_key_b64("") is None
