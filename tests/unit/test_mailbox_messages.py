#!/usr/bin/env python3
"""
Unit tests for the mailbox message-surface additions filed by the mobile team
on 2026-08-31 (docs/api/mailbox.md carries the wire contract):

* threading: in_reply_to / references pass through unfolded, and thread_id is
  derived the same way in the list shaper and the detail shaper (root of the
  References chain, else In-Reply-To, else the message's own id);
* provenance on the detail view: mailed_by / signed_by / security /
  list_unsubscribe / authentication, always present and null when absent;
* folder guards: INBOX and special-use folders cannot be renamed or deleted,
  non-empty folders cannot be deleted, and the routes emit the documented
  409 error_codes;
* forwarded attachments: extract_attachment exposes content_id, and
  build_message re-attaches inline parts under their original cid;
* the tracking opt-out header: the send path writes X-Mailyte-Tracking: off
  by default, and the Postfix injector honours it, strips it, and never lets
  it leak through the raw-bytes fallback path.

Everything runs against synthetic messages and fake IMAP connections -- no
live Dovecot, no live Postfix.
"""

import importlib.util
import io
import sys
from email.parser import BytesParser
from email.policy import default as default_policy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "worker" / "api"))

from fastapi import HTTPException  # noqa: E402

from shared.imap_mail import (  # noqa: E402
    FolderNotEmptyError,
    FolderProtectedError,
    build_message,
    delete_folder,
    extract_attachment,
    fetch_summaries,
    folder_id,
    is_protected_folder,
    mark_answered,
    message_bytes,
    parse_message,
    raw_download_name,
    rename_folder,
    thread_root,
)

# ---------------------------------------------------------------------------
# Synthetic messages
# ---------------------------------------------------------------------------

REPLY_HEADERS = (
    b"Subject: Re: hello\r\n"
    b"From: Ada <ada@example.org>\r\n"
    b"To: bob@example.net\r\n"
    b"Date: Mon, 31 Aug 2026 10:00:00 +0000\r\n"
    b"Message-ID: <m2@example.org>\r\n"
    b"In-Reply-To: <m1@example.net>\r\n"
    # Folded the way Gmail folds it -- the value must come back unfolded.
    b"References: <m0@example.net>\r\n\t<m1@example.net>\r\n"
)

FULL_RAW = (
    b"Return-Path: <bounce+tok@mailer.example.com>\r\n"
    # Dovecot's own LMTP hop sits on top and says nothing about the wire.
    b"Received: from mail.mailyte.com ([127.0.0.1]) by mail.mailyte.com"
    b" with LMTP id L1; Mon, 31 Aug 2026 10:00:01 +0000\r\n"
    b"Received: from mail-ej1.google.com (mail-ej1.google.com [209.85.218.52])\r\n"
    b"\t(using TLSv1.3 with cipher TLS_AES_256_GCM_SHA384 (256/256 bits))\r\n"
    b"\tby mail.mailyte.com (Postfix) with ESMTPS id 4X;"
    b" Mon, 31 Aug 2026 10:00:00 +0000\r\n"
    b"Authentication-Results: mail.mailyte.com;\r\n"
    b"\tdkim=pass header.d=example.org header.s=s1 header.b=abc;\r\n"
    b"\tdmarc=pass (policy=none) header.from=example.org;\r\n"
    b"\tspf=softfail smtp.mailfrom=bounce+tok@mailer.example.com\r\n"
    b"DKIM-Signature: v=1; a=rsa-sha256; d=mailer.example.com; s=k1; bh=x; b=y\r\n"
    b"DKIM-Signature: v=1; a=rsa-sha256; d=example.org; s=s1; bh=x; b=y\r\n"
    b"List-Unsubscribe: <mailto:unsub@example.org?subject=x>,\r\n"
    b" <https://example.org/u/1>\r\n"
    b"List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n" + REPLY_HEADERS + b"\r\nhi there\r\n"
)

