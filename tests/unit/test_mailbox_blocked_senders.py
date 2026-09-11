#!/usr/bin/env python3
"""
Unit tests for worker/api/routes/mailbox_blocked_senders.py and the Sieve
section of worker/api/routes/mailbox.py it builds on:

* the blocked-senders script compiles and round-trips, files to Junk, and
  never emits `keep;`;
* the active script includes the blocked-senders component FIRST and the
  other components' round-trips leave it alone;
* ManageSieve unavailability is a 503 with error_code sieve_unavailable,
  never a 502 or an unhandled exception, and it turns the capability off;
* _sieve_configured() reflects reachability, not just the env vars.

Handlers are called directly with the mailbox context require_mailbox would
have supplied; the ManageSieve client is replaced with an in-memory store.
"""

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "worker" / "api"))

from routes import mailbox as mailbox_routes  # noqa: E402
from routes import mailbox_blocked_senders as bs  # noqa: E402
from utils import managesieve as ms  # noqa: E402
from utils.sieve_compilers import (  # noqa: E402
    FORWARDING_SCRIPT,
    MAIN_SCRIPT,
    RULES_SCRIPT,
    VACATION_SCRIPT,
    compile_main,
)

MAILBOX = {"email": "jane@example.com", "email_account_id": "01MBOX", "organization_id": "01ORG"}


class FakeStore:
    """The ManageSieve state of one mailbox, shared by every FakeClient."""

    def __init__(self):
        self.scripts: dict[str, str] = {}
        self.active: str | None = None
        self.puts = 0
        self.raise_on_enter: Exception | None = None
        self.raise_on_put: Exception | None = None


class FakeClient:
    def __init__(self, store: FakeStore):
        self.store = store

    def __call__(self, email, *args, **kwargs):
        self.email = email
        return self

    def __enter__(self):
        if self.store.raise_on_enter:
            raise self.store.raise_on_enter
        return self

    def __exit__(self, *_exc):
        return None

    def get_script(self, name):
        return self.store.scripts.get(name)

    def put_script(self, name, content):
        if self.store.raise_on_put:
            raise self.store.raise_on_put
        self.store.puts += 1
        self.store.scripts[name] = content

    def set_active(self, name):
        self.store.active = name


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("IMAP_MASTER_USER", "jmap_master")
    monkeypatch.setenv("IMAP_MASTER_PASSWORD", "master-secret")
    ms.reset_availability()
    store = FakeStore()
    monkeypatch.setattr(mailbox_routes, "ManageSieveClient", FakeClient(store))
    yield store
    ms.reset_availability()


def error_code(exc: HTTPException) -> str | None:
    return exc.detail.get("error_code") if isinstance(exc.detail, dict) else None


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


class TestCompile:
    def test_round_trip_normalises_and_dedupes(self):
        script = bs.compile_blocked_senders(["Spam@Example.com", "b@c.org", "spam@example.com"])
        parsed = bs.parse_blocked_senders(script)
        assert parsed == {"addresses": ["spam@example.com", "b@c.org"], "managed": True}

    def test_files_to_junk_and_stops(self):
        script = bs.compile_blocked_senders(["spam@example.com", "b@c.org"])
        assert 'require ["fileinto", "mailbox"];' in script
        assert 'if address :is "from" ["spam@example.com", "b@c.org"] {' in script
        assert 'fileinto :create "Junk";' in script
        assert "stop;" in script
        assert "discard" not in script
        assert bs.JUNK_FOLDER == "Junk"

    def test_require_precedes_every_command(self):
        script = bs.compile_blocked_senders(["spam@example.com"])
        commands = [line for line in script.splitlines() if line and not line.startswith("#")]
        assert commands[0].startswith("require ")

    def test_empty_list_is_comments_only(self):
        script = bs.compile_blocked_senders([])
        assert "keep;" not in script, "an explicit keep would duplicate rule-filed mail into INBOX"
        assert "require" not in script
        assert "if " not in script
        assert bs.BLOCKED_SENDERS_MARKER in script
        assert bs.parse_blocked_senders(script) == {"addresses": [], "managed": True}

    def test_invalid_addresses_dropped(self):
        script = bs.compile_blocked_senders(["not-an-address", "a@b", "x@y.z", "", None])
        assert bs.parse_blocked_senders(script)["addresses"] == ["x@y.z"]

    def test_never_saved_is_managed_and_empty(self):
        assert bs.parse_blocked_senders(None) == {"addresses": [], "managed": True}

    def test_foreign_script_is_unmanaged(self):
        script = 'require ["fileinto"];\nif true { fileinto "Elsewhere"; }\n'
        assert bs.parse_blocked_senders(script) == {"addresses": [], "managed": False}

    def test_apostrophe_survives_round_trip(self):
        script = bs.compile_blocked_senders(["o'brien@example.com"])
        assert bs.parse_blocked_senders(script)["addresses"] == ["o'brien@example.com"]
        assert '"o\'brien@example.com"' in script


