#!/usr/bin/env python3
"""
Unit tests for mailer/dovecot/scripts/shared-mailbox-acl-sync.py.

This script is the only thing standing between "a permission level was chosen
in the dashboard" and "Dovecot enforces it". Before it existed, shared
mailboxes accepted mail into a Maildir nobody could open and the four
permission levels enforced nothing at all.

The rights strings are the security boundary, so they are asserted literally
rather than through the mapping the code itself uses -- a test that reads
RIGHTS to check RIGHTS would pass no matter what it said.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent.parent


def _load_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, project_root / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


acl_sync = _load_module(
    "shared_mailbox_acl_sync",
    "mailer/dovecot/scripts/shared-mailbox-acl-sync.py",
)


class FakeCursor:
    """Answers the two queries the syncer runs, in the order it runs them."""

    def __init__(self, members, shared):
        self._members = members
        self._shared = shared
        self._pending = None

    def execute(self, query):
        # The membership query is the one that joins the pivot table; the
        # other enumerates shared mailboxes so empty ones can be cleaned up.
        self._pending = "members" if "shared_mailbox_members" in query else "shared"

    def fetchall(self):
        if self._pending == "shared":
            return [{"email": email} for email in self._shared]
        return [
            {"shared_email": s, "member_email": m, "permission": p} for s, m, p in self._members
        ]


class TestRightsMapping:
    """The rights letters are the whole security model. Assert them literally."""

    def test_full_access_can_read_write_and_delete(self):
        assert acl_sync.RIGHTS["full_access"] == "lrwstipekxa"

    def test_read_only_cannot_write_anything(self):
        rights = acl_sync.RIGHTS["read_only"]

        assert rights == "lr"
        # Especially not `s`: \Seen is SHARED between members, so an observer
        # with write-seen could mark the team's queue as read.
        for forbidden in "wstipekxa":
            assert forbidden not in rights

    def test_send_levels_can_work_the_queue_but_not_destroy_it(self):
        for level in ("send_as", "send_on_behalf"):
            rights = acl_sync.RIGHTS[level]

            assert "r" in rights  # the guide says Send as can read and reply
            assert "e" not in rights  # no expunge
            assert "x" not in rights  # no deleting the mailbox
            assert "a" not in rights  # no rewriting the ACL

    def test_send_as_and_send_on_behalf_have_identical_mailbox_rights(self):
        """They differ only in the From header at submission, not in access."""
        assert acl_sync.RIGHTS["send_as"] == acl_sync.RIGHTS["send_on_behalf"]


class TestDesiredAcl:
    def test_one_line_per_member_in_dovecot_format(self):
        body = acl_sync.desired_acl(
            [("agent@example.com", "full_access"), ("boss@example.com", "read_only")]
        )

        assert "user=agent@example.com lrwstipekxa" in body
        assert "user=boss@example.com lr" in body
        assert body.endswith("\n")

    def test_an_unknown_permission_is_skipped_not_guessed(self):
        """A level we don't recognise must never become access."""
        body = acl_sync.desired_acl(
            [("agent@example.com", "superuser"), ("real@example.com", "read_only")]
        )

        assert "agent@example.com" not in body
        assert "user=real@example.com lr" in body


class TestMaildirPath:
    def test_resolves_to_the_path_user_query_hands_dovecot(self):
        assert acl_sync.maildir_for("sales@mailyte.com").endswith("/mailyte.com/sales")

    @pytest.mark.parametrize(
        "bad", ["notanemail", "", "@example.com", "user@", "../../etc/passwd@x.com"]
    )
    def test_refuses_anything_that_is_not_a_plain_address(self, bad):
        assert acl_sync.maildir_for(bad) is None