MINIMAL_RAW = b"Subject: bare\r\nFrom: a@x.com\r\nTo: b@y.com\r\n\r\nbody\r\n"


def _summary_fetch_data(uid: bytes, headers: bytes, flags: bytes = b"\\Seen") -> list:
    """One message's FETCH response, shaped the way imaplib hands it over."""
    text = b"hi there\r\n"
    prefix = (
        b"1 (UID " + uid + b" FLAGS (" + flags + b") RFC822.SIZE 100 "
        b'BODYSTRUCTURE ("text" "plain" ("charset" "utf-8") NIL NIL "7bit" 10 1'
        b" NIL NIL NIL NIL) "
        b"BODY[HEADER.FIELDS (SUBJECT FROM TO CC BCC REPLY-TO DATE MESSAGE-ID"
        b" IN-REPLY-TO REFERENCES)] {%d}" % len(headers)
    )
    return [(prefix, headers), (b" BODY[TEXT]<0> {%d}" % len(text), text), b")"]


class SummaryConn:
    def __init__(self, data):
        self._data = data

    def select(self, name, readonly=False):
        return "OK", [b"1"]

    def uid(self, command, *args):
        assert command == "FETCH"
        return "OK", self._data


# ---------------------------------------------------------------------------
# A. Threading identifiers and thread_id
# ---------------------------------------------------------------------------


class TestThreading:
    def test_summary_passes_reply_headers_through_unfolded(self):
        conn = SummaryConn(_summary_fetch_data(b"5", REPLY_HEADERS + b"\r\n"))
        row = fetch_summaries(conn, "INBOX", ["5"])[0]
        assert row["message_id"] == "<m2@example.org>"
        assert row["in_reply_to"] == "<m1@example.net>"
        # Unfolded: no CRLF or tab survives into the wire value.
        assert row["references"] == "<m0@example.net> <m1@example.net>"
        assert row["thread_id"] == "<m0@example.net>"
        assert row["is_answered"] is False

    def test_answered_flag_reaches_is_answered(self):
        conn = SummaryConn(
            _summary_fetch_data(b"5", REPLY_HEADERS + b"\r\n", flags=b"\\Seen \\Answered")
        )
        assert fetch_summaries(conn, "INBOX", ["5"])[0]["is_answered"] is True

    def test_detail_and_summary_agree_on_thread_id(self):
        detail = parse_message(FULL_RAW, "INBOX", "5", set())
        conn = SummaryConn(_summary_fetch_data(b"5", REPLY_HEADERS + b"\r\n"))
        row = fetch_summaries(conn, "INBOX", ["5"])[0]
        assert detail["thread_id"] == row["thread_id"] == "<m0@example.net>"
        assert detail["in_reply_to"] == row["in_reply_to"]

    def test_thread_root_precedence(self):
        # References' FIRST token is the root...
        assert thread_root("<c@x>", "<b@x>", "<a@x> <b@x>") == "<a@x>"
        # ...else In-Reply-To...
        assert thread_root("<c@x>", "<b@x>", None) == "<b@x>"
        # ...else the message roots its own thread...
        assert thread_root("<c@x>", None, None) == "<c@x>"
        # ...and with no identifier at all there is nothing to group on.
        assert thread_root(None, None, None) is None

    def test_message_with_no_reply_headers_roots_itself(self):
        detail = parse_message(MINIMAL_RAW, "INBOX", "1", set())
        assert detail["in_reply_to"] is None
        assert detail["references"] is None
        assert detail["thread_id"] is None  # no Message-ID either


