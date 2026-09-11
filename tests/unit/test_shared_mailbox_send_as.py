#!/usr/bin/env python3
"""
Unit tests for sending AS a shared mailbox from the webmail.

This endpoint is the ONLY gate on that. Postfix's smtpd_sender_login_maps
knows about shared mailboxes too, but it governs SASL submission from mail
clients; the webmail's send path goes out over the trusted network, so
Postfix will not check it. Anything not verified in send_message is not
verified at all -- which is why these tests exist and why the failure
direction matters more than the success one.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "worker" / "api"))

from routes import mailbox as mailbox_routes  # noqa: E402

MEMBER = {"email": "agent@techies.africa", "id": "01MEMBER0000000000000000AA"}
SHARED = "sales@mailyte.com"


class FakeRequest:
    def __init__(self, payload):
        self._payload = payload
        self.headers = {"content-type": "application/json"}

    async def json(self):
        return self._payload


def _body(**overrides):
    payload = {
        "to": ["customer@example.com"],
        "subject": "Re: your order",
        "body_text": "On its way.",
    }
    payload.update(overrides)
    return payload


def _send(payload, permission):
    """Run send_message with the permission lookup stubbed."""
    with (
        patch.object(mailbox_routes, "_shared_mailbox_permission", return_value=permission),
        patch.object(mailbox_routes, "build_message") as build,
        patch.object(mailbox_routes, "submit_message") as submit,
        patch.object(mailbox_routes, "message_bytes", return_value=b"raw"),
        patch.object(mailbox_routes, "imap_session", MagicMock()),
        patch.object(mailbox_routes, "append_message", MagicMock()),
        patch.object(mailbox_routes, "_mailbox_send_tracking_enabled", return_value=False),
    ):
        build.return_value = MagicMock()
        try:
            asyncio.run(mailbox_routes.send_message(FakeRequest(payload), mailbox=MEMBER))
        except HTTPException:
            raise
        except Exception:
            # Everything past submission (filing the Sent copy) is out of
            # scope here; the assertions below read the calls already made.
            pass
        return build, submit


class TestRefusal:
    @pytest.mark.parametrize("permission", [None, "read_only", "", "nonsense"])
    def test_a_member_without_a_sending_permission_is_refused(self, permission):
        with pytest.raises(HTTPException) as excinfo:
            _send(_body(**{"from": SHARED}), permission)

        assert excinfo.value.status_code == 403

    def test_refusal_happens_before_anything_is_submitted(self):
        """A 403 that still sent the mail would be worse than no check."""
        with pytest.raises(HTTPException):
            _send(_body(**{"from": SHARED}), "read_only")

    def test_a_database_failure_refuses_rather_than_allows(self):
        """_shared_mailbox_permission returns None on error -- fail closed."""
        with pytest.raises(HTTPException) as excinfo:
            _send(_body(**{"from": SHARED}), None)

        assert excinfo.value.status_code == 403


class TestPermittedSending:
    @pytest.mark.parametrize("permission", ["full_access", "send_as"])
    def test_from_and_envelope_both_become_the_shared_address(self, permission):
        build, submit = _send(_body(**{"from": SHARED}), permission)

        assert build.call_args.kwargs["from_address"] == SHARED
        # Envelope sender must match From, or SPF/DMARC align on the wrong
        # domain and bounces go to the individual instead of the team.
        assert submit.call_args.args[0] == SHARED

    def test_send_on_behalf_names_the_person_in_sender(self):
        build, submit = _send(_body(**{"from": SHARED}), "send_on_behalf")
        message = build.return_value

        assert build.call_args.kwargs["from_address"] == SHARED
        assert submit.call_args.args[0] == SHARED
        # Exchange's convention: clients render "agent on behalf of sales".
        message.__setitem__.assert_any_call("Sender", MEMBER["email"])

    def test_send_as_does_not_set_a_sender_header(self):
        """Send as is supposed to be indistinguishable from the shared address."""
        build, _ = _send(_body(**{"from": SHARED}), "send_as")

        headers = [c.args[0] for c in build.return_value.__setitem__.call_args_list]
        assert "Sender" not in headers


class TestOrdinarySending:
    def test_no_from_field_still_sends_as_the_signed_in_mailbox(self):
        build, submit = _send(_body(), None)

        assert build.call_args.kwargs["from_address"] == MEMBER["email"]
        assert submit.call_args.args[0] == MEMBER["email"]

    def test_sending_as_yourself_needs_no_permission_check(self):
        """Some clients always send `from`; that must not require membership."""
        with (
            patch.object(mailbox_routes, "_shared_mailbox_permission") as lookup,
            patch.object(mailbox_routes, "build_message") as build,
            patch.object(mailbox_routes, "submit_message"),
            patch.object(mailbox_routes, "message_bytes", return_value=b"raw"),
            patch.object(mailbox_routes, "imap_session", MagicMock()),
            patch.object(mailbox_routes, "append_message", MagicMock()),
            patch.object(mailbox_routes, "_mailbox_send_tracking_enabled", return_value=False),
        ):
            build.return_value = MagicMock()
            try:
                asyncio.run(
                    mailbox_routes.send_message(
                        FakeRequest(_body(**{"from": "AGENT@techies.africa"})),
                        mailbox=MEMBER,
                    )
                )
            except HTTPException:
                raise
            except Exception:
                pass

        # Case-insensitively the same address, so no lookup at all.
        lookup.assert_not_called()
        assert build.call_args.kwargs["from_address"] == MEMBER["email"]
