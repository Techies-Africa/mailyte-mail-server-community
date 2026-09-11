#!/usr/bin/env python3
"""
Unit tests for worker/api/utils/managesieve.py -- the ManageSieve client and
its availability cache -- against a scripted fake socket.

The first test is the production bug of 2026-08-31: every GET on
/mailbox/{rules,forwarding,vacation} 502'd for any mailbox that had never
saved a filter, because Pigeonhole answers a missing script with
    NO (NONEXISTENT) "Sieve script `mailyte-rules' not found"
and the client only recognised the RFC's example prose ("does not exist").
"""

import base64
import ssl
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "worker" / "api"))

from utils import managesieve as ms  # noqa: E402

GREETING = (
    b'"IMPLEMENTATION" "Dovecot Pigeonhole"\r\n'
    b'"SIEVE" "fileinto reject envelope vacation imap4flags copy include variables body mailbox"\r\n'
    b'"NOTIFY" "mailto"\r\n'
    b'"SASL" "PLAIN LOGIN"\r\n'
    b'"VERSION" "1.0"\r\n'
    b'OK "Dovecot ready."\r\n'
)
GREETING_STARTTLS = GREETING.replace(b'"VERSION"', b'"STARTTLS"\r\n"VERSION"')
GREETING_NO_SASL = GREETING.replace(b'"SASL" "PLAIN LOGIN"', b'"SASL" ""')
LOGGED_IN = b'OK "Logged in."\r\n'
NOT_FOUND = b'NO (NONEXISTENT) "Sieve script `mailyte-rules\' not found"\r\n'


class FakeSocket:
    """recv() hands out the scripted chunks in order (an Exception instance is raised)."""

    def __init__(self, *chunks):
        self.chunks = list(chunks)
        self.sent: list[bytes] = []
        self.closed = False

    def recv(self, _size):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if isinstance(chunk, Exception):
            raise chunk
        return chunk

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True

    def commands(self) -> list[bytes]:
        return [line.rstrip(b"\r\n") for line in self.sent]


class FakeTLSContext:
    def __init__(self, fail: Exception | None = None):
        self.check_hostname = True
        self.verify_mode = ssl.CERT_REQUIRED
        self.fail = fail
        self.wrapped = None

    def wrap_socket(self, sock, **_kwargs):
        if self.fail:
            raise self.fail
        self.wrapped = sock
        return sock


@pytest.fixture(autouse=True)
def master_credentials(monkeypatch):
    monkeypatch.setattr(ms, "SIEVE_MASTER_USER", "jmap_master")
    monkeypatch.setattr(ms, "SIEVE_MASTER_PASSWORD", "master-secret")
    ms.reset_availability()
    yield
    ms.reset_availability()


@pytest.fixture
def connect(monkeypatch):
    """Route socket.create_connection at a FakeSocket; returns the installer."""

    def install(sock_or_exc):
        def fake_create_connection(*_args, **_kwargs):
            if isinstance(sock_or_exc, Exception):
                raise sock_or_exc
            return sock_or_exc

        monkeypatch.setattr(ms.socket, "create_connection", fake_create_connection)
        return sock_or_exc

    return install


# ---------------------------------------------------------------------------
# Missing scripts (the production 502)
# ---------------------------------------------------------------------------


class TestMissingScript:
    def test_nonexistent_response_code_means_none(self, connect):
        """Pigeonhole's exact line for a script that was never saved."""
        sock = connect(FakeSocket(GREETING, LOGGED_IN, NOT_FOUND))
        with ms.ManageSieveClient("user@example.com") as client:
            assert client.get_script("mailyte-rules") is None
        assert b'GETSCRIPT "mailyte-rules"' in sock.commands()

    @pytest.mark.parametrize(
        "line",
        [
            b'NO "Script does not exist"\r\n',
            b'NO "Requested script is nonexistent"\r\n',
            b'NO "Sieve script not found"\r\n',
            b'NO "No such script"\r\n',
        ],
    )
    def test_wording_fallbacks_without_a_code(self, connect, line):
        connect(FakeSocket(GREETING, LOGGED_IN, line))
        with ms.ManageSieveClient("user@example.com") as client:
            assert client.get_script("mailyte-rules") is None

    def test_delete_missing_ok(self, connect):
        connect(FakeSocket(GREETING, LOGGED_IN, NOT_FOUND))
        with ms.ManageSieveClient("user@example.com") as client:
            client.delete_script("mailyte-rules", missing_ok=True)

    def test_delete_missing_raises_by_default(self, connect):
        connect(FakeSocket(GREETING, LOGGED_IN, NOT_FOUND))
        with (
            ms.ManageSieveClient("user@example.com") as client,
            pytest.raises(ms.ManageSieveError) as info,
        ):
            client.delete_script("mailyte-rules")
        assert info.value.code == "NONEXISTENT"


# ---------------------------------------------------------------------------
# Response codes and diagnostics
# ---------------------------------------------------------------------------