# ---------------------------------------------------------------------------
# B. Provenance on the detail view
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_full_message(self):
        d = parse_message(FULL_RAW, "INBOX", "5", set())
        assert d["mailed_by"] == "mailer.example.com"
        # Two signatures; the one Authentication-Results verified wins.
        assert d["signed_by"] == "example.org"
        # The LMTP hop is skipped; the real receiving hop used ESMTPS.
        assert d["security"] == "tls"
        assert d["list_unsubscribe"] == {
            "mailto": "mailto:unsub@example.org?subject=x",
            "url": "https://example.org/u/1",
            "one_click": True,
        }
        assert d["authentication"] == {"spf": "softfail", "dkim": "pass", "dmarc": "pass"}

    def test_absent_headers_are_null_not_omitted(self):
        d = parse_message(MINIMAL_RAW, "INBOX", "1", set())
        assert d["mailed_by"] is None
        assert d["signed_by"] is None
        assert d["security"] is None  # never received -- e.g. the Sent copy
        assert d["list_unsubscribe"] is None
        assert d["authentication"] == {"spf": None, "dkim": None, "dmarc": None}

    def test_plaintext_hop_reports_none(self):
        raw = (
            b"Received: from other.example.com (other.example.com [1.2.3.4])"
            b" by mail.mailyte.com (Postfix) with ESMTP id 9Z;"
            b" Mon, 31 Aug 2026 10:00:00 +0000\r\n" + MINIMAL_RAW
        )
        assert parse_message(raw, "INBOX", "1", set())["security"] == "none"

    def test_unknown_verdict_becomes_null(self):
        raw = (
            b"Authentication-Results: mail.mailyte.com; dkim=bestguesspass"
            b" header.d=x.com; spf=pass smtp.mailfrom=a@x.com\r\n" + MINIMAL_RAW
        )
        d = parse_message(raw, "INBOX", "1", set())
        assert d["authentication"]["dkim"] is None
        assert d["authentication"]["spf"] == "pass"

    def test_null_return_path_has_no_domain(self):
        raw = b"Return-Path: <>\r\n" + MINIMAL_RAW
        assert parse_message(raw, "INBOX", "1", set())["mailed_by"] is None

    def test_one_click_requires_post_header(self):
        raw = b"List-Unsubscribe: <https://example.org/u/1>\r\n" + MINIMAL_RAW
        unsub = parse_message(raw, "INBOX", "1", set())["list_unsubscribe"]
        assert unsub["url"] == "https://example.org/u/1"
        assert unsub["one_click"] is False


# ---------------------------------------------------------------------------
# C. Raw download filename
# ---------------------------------------------------------------------------


class TestRawDownloadName:
    def test_subject_is_reduced_to_safe_ascii(self):
        raw = b'Subject: Invoice #42: "Q3/2026" \xe2\x82\xac\r\n\r\nx'
        name = raw_download_name(raw, "7")
        assert name.endswith("-7.eml")
        assert all(c.isalnum() or c in "._-" for c in name[: -len(".eml")])
        assert "Invoice" in name

    def test_missing_subject_still_names_the_file(self):
        assert raw_download_name(b"From: a@x.com\r\n\r\nx", "12") == "message-12.eml"

    def test_path_tricks_do_not_survive(self):
        raw = b"Subject: ../../etc/passwd\r\n\r\nx"
        name = raw_download_name(raw, "3")
        assert "/" not in name and ".." not in name


# ---------------------------------------------------------------------------
# D. Folder guards
# ---------------------------------------------------------------------------


class FolderConn:
    """A fake IMAP connection for the LIST/STATUS/RENAME/DELETE surface."""

    def __init__(self, rows, counts=None):
        # rows: (flags, name) pairs, delimiter always "/"
        self._rows = [
            (f"({flags})" + ' "/" ' + (f'"{name}"' if " " in name else name)).encode()
            for flags, name in rows
        ]
        self._counts = counts or {}
        self.renamed = []
        self.deleted = []
        self.subscribed = []
        self.unsubscribed = []

    def list(self, *args):
        if args:  # the delimiter probe: LIST "" ""
            return "OK", [b'(\\Noselect) "/" ""']
        return "OK", list(self._rows)

    def status(self, name, spec):
        key = name.strip('"')
        return "OK", [f'"{key}" (MESSAGES {self._counts.get(key, 0)})'.encode()]

    def rename(self, old, new):
        self.renamed.append((old.strip('"'), new.strip('"')))
        return "OK", [b"Rename completed"]

    def delete(self, name):
        self.deleted.append(name.strip('"'))
        return "OK", [b"Delete completed"]

    def subscribe(self, name):
        self.subscribed.append(name.strip('"'))
        return "OK", [b""]

    def unsubscribe(self, name):
        self.unsubscribed.append(name.strip('"'))
        return "OK", [b""]


