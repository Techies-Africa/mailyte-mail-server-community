#!/usr/bin/env python3
"""
Unit tests for worker/api/routes/shared_mailboxes.py.

POST /shared-mailboxes had never once succeeded in production. Its INSERT
omitted the id column, but 009_ulid_safe.sql had converted
email_accounts.id to CHAR(26) with no AUTO_INCREMENT and no default -- so
MySQL raised "1364 Field 'id' doesn't have a default value" under
STRICT_TRANS_TABLES and the customer saw a bare 500 ("The mail server is
temporarily unavailable"). The members INSERT had the same defect waiting
behind it.

The e2e suite did not catch this: test_create_shared_mailbox asserts
`status_code in (200, 201, 400, 404, 409)` and then only inspects the body
`if resp.status_code in (200, 201)` -- a create that can never succeed
passes it silently. These tests assert on the SQL actually generated
instead, which is where the defect lived.

Handlers are exercised through __wrapped__ (require_api_key uses
functools.wraps), with auth_context supplied directly -- auth itself is
covered elsewhere.
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

from routes import shared_mailboxes as shared_routes  # noqa: E402

ORG_ID = "01ORGULID0000000000000000A"
DOMAIN_ID = "01DOMAINULID000000000000AA"
MBOX_ID = "01MBOXULID00000000000000AA"
MEMBER_ID = "01MEMBERULID000000000000AA"


class FakeRequest:
    """Just enough of fastapi.Request for the handlers under test."""

    def __init__(self, scope="platform", org_id=None):
        self.state = SimpleNamespace(auth_context={"scope": scope, "organization_id": org_id})


def _mock_db(fetchone_results):
    db = MagicMock()
    cursor = MagicMock()
    db.cursor.return_value = cursor
    cursor.fetchone.side_effect = list(fetchone_results)
    cursor.fetchall.return_value = []
    # The CHAR(26)-PK behaviour under test: MySQL only tracks AUTO_INCREMENT
    # columns here, so lastrowid is 0 on every one of these inserts.
    cursor.lastrowid = 0
    return db, cursor


def _insert_call(cursor, table):
    return next(
        call
        for call in cursor.execute.call_args_list
        if f"INSERT INTO {table}" in str(call.args[0])
    )


def _column_list(sql, table):
    """The column names between the parens after `INSERT INTO <table>`."""
    head = sql.split(f"INSERT INTO {table}", 1)[1]
    columns = head[head.index("(") + 1 : head.index(")")]
    return [c.strip() for c in columns.split(",")]


class TestCreateSharedMailbox:
    def _run(self, auto_reply_enabled=False, auto_reply_message=None):
        db, cursor = _mock_db(
            [
                {"id": DOMAIN_ID, "organization_id": ORG_ID},  # domain lookup
                None,  # email-already-exists check
            ]
        )
        payload = shared_routes.SharedMailboxCreate(
            email="support@example.com",
            name="Customer Support",
            auto_reply_enabled=auto_reply_enabled,
            auto_reply_message=auto_reply_message,
        )
        with patch.object(shared_routes, "dispatch_event") as dispatch:
            result = asyncio.run(
                shared_routes.create_shared_mailbox.__wrapped__(payload, FakeRequest(), db=db)
            )
        return result, cursor, dispatch

    def test_insert_names_the_ulid_id_column(self):
        """The 1364 regression: id has no default, so it must be in the INSERT."""
        _, cursor, _ = self._run()

        call = _insert_call(cursor, "email_accounts")
        columns = _column_list(str(call.args[0]), "email_accounts")

        assert columns[0] == "id"
        assert len(call.args[1][0]) == 26

    def test_returned_id_is_the_inserted_ulid_not_lastrowid(self):
        result, cursor, dispatch = self._run()

        inserted_id = _insert_call(cursor, "email_accounts").args[1][0]

        assert result["id"] == inserted_id
        assert result["id"] != 0
        # The webhook consumers must be told the real id too.
        assert dispatch.call_args.kwargs["data"]["shared_mailbox_id"] == inserted_id

    def test_auto_reply_is_persisted_to_the_vacation_columns(self):
        """Accepted in the request model, so it must not be dropped silently."""
        _, cursor, _ = self._run(auto_reply_enabled=True, auto_reply_message="Back Monday")

        call = _insert_call(cursor, "email_accounts")
        columns = _column_list(str(call.args[0]), "email_accounts")

        assert "vacation_enabled" in columns
        assert "vacation_message" in columns
        assert True in call.args[1]
        assert "Back Monday" in call.args[1]

    def test_placeholders_match_bound_parameters(self):
        """A miscount here is another 500 that only shows up in production."""
        call = _insert_call(self._run()[1], "email_accounts")
        sql = str(call.args[0])

        columns = _column_list(sql, "email_accounts")
        values = sql[sql.rindex("VALUES") :]

        assert values.count("%s") == len(call.args[1])
        # Every column is filled by a placeholder or a literal, never left short.
        assert len(columns) == values.count(",") + 1


class TestAddMember:
    def _run(self, identifier=MEMBER_ID):
        db, cursor = _mock_db(
            [
                {"id": MBOX_ID, "organization_id": ORG_ID},  # shared mailbox lookup
                {"id": MEMBER_ID, "email": "agent@example.com"},  # member account lookup
                {"id": "01STOREDMEMBERROWID000000"},  # id read-back after upsert
            ]
        )
        member = shared_routes.SharedMailboxMember(
            email_account_id=identifier, permission="full_access"
        )
        with patch.object(shared_routes, "dispatch_event") as dispatch:
            result = asyncio.run(
                shared_routes.add_member.__wrapped__(MBOX_ID, member, FakeRequest(), db=db)
            )
        return result, cursor, dispatch

    def test_insert_names_the_ulid_id_column(self):
        _, cursor, _ = self._run()

        call = _insert_call(cursor, "shared_mailbox_members")
        columns = _column_list(str(call.args[0]), "shared_mailbox_members")

        assert columns[0] == "id"
        assert len(call.args[1][0]) == 26

    def test_member_id_is_read_back_not_lastrowid(self):
        """On the ON DUPLICATE KEY branch the row keeps its own id, not ours."""
        result, _, dispatch = self._run()

        assert result["member_id"] == "01STOREDMEMBERROWID000000"
        assert result["member_id"] != 0
        assert dispatch.call_args.kwargs["data"]["member_id"] == "01STOREDMEMBERROWID000000"

    def test_placeholders_match_bound_parameters(self):
        call = _insert_call(self._run()[1], "shared_mailbox_members")
        sql = str(call.args[0])

        # Includes the ON DUPLICATE KEY UPDATE placeholder.
        assert sql.count("%s") == len(call.args[1])

    def test_member_is_looked_up_by_id_or_email(self):
        """A caller holding only the address must still resolve."""
        _, cursor, _ = self._run()

        lookup = next(
            call
            for call in cursor.execute.call_args_list
            if "FROM email_accounts" in str(call.args[0])
            and "status = 'active'" in str(call.args[0])
        )
        sql = " ".join(str(lookup.args[0]).split())

        assert "id = %s OR email = %s" in sql
        # Still scoped to the organization -- resolution must not widen access.
        assert "organization_id = %s" in sql

    def test_resolved_id_is_stored_not_the_identifier_sent(self):
        """The row must key on OUR id even when the caller passed an address."""
        _, cursor, dispatch = self._run(identifier="agent@example.com")

        insert = _insert_call(cursor, "shared_mailbox_members")

        # (id, shared_mailbox_id, email_account_id, permission, permission)
        assert insert.args[1][2] == MEMBER_ID
        assert "@" not in insert.args[1][2]
        assert dispatch.call_args.kwargs["data"]["member_email"] == "agent@example.com"

    def test_response_reports_the_identifier_actually_stored(self):
        result, _, _ = self._run(identifier="agent@example.com")

        assert result["email_account_id"] == MEMBER_ID
        assert result["email"] == "agent@example.com"


class TestMemberRouteSegmentResolution:
    """remove/update take the member in a path segment -- same two id spaces."""

    def _db(self):
        return _mock_db(
            [
                {"organization_id": ORG_ID},  # _get_mailbox_org
                {"id": MEMBER_ID, "email": "agent@example.com"},  # _resolve_member_account
            ]
        )

    def test_remove_deletes_by_the_resolved_id(self):
        db, cursor = self._db()
        cursor.rowcount = 1
        with patch.object(shared_routes, "dispatch_event") as dispatch:
            asyncio.run(
                shared_routes.remove_member.__wrapped__(
                    MBOX_ID, FakeRequest(), "agent@example.com", db=db
                )
            )

        delete = next(
            call for call in cursor.execute.call_args_list if "DELETE FROM" in str(call.args[0])
        )

        assert delete.args[1] == (MBOX_ID, MEMBER_ID)
        assert dispatch.call_args.kwargs["data"]["member_account_id"] == MEMBER_ID

    def test_update_writes_by_the_resolved_id(self):
        db, cursor = self._db()
        cursor.rowcount = 1
        member = shared_routes.SharedMailboxMember(
            email_account_id="agent@example.com", permission="read_only"
        )
        asyncio.run(
            shared_routes.update_member_permission.__wrapped__(
                MBOX_ID, "agent@example.com", member, FakeRequest(), db=db
            )
        )

        update = next(
            call
            for call in cursor.execute.call_args_list
            if "UPDATE shared_mailbox_members" in str(call.args[0])
        )

        assert update.args[1] == ("read_only", MBOX_ID, MEMBER_ID)

    def test_unknown_member_is_404_not_a_silent_no_op(self):
        db, _ = _mock_db([{"organization_id": ORG_ID}, None])

        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(
                shared_routes.remove_member.__wrapped__(
                    MBOX_ID, FakeRequest(), "nobody@example.com", db=db
                )
            )

        assert excinfo.value.status_code == 404


class TestGetSharedMailbox:
    def test_password_column_is_not_returned(self):
        """`SELECT ea.*` pulls password; it must never reach the client."""
        db, cursor = _mock_db(
            [
                {
                    "id": MBOX_ID,
                    "email": "support@example.com",
                    "organization_id": ORG_ID,
                    "password": "",
                }
            ]
        )
        result = asyncio.run(
            shared_routes.get_shared_mailbox.__wrapped__(MBOX_ID, FakeRequest(), db=db)
        )

        assert "password" not in result["mailbox"]