class TestNormaliseAddress:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Spam@Example.COM", "spam@example.com"),
            ("  <spam@example.com> ", "spam@example.com"),
            ("first.last+tag@sub.example.co.uk", "first.last+tag@sub.example.co.uk"),
        ],
    )
    def test_accepted(self, raw, expected):
        assert bs.normalise_address(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            None,
            "nope",
            "a@b",
            "a@b.",
            "a@@b.com",
            'a"b@example.com',
            "a\\b@example.com",
            'a@b.com"\nredirect "x@y.z',
            "a b@example.com",
            "<a@b.com",
            "a@-bad.com",
            "x" * 250 + "@e.com",
        ],
    )
    def test_rejected(self, raw):
        assert bs.normalise_address(raw) is None


# ---------------------------------------------------------------------------
# The active script
# ---------------------------------------------------------------------------


class TestActiveScript:
    def test_blocked_senders_included_first(self):
        main = mailbox_routes._compile_active_script()
        includes = [line for line in main.splitlines() if line.startswith("include ")]
        assert includes[0] == f'include :personal :optional "{bs.BLOCKED_SENDERS_SCRIPT}";'
        assert [
            line for line in compile_main().splitlines() if line.startswith("include ")
        ] == includes[1:]
        commands = [line for line in main.splitlines() if line and not line.startswith("#")]
        assert commands[0] == 'require ["include"];'

    def test_idempotent_when_compile_main_already_includes_it(self, monkeypatch):
        already = (
            'require ["include"];\n\n'
            f'include :personal :optional "{bs.BLOCKED_SENDERS_SCRIPT}";\n'
            f'include :personal :optional "{RULES_SCRIPT}";\n'
        )
        monkeypatch.setattr(mailbox_routes, "compile_main", lambda: already)
        assert mailbox_routes._compile_active_script() == already

    def test_appends_when_main_has_no_includes(self, monkeypatch):
        monkeypatch.setattr(mailbox_routes, "compile_main", lambda: 'require ["include"];\n')
        main = mailbox_routes._compile_active_script()
        assert main.endswith(f'include :personal :optional "{bs.BLOCKED_SENDERS_SCRIPT}";\n')


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class TestRoutes:
    def test_never_saved_reads_empty(self, store):
        body = bs.blocked_senders_show(mailbox=MAILBOX)
        assert body["type"] == "success"
        assert body["data"] == {
            "addresses": [],
            "managed": True,
            "folder": "Junk",
            "limit": bs.MAX_BLOCKED_SENDERS,
        }

    def test_add_writes_component_and_activates_main(self, store):
        body = bs.blocked_senders_add(
            bs.BlockSenderRequest(address="Spam@Example.com"), mailbox=MAILBOX
        )
        assert body["data"]["addresses"] == ["spam@example.com"]
        assert store.active == MAIN_SCRIPT
        assert 'fileinto :create "Junk";' in store.scripts[bs.BLOCKED_SENDERS_SCRIPT]
        assert (
            f'include :personal :optional "{bs.BLOCKED_SENDERS_SCRIPT}";'
            in store.scripts[MAIN_SCRIPT]
        )
        # Component stored, main stored -- and nothing else touched.
        assert set(store.scripts) == {bs.BLOCKED_SENDERS_SCRIPT, MAIN_SCRIPT}

    def test_add_then_read_then_add_another(self, store):
        bs.blocked_senders_add(bs.BlockSenderRequest(address="a@example.com"), mailbox=MAILBOX)
        bs.blocked_senders_add(bs.BlockSenderRequest(address="b@example.com"), mailbox=MAILBOX)
        assert bs.blocked_senders_show(mailbox=MAILBOX)["data"]["addresses"] == [
            "a@example.com",
            "b@example.com",
        ]

    def test_add_existing_is_idempotent_and_does_not_write(self, store):
        bs.blocked_senders_add(bs.BlockSenderRequest(address="a@example.com"), mailbox=MAILBOX)
        puts = store.puts
        body = bs.blocked_senders_add(
            bs.BlockSenderRequest(address="A@EXAMPLE.com"), mailbox=MAILBOX
        )
        assert body["msg"] == "Sender is already blocked"
        assert body["data"]["addresses"] == ["a@example.com"]
        assert store.puts == puts

    def test_add_rejects_invalid_and_self(self, store):
        with pytest.raises(HTTPException) as info:
            bs.blocked_senders_add(bs.BlockSenderRequest(address="nope"), mailbox=MAILBOX)
        assert info.value.status_code == 422
        with pytest.raises(HTTPException) as info:
            bs.blocked_senders_add(
                bs.BlockSenderRequest(address="Jane@Example.com"), mailbox=MAILBOX
            )
        assert info.value.status_code == 422
        assert store.puts == 0

    def test_limit(self, store, monkeypatch):
        monkeypatch.setattr(bs, "MAX_BLOCKED_SENDERS", 2)
        bs.blocked_senders_add(bs.BlockSenderRequest(address="a@example.com"), mailbox=MAILBOX)
        bs.blocked_senders_add(bs.BlockSenderRequest(address="b@example.com"), mailbox=MAILBOX)
        with pytest.raises(HTTPException) as info:
            bs.blocked_senders_add(bs.BlockSenderRequest(address="c@example.com"), mailbox=MAILBOX)
        assert info.value.status_code == 422

    def test_remove(self, store):
        bs.blocked_senders_add(bs.BlockSenderRequest(address="a@example.com"), mailbox=MAILBOX)
        bs.blocked_senders_add(bs.BlockSenderRequest(address="b@example.com"), mailbox=MAILBOX)
        body = bs.blocked_senders_remove("A@example.com", mailbox=MAILBOX)
        assert body["data"]["addresses"] == ["b@example.com"]
        assert bs.parse_blocked_senders(store.scripts[bs.BLOCKED_SENDERS_SCRIPT])["addresses"] == [
            "b@example.com"
        ]

    def test_remove_last_leaves_comment_only_script(self, store):
        bs.blocked_senders_add(bs.BlockSenderRequest(address="a@example.com"), mailbox=MAILBOX)
        bs.blocked_senders_remove("a@example.com", mailbox=MAILBOX)
        assert "keep;" not in store.scripts[bs.BLOCKED_SENDERS_SCRIPT]
        assert bs.blocked_senders_show(mailbox=MAILBOX)["data"]["addresses"] == []

    def test_remove_unknown_is_idempotent(self, store):
        body = bs.blocked_senders_remove("ghost@example.com", mailbox=MAILBOX)
        assert body["msg"] == "Sender was not blocked"
        assert store.puts == 0

    def test_remove_invalid_is_422(self, store):
        with pytest.raises(HTTPException) as info:
            bs.blocked_senders_remove("not an address", mailbox=MAILBOX)
        assert info.value.status_code == 422

    def test_survives_rules_forwarding_vacation_round_trips(self, store):
        bs.blocked_senders_add(bs.BlockSenderRequest(address="a@example.com"), mailbox=MAILBOX)
        blocked = store.scripts[bs.BLOCKED_SENDERS_SCRIPT]

        mailbox_routes.rules_update(
            mailbox_routes.RulesRequest(
                rules=[
                    {
                        "name": "File newsletters",
                        "conditions": [
                            {"field": "subject", "operator": "contains", "value": "news"}
                        ],
                        "actions": [{"type": "move", "value": "Newsletters"}],
                    }
                ]
            ),
            mailbox=MAILBOX,
        )
        mailbox_routes.forwarding_update(
            mailbox_routes.ForwardingRequest(enabled=False, addresses=[], keep_copy=True),
            mailbox=MAILBOX,
        )
        mailbox_routes.vacation_update(
            mailbox_routes.VacationRequest(enabled=False), mailbox=MAILBOX
        )

        assert store.scripts[bs.BLOCKED_SENDERS_SCRIPT] == blocked
        assert set(store.scripts) == {
            bs.BLOCKED_SENDERS_SCRIPT,
            RULES_SCRIPT,
            FORWARDING_SCRIPT,
            VACATION_SCRIPT,
            MAIN_SCRIPT,
        }
        includes = [l for l in store.scripts[MAIN_SCRIPT].splitlines() if l.startswith("include ")]
        assert includes[0].endswith(f'"{bs.BLOCKED_SENDERS_SCRIPT}";')
        assert store.active == MAIN_SCRIPT
        assert bs.blocked_senders_show(mailbox=MAILBOX)["data"]["addresses"] == ["a@example.com"]
        assert (
            mailbox_routes.rules_show(mailbox=MAILBOX)["data"]["rules"][0]["name"]
            == "File newsletters"
        )