class TestSyncOnce:
    def _maildir(self, tmp_path, email):
        local, domain = email.split("@")
        path = tmp_path / domain / local
        path.mkdir(parents=True)
        return path

    def test_writes_the_acl_file_into_the_shared_maildir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(acl_sync, "VHOSTS", str(tmp_path))
        maildir = self._maildir(tmp_path, "sales@mailyte.com")

        cursor = FakeCursor(
            members=[("sales@mailyte.com", "agent@techies.africa", "full_access")],
            shared=["sales@mailyte.com"],
        )
        changed = acl_sync.sync_once(cursor)

        written = (maildir / "dovecot-acl").read_text()
        assert changed == 1
        assert "user=agent@techies.africa lrwstipekxa" in written

    def test_a_second_pass_with_no_changes_rewrites_nothing(self, tmp_path, monkeypatch):
        """Steady state must be pure reads -- this polls every few seconds."""
        monkeypatch.setattr(acl_sync, "VHOSTS", str(tmp_path))
        maildir = self._maildir(tmp_path, "sales@mailyte.com")

        def run():
            return acl_sync.sync_once(
                FakeCursor(
                    members=[("sales@mailyte.com", "agent@techies.africa", "full_access")],
                    shared=["sales@mailyte.com"],
                )
            )

        run()
        before = (maildir / "dovecot-acl").stat().st_mtime_ns

        assert run() == 0
        assert (maildir / "dovecot-acl").stat().st_mtime_ns == before

    def test_removing_the_last_member_removes_the_file(self, tmp_path, monkeypatch):
        """acl_defaults_from_inbox makes a stale INBOX file grant rights on
        every folder beneath it, so it has to go, not just be emptied."""
        monkeypatch.setattr(acl_sync, "VHOSTS", str(tmp_path))
        maildir = self._maildir(tmp_path, "sales@mailyte.com")

        acl_sync.sync_once(
            FakeCursor(
                members=[("sales@mailyte.com", "agent@techies.africa", "full_access")],
                shared=["sales@mailyte.com"],
            )
        )
        assert (maildir / "dovecot-acl").exists()

        changed = acl_sync.sync_once(FakeCursor(members=[], shared=["sales@mailyte.com"]))

        assert changed == 1
        assert not (maildir / "dovecot-acl").exists()

    def test_revoked_member_disappears_from_the_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(acl_sync, "VHOSTS", str(tmp_path))
        maildir = self._maildir(tmp_path, "sales@mailyte.com")

        acl_sync.sync_once(
            FakeCursor(
                members=[
                    ("sales@mailyte.com", "agent@techies.africa", "full_access"),
                    ("sales@mailyte.com", "leaver@techies.africa", "full_access"),
                ],
                shared=["sales@mailyte.com"],
            )
        )
        acl_sync.sync_once(
            FakeCursor(
                members=[("sales@mailyte.com", "agent@techies.africa", "full_access")],
                shared=["sales@mailyte.com"],
            )
        )

        written = (maildir / "dovecot-acl").read_text()
        assert "leaver@techies.africa" not in written
        assert "agent@techies.africa" in written

    def test_a_new_shared_mailbox_gets_its_maildir_so_members_can_open_it(
        self, tmp_path, monkeypatch
    ):
        """Otherwise a new shared mailbox is unreachable until its first
        message and cannot bootstrap out of it: the ACL file lives inside the
        Maildir, and with no ACL there are no rights to open it with."""
        monkeypatch.setattr(acl_sync, "VHOSTS", str(tmp_path))
        (tmp_path / "mailyte.com").mkdir()

        changed = acl_sync.sync_once(
            FakeCursor(
                members=[("sales@mailyte.com", "agent@techies.africa", "full_access")],
                shared=["sales@mailyte.com"],
            )
        )

        maildir = tmp_path / "mailyte.com" / "sales"
        assert changed == 1
        for subdir in ("cur", "new", "tmp"):
            assert (maildir / subdir).is_dir()
        assert "user=agent@techies.africa" in (maildir / "dovecot-acl").read_text()

    def test_a_shared_mailbox_with_no_members_gets_no_maildir(self, tmp_path, monkeypatch):
        """Nothing to grant means nothing to create -- delivery still makes it
        the ordinary way."""
        monkeypatch.setattr(acl_sync, "VHOSTS", str(tmp_path))

        changed = acl_sync.sync_once(FakeCursor(members=[], shared=["sales@mailyte.com"]))

        assert changed == 0
        assert not (tmp_path / "mailyte.com").exists()

    def test_no_temp_files_are_left_behind(self, tmp_path, monkeypatch):
        """A stray .tmp beside dovecot-acl would be harmless but a leak."""
        monkeypatch.setattr(acl_sync, "VHOSTS", str(tmp_path))
        maildir = self._maildir(tmp_path, "sales@mailyte.com")

        acl_sync.sync_once(
            FakeCursor(
                members=[("sales@mailyte.com", "agent@techies.africa", "full_access")],
                shared=["sales@mailyte.com"],
            )
        )

        assert [p.name for p in maildir.iterdir()] == ["dovecot-acl"]

    def test_acl_file_is_not_world_readable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(acl_sync, "VHOSTS", str(tmp_path))
        maildir = self._maildir(tmp_path, "sales@mailyte.com")

        acl_sync.sync_once(
            FakeCursor(
                members=[("sales@mailyte.com", "agent@techies.africa", "full_access")],
                shared=["sales@mailyte.com"],
            )
        )

        mode = os.stat(maildir / "dovecot-acl").st_mode & 0o777
        assert mode == 0o600