BASIC_ROWS = [
    ("\\HasNoChildren", "INBOX"),
    ("\\HasNoChildren \\Sent", "Sent"),
    ("\\HasNoChildren \\Sent", "Sent Messages"),
    ("\\HasNoChildren \\Trash", "Trash"),
    ("\\HasChildren", "Clients"),
    ("\\HasNoChildren", "Clients/Acme"),
    ("\\HasNoChildren", "Receipts"),
]


class TestFolderGuards:
    def test_protection_matrix(self):
        assert is_protected_folder("INBOX")
        assert is_protected_folder("inbox")
        assert is_protected_folder("Sent Messages")  # well-known name, no flag
        assert is_protected_folder("Anything", {"\\sent"})  # flag, odd name
        assert not is_protected_folder("Projects/Archive")  # only the leaf matches
        assert not is_protected_folder("Receipts")

    def test_rename_refuses_special_folders(self):
        conn = FolderConn(BASIC_ROWS)
        for name in ("INBOX", "Sent", "Sent Messages"):
            with pytest.raises(FolderProtectedError):
                rename_folder(conn, name, "Whatever")
        assert conn.renamed == []

    def test_rename_keeps_parent_and_resubscribes(self):
        conn = FolderConn(BASIC_ROWS)
        assert rename_folder(conn, "Clients/Acme", "Acme Ltd") == "Clients/Acme Ltd"
        assert conn.renamed == [("Clients/Acme", "Clients/Acme Ltd")]
        assert conn.unsubscribed == ["Clients/Acme"]
        assert conn.subscribed == ["Clients/Acme Ltd"]

    def test_rename_to_existing_or_reserved_name_refused(self):
        conn = FolderConn(BASIC_ROWS)
        with pytest.raises(ValueError):
            rename_folder(conn, "Receipts", "Trash")
        with pytest.raises(ValueError):
            rename_folder(conn, "Receipts", "Clients")

    def test_delete_refuses_special_folders(self):
        conn = FolderConn(BASIC_ROWS)
        with pytest.raises(FolderProtectedError):
            delete_folder(conn, "Trash")
        assert conn.deleted == []

    def test_delete_refuses_messages_and_children(self):
        conn = FolderConn(BASIC_ROWS, counts={"Receipts": 3})
        with pytest.raises(FolderNotEmptyError) as excinfo:
            delete_folder(conn, "Receipts")
        assert excinfo.value.reason == "messages"

        with pytest.raises(FolderNotEmptyError) as excinfo:
            delete_folder(conn, "Clients")
        assert excinfo.value.reason == "children"
        assert conn.deleted == []

    def test_delete_empty_folder_unsubscribes_first(self):
        conn = FolderConn(BASIC_ROWS)
        delete_folder(conn, "Receipts")
        assert conn.unsubscribed == ["Receipts"]
        assert conn.deleted == ["Receipts"]


class _SessionStub:
    def __init__(self, conn):
        self._conn = conn

    def __call__(self, email):
        return self

    def __enter__(self):
        return self._conn

    def __exit__(self, *args):
        return False