class TestRefusals:
    def test_other_no_carries_code_and_message(self, connect):
        connect(FakeSocket(GREETING, LOGGED_IN, b'NO (QUOTA/MAXSIZE) "Script too large"\r\n'))
        with (
            ms.ManageSieveClient("user@example.com") as client,
            pytest.raises(ms.ManageSieveError) as info,
        ):
            client.put_script("mailyte-rules", "keep;")
        assert info.value.code == "QUOTA/MAXSIZE"
        assert str(info.value) == "Script too large"
        assert not isinstance(info.value, ms.ManageSieveUnavailableError)

    def test_compiler_diagnostic_literal_is_the_message(self, connect):
        diagnostic = b"line 3: error: unknown command 'foo'.\r\nerror: validation failed."
        response = b"NO {%d}\r\n%s\r\n" % (len(diagnostic), diagnostic)
        connect(FakeSocket(GREETING, LOGGED_IN, response))
        with (
            ms.ManageSieveClient("user@example.com") as client,
            pytest.raises(ms.ManageSieveError) as info,
        ):
            client.put_script("mailyte-rules", "foo;")
        assert str(info.value) == diagnostic.decode()
        assert info.value.code is None

    def test_active_script_cannot_be_deleted(self, connect):
        connect(
            FakeSocket(
                GREETING, LOGGED_IN, b'NO (ACTIVE) "You may not delete an active script"\r\n'
            )
        )
        with (
            ms.ManageSieveClient("user@example.com") as client,
            pytest.raises(ms.ManageSieveError) as info,
        ):
            client.delete_script("mailyte-main", missing_ok=True)
        assert info.value.code == "ACTIVE"

    def test_list_scripts_reports_foreign_names_verbatim(self, connect):
        """Migrated mailboxes carry an inactive 'sogo' script; it is listed, never touched."""
        connect(
            FakeSocket(
                GREETING,
                LOGGED_IN,
                b'"sogo"\r\n"mailyte-main" ACTIVE\r\nOK "Listscripts completed."\r\n',
            )
        )
        with ms.ManageSieveClient("user@example.com") as client:
            assert client.list_scripts() == [
                {"name": "sogo", "active": False},
                {"name": "mailyte-main", "active": True},
            ]


# ---------------------------------------------------------------------------
# Connection, TLS and login failures are "unavailable", never a bare exception
# ---------------------------------------------------------------------------


class TestUnavailable:
    def test_connection_refused(self, connect):
        connect(ConnectionRefusedError(111, "Connection refused"))
        with pytest.raises(ms.ManageSieveUnavailableError) as info:
            ms.ManageSieveClient("user@example.com").connect()
        assert str(info.value).startswith("Cannot reach the Sieve service")

    def test_timeout_mid_read_is_not_an_auth_failure(self, connect):
        connect(FakeSocket(GREETING, TimeoutError("timed out")))
        with pytest.raises(ms.ManageSieveUnavailableError) as info:
            ms.ManageSieveClient("user@example.com").connect()
        assert str(info.value).startswith("Cannot reach the Sieve service")
        assert "authentication" not in str(info.value).lower()

    def test_server_closing_mid_response(self, connect):
        connect(FakeSocket(GREETING, LOGGED_IN, b""))
        with (
            ms.ManageSieveClient("user@example.com") as client,
            pytest.raises(ms.ManageSieveUnavailableError),
        ):
            client.get_script("mailyte-rules")

    def test_bye_is_unavailable(self, connect):
        connect(FakeSocket(GREETING, LOGGED_IN, b'BYE "Too many invalid commands."\r\n'))
        with (
            ms.ManageSieveClient("user@example.com") as client,
            pytest.raises(ms.ManageSieveUnavailableError),
        ):
            client.get_script("mailyte-rules")

    def test_auth_failure_keeps_server_reason_and_never_the_secret(self, connect):
        connect(FakeSocket(GREETING, b'NO "Authentication failed."\r\n'))
        with pytest.raises(ms.ManageSieveUnavailableError) as info:
            ms.ManageSieveClient("user@example.com").connect()
        message = str(info.value)
        assert message.startswith("Sieve authentication failed for user@example.com")
        assert "Authentication failed." in message
        token = base64.b64encode(b"user@example.com\0jmap_master\0master-secret").decode()
        assert token not in message
        assert "master-secret" not in message

    def test_missing_credentials(self, monkeypatch):
        monkeypatch.setattr(ms, "SIEVE_MASTER_USER", "")
        with pytest.raises(ms.ManageSieveUnavailableError):
            ms.ManageSieveClient("user@example.com").connect()

    def test_tls_handshake_failure(self, connect, monkeypatch):
        connect(FakeSocket(GREETING_STARTTLS, b'OK "Begin TLS negotiation now."\r\n'))
        monkeypatch.setattr(
            ms.ssl, "create_default_context", lambda: FakeTLSContext(fail=ssl.SSLError("handshake"))
        )
        with pytest.raises(ms.ManageSieveUnavailableError) as info:
            ms.ManageSieveClient("user@example.com").connect()
        assert "TLS" in str(info.value)