# ---------------------------------------------------------------------------
# Unavailability and the capability
# ---------------------------------------------------------------------------


class TestUnavailable:
    def test_unreachable_is_503_sieve_unavailable_and_turns_capability_off(
        self, store, monkeypatch
    ):
        probes = {"n": 0}

        def no_probe(**_kwargs):
            probes["n"] += 1
            return (True, "would have said yes")

        monkeypatch.setattr(ms, "probe_service", no_probe)
        store.raise_on_enter = ms.ManageSieveUnavailableError(
            "Cannot reach the Sieve service: [Errno 111] Connection refused"
        )

        with pytest.raises(HTTPException) as info:
            bs.blocked_senders_show(mailbox=MAILBOX)
        assert info.value.status_code == 503
        assert error_code(info.value) == "sieve_unavailable"

        with pytest.raises(HTTPException) as info:
            mailbox_routes.rules_show(mailbox=MAILBOX)
        assert info.value.status_code == 503
        assert error_code(info.value) == "sieve_unavailable"

        # The failure a real request saw is authoritative: capability off, no probe.
        assert mailbox_routes._sieve_configured() is False
        assert probes["n"] == 0

    def test_auth_failure_is_503_not_502(self, store):
        store.raise_on_enter = ms.ManageSieveUnavailableError(
            "Sieve authentication failed for jane@example.com: Authentication failed."
        )
        with pytest.raises(HTTPException) as info:
            mailbox_routes.vacation_show(mailbox=MAILBOX)
        assert info.value.status_code == 503
        assert error_code(info.value) == "sieve_unavailable"

    def test_write_unavailable_is_503(self, store):
        store.raise_on_enter = ms.ManageSieveUnavailableError(
            "Cannot reach the Sieve service: down"
        )
        with pytest.raises(HTTPException) as info:
            bs.blocked_senders_add(bs.BlockSenderRequest(address="a@example.com"), mailbox=MAILBOX)
        assert info.value.status_code == 503
        assert error_code(info.value) == "sieve_unavailable"

    def test_no_credentials_is_503_sieve_not_configured(self, store, monkeypatch):
        monkeypatch.delenv("IMAP_MASTER_USER")
        with pytest.raises(HTTPException) as info:
            bs.blocked_senders_show(mailbox=MAILBOX)
        assert info.value.status_code == 503
        assert error_code(info.value) == "sieve_not_configured"
        assert mailbox_routes._sieve_configured() is False

    def test_success_heals_capability(self, store, monkeypatch):
        monkeypatch.setattr(ms, "probe_service", lambda **_k: (False, "down"))
        assert mailbox_routes._sieve_configured() is False
        bs.blocked_senders_show(mailbox=MAILBOX)  # a real request got through
        assert mailbox_routes._sieve_configured() is True

    def test_compiler_refusal_is_400_with_diagnostic(self, store):
        store.raise_on_put = ms.ManageSieveError(
            "mailyte-rules: line 3: error: unknown command 'foo'."
        )
        with pytest.raises(HTTPException) as info:
            mailbox_routes.rules_update(mailbox_routes.RulesRequest(rules=[]), mailbox=MAILBOX)
        assert info.value.status_code == 400
        assert "line 3" in info.value.detail["msg"]

    def test_quota_code_is_400(self, store):
        store.raise_on_put = ms.ManageSieveError("Quota exceeded", code="QUOTA/MAXSCRIPTS")
        with pytest.raises(HTTPException) as info:
            bs.blocked_senders_add(bs.BlockSenderRequest(address="a@example.com"), mailbox=MAILBOX)
        assert info.value.status_code == 400

    def test_trylater_code_is_503(self, store):
        store.raise_on_put = ms.ManageSieveError("Try again later", code="TRYLATER")
        with pytest.raises(HTTPException) as info:
            bs.blocked_senders_add(bs.BlockSenderRequest(address="a@example.com"), mailbox=MAILBOX)
        assert info.value.status_code == 503
        assert error_code(info.value) == "sieve_unavailable"

    def test_unexplained_refusal_stays_502(self, store):
        store.raise_on_put = ms.ManageSieveError("Failed to store script")
        with pytest.raises(HTTPException) as info:
            bs.blocked_senders_add(bs.BlockSenderRequest(address="a@example.com"), mailbox=MAILBOX)
        assert info.value.status_code == 502


class TestCapability:
    def test_reflects_probe(self, monkeypatch):
        monkeypatch.setenv("IMAP_MASTER_USER", "jmap_master")
        monkeypatch.setenv("IMAP_MASTER_PASSWORD", "master-secret")
        ms.reset_availability()
        monkeypatch.setattr(ms, "probe_service", lambda **_k: (False, "Cannot reach"))
        assert mailbox_routes._sieve_configured() is False
        ms.reset_availability()
        monkeypatch.setattr(ms, "probe_service", lambda **_k: (True, "ok"))
        assert mailbox_routes._sieve_configured() is True
        ms.reset_availability()

    def test_false_without_credentials_and_never_probes(self, monkeypatch):
        monkeypatch.delenv("IMAP_MASTER_USER", raising=False)
        monkeypatch.delenv("IMAP_MASTER_PASSWORD", raising=False)
        ms.reset_availability()

        def boom(**_kwargs):
            raise AssertionError("must not probe without a credential")

        monkeypatch.setattr(ms, "probe_service", boom)
        assert mailbox_routes._sieve_configured() is False