class TestFolderRoutes:
    """The 409 error_code contract the mobile client keys its warnings off."""

    def _routes(self):
        import routes.mailbox as mailbox_routes

        return mailbox_routes

    def test_delete_special_folder_emits_error_code(self):
        mailbox_routes = self._routes()
        conn = FolderConn(BASIC_ROWS)
        with (
            patch.object(mailbox_routes, "imap_session", _SessionStub(conn)),
            pytest.raises(HTTPException) as excinfo,
        ):
            mailbox_routes.delete_folder_route(
                folder=folder_id("Sent"), mailbox={"email": "a@x.com"}
            )
        assert excinfo.value.status_code == 409
        assert excinfo.value.detail["error_code"] == "cannot_delete_special_folder"

    def test_delete_non_empty_folder_emits_error_code(self):
        mailbox_routes = self._routes()
        conn = FolderConn(BASIC_ROWS, counts={"Receipts": 2})
        with (
            patch.object(mailbox_routes, "imap_session", _SessionStub(conn)),
            pytest.raises(HTTPException) as excinfo,
        ):
            mailbox_routes.delete_folder_route(
                folder=folder_id("Receipts"), mailbox={"email": "a@x.com"}
            )
        assert excinfo.value.status_code == 409
        assert excinfo.value.detail["error_code"] == "folder_not_empty"

    def test_rename_special_folder_emits_error_code(self):
        mailbox_routes = self._routes()
        conn = FolderConn(BASIC_ROWS)
        with (
            patch.object(mailbox_routes, "imap_session", _SessionStub(conn)),
            pytest.raises(HTTPException) as excinfo,
        ):
            mailbox_routes.rename_folder_route(
                folder=folder_id("INBOX"),
                body=mailbox_routes.RenameFolderRequest(name="Mail"),
                mailbox={"email": "a@x.com"},
            )
        assert excinfo.value.status_code == 409
        assert excinfo.value.detail["error_code"] == "cannot_rename_special_folder"

    def test_unknown_folder_is_404(self):
        mailbox_routes = self._routes()
        conn = FolderConn(BASIC_ROWS)
        with (
            patch.object(mailbox_routes, "imap_session", _SessionStub(conn)),
            pytest.raises(HTTPException) as excinfo,
        ):
            mailbox_routes.delete_folder_route(folder="doesnotexist", mailbox={"email": "a@x.com"})
        assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# E. Forwarded attachments
# ---------------------------------------------------------------------------


def _message_with_parts() -> bytes:
    msg = build_message(
        from_address="a@x.com",
        to=["b@y.com"],
        subject="original",
        body_text="t",
        body_html='<p><img src="cid:pic1@x"></p>',
        inline_parts=[
            {"cid": "pic1@x", "type": "image/png", "name": "pic.png", "content": b"\x89PNGdata"}
        ],
        attachments=[{"name": "doc.pdf", "type": "application/pdf", "content": b"%PDF-1.4"}],
    )
    return message_bytes(msg)


class TestForwardedAttachments:
    def _indexed_parts(self, raw):
        parsed = BytesParser().parsebytes(raw)
        found = {}
        for index, _part in enumerate(parsed.walk()):
            extracted = extract_attachment(raw, index)
            if extracted:
                found[extracted["name"]] = (index, extracted)
        return found

    def test_extract_attachment_reports_content_id(self):
        parts = self._indexed_parts(_message_with_parts())
        _, inline = parts["pic.png"]
        assert inline["content_id"] == "pic1@x"
        assert inline["is_inline"] is True
        assert inline["content"] == b"\x89PNGdata"
        _, attachment = parts["doc.pdf"]
        assert attachment["content_id"] is None
        assert attachment["is_inline"] is False

    def test_build_message_reattaches_inline_under_same_cid(self):
        raw = _message_with_parts()
        parsed = BytesParser(policy=default_policy).parsebytes(raw)
        related = [p for p in parsed.walk() if p.get_content_type() == "image/png"]
        assert related and related[0]["Content-ID"] == "<pic1@x>"
        # The image sits inside multipart/related next to the HTML, so a
        # recipient's client renders it where the original showed it.
        structure = [p.get_content_type() for p in parsed.walk()]
        assert "multipart/related" in structure

    def test_inline_parts_without_html_become_attachments(self):
        msg = build_message(
            from_address="a@x.com",
            to=["b@y.com"],
            body_text="plain only",
            inline_parts=[
                {"cid": "pic1@x", "type": "image/png", "name": "pic.png", "content": b"IMG"}
            ],
        )
        parsed = BytesParser(policy=default_policy).parsebytes(message_bytes(msg))
        dispositions = [p.get_content_disposition() for p in parsed.walk()]
        assert "attachment" in dispositions  # carried, not dropped

    def test_parse_indexes_accepts_ints_lists_and_csv(self):
        import routes.mailbox as mailbox_routes

        assert mailbox_routes._parse_indexes(["1,3", "5"]) == [1, 3, 5]
        assert mailbox_routes._parse_indexes(["3", "3"]) == [3]  # de-duplicated
        with pytest.raises(HTTPException):
            mailbox_routes._parse_indexes(["two"])

    def test_json_form_getlist_accepts_integers(self):
        import routes.mailbox as mailbox_routes

        form = mailbox_routes._JsonForm({"attachment_indexes": [3, 5], "flag": True})
        assert form.getlist("attachment_indexes") == ["3", "5"]
        assert form.getlist("flag") == []  # bool is not an index