# ---------------------------------------------------------------------------
# The happy path, including STARTTLS and the PLAIN token
# ---------------------------------------------------------------------------


class TestLogin:
    def test_plain_token_is_authzid_master_password(self, connect):
        sock = connect(FakeSocket(GREETING, LOGGED_IN))
        with ms.ManageSieveClient("user@example.com"):
            pass
        auth = [c for c in sock.commands() if c.startswith(b"AUTHENTICATE")]
        assert len(auth) == 1
        token = auth[0].split(b'"')[3]
        assert base64.b64decode(token) == b"user@example.com\0jmap_master\0master-secret"
        assert sock.commands()[-1] == b"LOGOUT"
        assert sock.closed

    def test_starttls_negotiated_before_login(self, connect, monkeypatch):
        sock = connect(
            FakeSocket(
                GREETING_STARTTLS,
                b'OK "Begin TLS negotiation now."\r\n',
                GREETING,  # re-issued capabilities after the handshake
                LOGGED_IN,
            )
        )
        context = FakeTLSContext()
        monkeypatch.setattr(ms.ssl, "create_default_context", lambda: context)
        with ms.ManageSieveClient("user@example.com") as client:
            assert client.capabilities["SASL"] == "PLAIN LOGIN"
        commands = sock.commands()
        assert commands.index(b"STARTTLS") < next(
            i for i, c in enumerate(commands) if c.startswith(b"AUTHENTICATE")
        )
        assert context.wrapped is sock
        assert context.check_hostname is False
        assert context.verify_mode == ssl.CERT_NONE


# ---------------------------------------------------------------------------
# Availability probe and cache
# ---------------------------------------------------------------------------


class TestProbe:
    def test_reachable_when_plain_offered(self, connect):
        sock = connect(FakeSocket(GREETING))
        ok, detail = ms.probe_service()
        assert ok, detail
        assert not any(c.startswith(b"AUTHENTICATE") for c in sock.commands())
        assert sock.closed

    def test_not_ok_when_plain_not_offered(self, connect):
        connect(FakeSocket(GREETING_NO_SASL))
        ok, detail = ms.probe_service()
        assert not ok
        assert "PLAIN" in detail

    def test_not_ok_when_unreachable(self, connect):
        connect(OSError("Name or service not known"))
        ok, detail = ms.probe_service()
        assert not ok
        assert detail.startswith("Cannot reach the Sieve service")

    def test_account_upgrades_probe_to_a_login(self, connect):
        sock = connect(FakeSocket(GREETING, LOGGED_IN))
        ok, detail = ms.probe_service(account="postmaster@example.com")
        assert ok, detail
        assert any(c.startswith(b"AUTHENTICATE") for c in sock.commands())

    def test_account_login_failure_is_not_ok(self, connect):
        connect(FakeSocket(GREETING, b'NO "Authentication failed."\r\n'))
        ok, detail = ms.probe_service(account="postmaster@example.com")
        assert not ok
        assert "authentication failed" in detail.lower()


class TestAvailabilityCache:
    @pytest.fixture
    def clock(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(ms._availability, "_clock", lambda: now[0])
        return now

    @pytest.fixture
    def probe(self, monkeypatch):
        calls = {"n": 0, "result": (True, "ok")}

        def fake_probe(**_kwargs):
            calls["n"] += 1
            return calls["result"]

        monkeypatch.setattr(ms, "probe_service", fake_probe)
        return calls

    def test_positive_result_is_cached_for_ttl(self, clock, probe, monkeypatch):
        monkeypatch.setattr(ms, "SIEVE_PROBE_TTL", 60)
        assert ms.sieve_available() is True
        assert ms.sieve_available() is True
        assert probe["n"] == 1
        clock[0] += 61
        assert ms.sieve_available() is True
        assert probe["n"] == 2

    def test_negative_result_is_cached_too(self, clock, probe, monkeypatch):
        monkeypatch.setattr(ms, "SIEVE_PROBE_TTL", 60)
        probe["result"] = (False, "Cannot reach the Sieve service: refused")
        assert ms.sieve_available() is False
        assert ms.sieve_available() is False
        assert probe["n"] == 1

    def test_report_failure_flips_off_until_ttl_expires(self, clock, probe, monkeypatch):
        monkeypatch.setattr(ms, "SIEVE_PROBE_TTL", 60)
        assert ms.sieve_available() is True
        ms.report_failure("Sieve authentication failed for a@b.c: Authentication failed.")
        assert ms.sieve_available() is False
        assert probe["n"] == 1, "a reported failure is authoritative; no re-probe inside the TTL"
        clock[0] += 61
        assert ms.sieve_available() is True
        assert probe["n"] == 2

    def test_report_success_heals_immediately(self, clock, probe):
        probe["result"] = (False, "down")
        assert ms.sieve_available() is False
        ms.report_success()
        assert ms.sieve_available() is True
        assert probe["n"] == 1

    def test_reset_forces_a_fresh_probe(self, clock, probe):
        assert ms.sieve_available() is True
        ms.reset_availability()
        assert ms.sieve_available() is True
        assert probe["n"] == 2
