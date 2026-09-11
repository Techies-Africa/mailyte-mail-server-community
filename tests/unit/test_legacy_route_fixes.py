#!/usr/bin/env python3
"""
Unit tests for the 2026-08-30 legacy-route fixes in
worker/api/routes/mailboxes.py and worker/api/routes/aliases.py:

* POST /aliases/add and POST /mailboxes/add returned cursor.lastrowid,
  which is always 0 for the CHAR(26) ULID primary keys -- they must return
  the generated ULID.
* GET /mailboxes/get/quota/{mailbox} ran a folder-breakdown query with no
  FROM clause (500 on every call).
* POST /mailboxes/edit/quota and POST /mailboxes/edit wrote nonexistent
  columns (quota, modified) -- fixed to storage_quota / updated_at / status.
* Mailbox mutations that change credentials or status now flush Dovecot's
  auth cache (the SMTP-credential routes' doveadm flush), so revocation is
  not delayed by the 1h auth_cache_ttl.

The undecorated handlers are exercised via __wrapped__ (functools.wraps
exposes it), with auth_context supplied directly -- auth itself is covered
elsewhere.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "worker" / "api"))

from routes import aliases as aliases_routes
from routes import mailboxes as mailboxes_routes

ORG_ID = "01ORGULID0000000000000000A"
DOMAIN_ID = "01DOMAINULID000000000000AA"
MBOX_ID = "01MBOXULID00000000000000AA"


class FakeRequest:
    """Just enough of fastapi.Request for the handlers under test."""

    def __init__(self, payload=None, scope="platform", org_id=None):
        self._payload = payload
        self.state = SimpleNamespace(auth_context={"scope": scope, "organization_id": org_id})

    async def json(self):
        return self._payload


def _mock_conn(fetchone_results, rowcount=1):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.side_effect = list(fetchone_results)
    cursor.fetchall.return_value = []
    cursor.rowcount = rowcount
    # The CHAR(26)-PK behaviour under test: MySQL only tracks
    # AUTO_INCREMENT columns here, so lastrowid is 0.
    cursor.lastrowid = 0
    return conn, cursor


def _executed_sql(cursor):
    return [str(call.args[0]) for call in cursor.execute.call_args_list]


class TestAliasAddReturnsUlid:
    def test_alias_id_is_inserted_ulid_not_lastrowid(self):
        conn, cursor = _mock_conn(
            [
                {"id": DOMAIN_ID, "organization_id": ORG_ID},
                None,  # alias-exists check
            ]
        )
        with (
            patch.object(aliases_routes, "get_db_connection", return_value=conn),
            patch.object(aliases_routes, "dispatch_event") as dispatch,
        ):
            request = FakeRequest({"source": "sales@example.com", "destination": "a@example.com"})
            result = asyncio.run(aliases_routes.add_alias.__wrapped__(request))

        insert_call = next(
            call
            for call in cursor.execute.call_args_list
            if "INSERT INTO aliases" in str(call.args[0])
        )
        inserted_id = insert_call.args[1][0]
        assert len(inserted_id) == 26
        assert result["data"]["alias_id"] == inserted_id
        assert result["data"]["alias_id"] != 0
        # The dispatched event must carry the real id too.
        assert dispatch.call_args.kwargs["data"]["alias_id"] == inserted_id


class TestMailboxAddReturnsUlid:
    def test_user_id_is_inserted_ulid_not_lastrowid(self):
        conn, cursor = _mock_conn(
            [
                {"id": DOMAIN_ID, "organization_id": ORG_ID},
                None,  # mailbox-exists check
            ]
        )
        with (
            patch.object(mailboxes_routes, "get_db_connection", return_value=conn),
            patch.object(mailboxes_routes, "dispatch_event"),
            patch.object(mailboxes_routes, "validate_password", return_value=(True, "")),
            patch.object(mailboxes_routes, "hash_password", return_value="$2b$fake"),
        ):
            request = FakeRequest(
                {"local_part": "user", "domain": "example.com", "password": "Str0ngP@ss!23"}
            )
            result = asyncio.run(mailboxes_routes.add_mailbox.__wrapped__(request))

        insert_call = next(
            call
            for call in cursor.execute.call_args_list
            if "INSERT INTO email_accounts" in str(call.args[0])
        )
        inserted_id = insert_call.args[1][0]
        assert len(inserted_id) == 26
        assert result["data"]["user_id"] == inserted_id
        assert result["data"]["user_id"] != 0


class TestLegacyQuotaEndpoints:
    def test_get_quota_returns_empty_folder_breakdown_and_valid_sql(self):
        quota_row = {
            "email": "user@example.com",
            "storage_quota": 1073741824,
            "storage_used": 1024,
            "usage_percent": 0.0001,
            "available": 1073740800,
        }
        conn, cursor = _mock_conn([quota_row])
        with patch.object(mailboxes_routes, "get_db_connection", return_value=conn):
            request = FakeRequest()  # platform scope skips verify_mailbox_scope's query
            result = asyncio.run(
                mailboxes_routes.get_mailbox_quota.__wrapped__("user@example.com", request)
            )

        assert result["type"] == "success"
        assert result["data"]["folder_breakdown"] == []
        assert result["data"]["storage_quota"] == 1073741824
        # No FROM-less query may survive: every SELECT must name a table.
        for sql in _executed_sql(cursor):
            if sql.strip().upper().startswith("SELECT"):
                assert "FROM" in sql.upper()

    def test_edit_quota_writes_real_columns(self):
        conn, cursor = _mock_conn([], rowcount=1)
        with patch.object(mailboxes_routes, "get_db_connection", return_value=conn):
            request = FakeRequest({"mailbox": "user@example.com", "quota": "2147483648"})
            result = asyncio.run(mailboxes_routes.edit_mailbox_quota.__wrapped__(request))

        assert result["type"] == "success"
        update_call = next(
            call
            for call in cursor.execute.call_args_list
            if "UPDATE email_accounts" in str(call.args[0])
        )
        sql = str(update_call.args[0])
        assert "storage_quota = %s" in sql
        assert "updated_at = %s" in sql
        assert "modified" not in sql
        assert "SET quota" not in sql
        # bytes, coerced to int
        assert update_call.args[1][0] == 2147483648

    def test_edit_quota_rejects_non_integer(self):
        with patch.object(mailboxes_routes, "get_db_connection") as get_conn:
            request = FakeRequest({"mailbox": "user@example.com", "quota": "lots"})
            response = asyncio.run(mailboxes_routes.edit_mailbox_quota.__wrapped__(request))
        assert response.status_code == 400
        get_conn.assert_not_called()

    def test_legacy_edit_maps_attrs_to_real_columns(self):
        conn, cursor = _mock_conn([{"organization_id": ORG_ID}], rowcount=1)
        with (
            patch.object(mailboxes_routes, "get_db_connection", return_value=conn),
            patch.object(mailboxes_routes, "dispatch_event"),
            patch.object(mailboxes_routes, "flush_auth_cache") as flush,
        ):
            request = FakeRequest(
                {
                    "items": ["user@example.com"],
                    "attr": {"name": "New Name", "quota": 123456, "active": 0},
                }
            )
            result = asyncio.run(mailboxes_routes.edit_mailbox.__wrapped__(request))

        assert result["data"][0]["status"] == "success"
        update_call = next(
            call
            for call in cursor.execute.call_args_list
            if "UPDATE email_accounts" in str(call.args[0])
        )
        sql = str(update_call.args[0])
        assert "storage_quota = %s" in sql
        assert "status = %s" in sql
        assert "updated_at = %s" in sql
        assert "modified" not in sql
        assert "quota = %s" not in sql.replace("storage_quota = %s", "")
        assert "active = %s" not in sql
        assert "inactive" in update_call.args[1]
        # active changed -> the auth cache must be flushed for this mailbox.
        flush.assert_called_once_with("user@example.com")


class TestAuthCacheFlushOnMutation:
    def test_legacy_edit_without_credential_change_does_not_flush(self):
        conn, _cursor = _mock_conn([{"organization_id": ORG_ID}], rowcount=1)
        with (
            patch.object(mailboxes_routes, "get_db_connection", return_value=conn),
            patch.object(mailboxes_routes, "dispatch_event"),
            patch.object(mailboxes_routes, "flush_auth_cache") as flush,
        ):
            request = FakeRequest({"items": ["user@example.com"], "attr": {"name": "Only a name"}})
            asyncio.run(mailboxes_routes.edit_mailbox.__wrapped__(request))
        flush.assert_not_called()

    def test_legacy_delete_flushes(self):
        conn, _cursor = _mock_conn([{"organization_id": ORG_ID}], rowcount=1)
        with (
            patch.object(mailboxes_routes, "get_db_connection", return_value=conn),
            patch.object(mailboxes_routes, "dispatch_event"),
            patch.object(mailboxes_routes, "flush_auth_cache") as flush,
        ):
            request = FakeRequest(["user@example.com"])
            result = asyncio.run(mailboxes_routes.delete_mailbox.__wrapped__(request))
        assert result["data"][0]["status"] == "success"
        flush.assert_called_once_with("user@example.com")

    def _orm_session_for_update(self, account):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = account
        return session

    def test_orm_status_change_flushes(self):
        account = MagicMock()
        account.email = "user@example.com"
        account.organization_id = ORG_ID
        account.to_dict.return_value = {"email": account.email}
        session = self._orm_session_for_update(account)
        with (
            patch.object(mailboxes_routes, "get_db_session", return_value=session),
            patch.object(mailboxes_routes, "dispatch_event"),
            patch.object(mailboxes_routes, "flush_auth_cache") as flush,
        ):
            request = FakeRequest({"status": "suspended"})
            result = asyncio.run(
                mailboxes_routes.update_email_account.__wrapped__(MBOX_ID, request)
            )
        assert result["type"] == "success"
        flush.assert_called_once_with("user@example.com")

    def test_orm_name_change_does_not_flush(self):
        account = MagicMock()
        account.email = "user@example.com"
        account.organization_id = ORG_ID
        account.to_dict.return_value = {"email": account.email}
        session = self._orm_session_for_update(account)
        with (
            patch.object(mailboxes_routes, "get_db_session", return_value=session),
            patch.object(mailboxes_routes, "dispatch_event"),
            patch.object(mailboxes_routes, "flush_auth_cache") as flush,
        ):
            request = FakeRequest({"name": "Renamed"})
            asyncio.run(mailboxes_routes.update_email_account.__wrapped__(MBOX_ID, request))
        flush.assert_not_called()

    def test_orm_delete_flushes(self):
        account = MagicMock()
        account.id = MBOX_ID
        account.email = "user@example.com"
        account.organization_id = ORG_ID
        account.storage_used = 1024
        domain = MagicMock()
        domain.total_email_accounts = 3
        domain.total_storage_used = 4096
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.side_effect = [account, domain]
        with (
            patch.object(mailboxes_routes, "get_db_session", return_value=session),
            patch.object(mailboxes_routes, "dispatch_event"),
            patch.object(mailboxes_routes, "flush_auth_cache") as flush,
        ):
            request = FakeRequest()
            result = asyncio.run(
                mailboxes_routes.delete_email_account.__wrapped__(MBOX_ID, request)
            )
        assert result["type"] == "success"
        flush.assert_called_once_with("user@example.com")