# ---------------------------------------------------------------------------
# A (send side): the \Answered flag
# ---------------------------------------------------------------------------


class AnsweredConn:
    def __init__(self, hits):
        # hits: folder -> uid answered for a Message-ID search
        self._hits = hits
        self.stored = []
        self._selected = None

    def select(self, name, readonly=False):
        self._selected = name.strip('"')
        return "OK", [b"1"]

    def uid(self, command, *args):
        if command == "STORE":
            self.stored.append((self._selected, args[0]))
            return "OK", [b""]
        assert command == "SEARCH"
        uid = self._hits.get(self._selected)
        if "UID" in args:  # the hint-verification search
            wanted = args[args.index("UID") + 1]
            return "OK", [uid.encode() if uid == wanted else b""]
        return "OK", [uid.encode() if uid else b""]


class TestMarkAnswered:
    def test_found_by_search_and_flagged(self):
        conn = AnsweredConn({"INBOX": "41"})
        result = mark_answered(conn, "<m1@example.net>", ["INBOX", "Archive"])
        assert result == "INBOX:41"
        assert conn.stored == [("INBOX", "41")]

    def test_verified_hint_short_circuits(self):
        conn = AnsweredConn({"Archive": "9"})
        result = mark_answered(conn, "<m1@x>", ["INBOX"], hint=("Archive", "9"))
        assert result == "Archive:9"
        assert conn.stored == [("Archive", "9")]

    def test_wrong_hint_falls_back_to_search(self):
        conn = AnsweredConn({"INBOX": "41"})
        result = mark_answered(conn, "<m1@x>", ["INBOX"], hint=("Archive", "999"))
        assert result == "INBOX:41"

    def test_missing_message_answers_none(self):
        conn = AnsweredConn({})
        assert mark_answered(conn, "<gone@x>", ["INBOX"]) is None
        assert conn.stored == []

    def test_blank_id_is_a_no_op(self):
        conn = AnsweredConn({"INBOX": "41"})
        assert mark_answered(conn, "  ", ["INBOX"]) is None


# ---------------------------------------------------------------------------
# F. Tracking opt-out: the send path and the Postfix injector
# ---------------------------------------------------------------------------

_INJECTOR = None


def _load_injector():
    """Import the injector once, with its file logging neutralised.

    The module configures a FileHandler on /var/log at import time, which
    does not exist on a dev machine or in CI; tests only need the code.
    """
    global _INJECTOR
    if _INJECTOR is not None:
        return _INJECTOR
    import atexit
    import logging

    real_file_handler = logging.FileHandler
    real_register = atexit.register
    logging.FileHandler = lambda *a, **k: logging.NullHandler()
    atexit.register = lambda *a, **k: None
    try:
        spec = importlib.util.spec_from_file_location(
            "tracking_injector_under_test",
            project_root / "mailer" / "postfix" / "scripts" / "tracking_injector.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        logging.FileHandler = real_file_handler
        atexit.register = real_register
    _INJECTOR = module
    return module


def _raw_mail(opt_out: bool) -> bytes:
    lines = [
        "From: holder@example.org",
        "To: friend@example.net",
        "Subject: lunch?",
        "MIME-Version: 1.0",
        'Content-Type: text/html; charset="utf-8"',
    ]
    if opt_out:
        lines.append("X-Mailyte-Tracking: off")
    return ("\r\n".join(lines) + "\r\n\r\n<html><body><p>hello</p></body></html>\r\n").encode()


def _run_injector(raw: bytes):
    module = _load_injector()
    injector = module.PostfixTrackingInjector()
    injector.cli_sender = "holder@example.org"
    injector.cli_recipients = ["friend@example.net"]
    delivered = []

    injector._setup_signal_handlers = lambda: None
    injector._check_delivery_timing = lambda *a: True
    injector._record_delivery_attempt = lambda *a: None
    injector.get_tenant_domain_ids = lambda *a: ("org1", "dom1")
    injector._store_email_body = lambda *a: None
    injector._archive_message = lambda *a: None
    injector.inject_tracking_for_recipient = lambda html, *a: html + "<!--PIXEL-->"
    injector._reinject_email = lambda email_bytes, metadata: delivered.append(email_bytes)

    real_stdin = sys.stdin
    sys.stdin = SimpleNamespace(buffer=io.BytesIO(raw))
    try:
        injector.process_email()
    finally:
        sys.stdin = real_stdin

    assert len(delivered) == 1
    return delivered[0]


class TestTrackingOptOut:
    def test_header_name_matches_between_sender_and_injector(self):
        import routes.mailbox as mailbox_routes

        module = _load_injector()
        assert mailbox_routes.TRACKING_OPT_OUT_HEADER == module.TRACKING_OPT_OUT_HEADER

    def test_mailbox_sends_opt_out_by_default(self, monkeypatch):
        import routes.mailbox as mailbox_routes

        monkeypatch.delenv("MAILBOX_SEND_TRACKING", raising=False)
        assert mailbox_routes._mailbox_send_tracking_enabled() is False
        monkeypatch.setenv("MAILBOX_SEND_TRACKING", "true")
        assert mailbox_routes._mailbox_send_tracking_enabled() is True

    def test_opted_out_message_is_untracked_and_header_stripped(self):
        out = _run_injector(_raw_mail(opt_out=True))
        assert b"PIXEL" not in out
        assert b"X-Mailyte-Tracking" not in out
        assert b"X-Mailyte-ID" in out  # the trace id still rides along

    def test_normal_message_is_still_tracked(self):
        out = _run_injector(_raw_mail(opt_out=False))
        assert b"PIXEL" in out
        assert b"X-Mailyte-ID" in out

    def test_raw_fallback_strip_removes_header_only(self):
        module = _load_injector()
        raw = _raw_mail(opt_out=True)
        stripped = module._strip_opt_out_header(raw)
        assert b"X-Mailyte-Tracking" not in stripped
        assert b"<p>hello</p>" in stripped
        assert b"Subject: lunch?" in stripped
        # A body mentioning the header name is not a header and is kept.
        body_mention = MINIMAL_RAW + b"X-Mailyte-Tracking: off\r\n"
        assert module._strip_opt_out_header(body_mention) == body_mention

    def test_strip_survives_folded_header_and_garbage(self):
        module = _load_injector()
        folded = (
            b"From: a@x.com\r\nX-Mailyte-Tracking: off\r\n\tby-accident-folded\r\n"
            b"To: b@y.com\r\n\r\nbody"
        )
        stripped = module._strip_opt_out_header(folded)
        assert b"X-Mailyte-Tracking" not in stripped
        assert b"by-accident-folded" not in stripped
        assert stripped.startswith(b"From: a@x.com\r\nTo: b@y.com")
        # No header/body split at all: returned untouched, never an exception.
        assert module._strip_opt_out_header(b"no blank line") == b"no blank line"
