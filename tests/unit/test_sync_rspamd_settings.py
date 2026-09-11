#!/usr/bin/env python3
"""
Unit tests for scripts/sync_rspamd_settings.py.

The multimap sender/domain lists must land in Redis db 3 -- the db
mailer/rspamd/config/local.d/multimap.conf actually reads
(redis://redis:6379/3/org_sender_whitelist_${rcpt_domain}) -- while the
settings-module keys stay in db 0 (settings.conf uses the default db).
The script used to write everything to db 0, so per-org sender lists never
matched anything at scan time.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "scripts"))

import sync_rspamd_settings as sync_mod

fakeredis = pytest.importorskip("fakeredis")


def _org_row():
    return {
        "org_id": "01ORGULID0000000000000000A",
        "org_name": "Test Org",
        "org_settings": json.dumps(
            {
                "spam_policy": {
                    "spam_threshold": 5,
                    "sender_whitelist": ["trusted@partner.com"],
                    "sender_blacklist": ["spammer@bad.com"],
                    "domain_whitelist": ["partner.com"],
                }
            }
        ),
        "domains": "example.com",
    }


def _mock_conn(rows):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = rows
    return conn, cursor


class TestMultimapRedisDb:
    def test_constant_matches_multimap_conf(self):
        assert sync_mod.MULTIMAP_REDIS_DB == 3

    def test_lists_go_to_multimap_client_settings_stay_in_default(self):
        r_settings = fakeredis.FakeRedis(decode_responses=True)
        r_multimap = fakeredis.FakeRedis(decode_responses=True)
        conn, cursor = _mock_conn([_org_row()])

        synced = sync_mod.sync_all_orgs(r_settings, r_multimap, conn)
        assert synced == 1

        # Multimap lists live only in the multimap client (db 3 in prod).
        assert r_multimap.smembers("org_sender_whitelist_example.com") == {"trusted@partner.com"}
        assert r_multimap.smembers("org_sender_blacklist_example.com") == {"spammer@bad.com"}
        assert r_multimap.smembers("org_domain_whitelist_example.com") == {"partner.com"}
        assert not r_settings.exists("org_sender_whitelist_example.com")

        # Settings-module keys stay in the default-db client.
        settings_key = "rspamd_settings:org_01ORGULID0000000000000000A_example.com"
        stored = json.loads(r_settings.get(settings_key))
        assert stored["apply"]["actions"]["add header"] == 5
        assert not r_multimap.exists(settings_key)

    def test_org_query_filters_on_active_column(self):
        # organizations has an `active` tinyint, not a `status` column --
        # the old o.status filter crashed the sync with "Unknown column".
        r = fakeredis.FakeRedis(decode_responses=True)
        conn, cursor = _mock_conn([])
        sync_mod.sync_all_orgs(r, r, conn)
        sql = str(cursor.execute.call_args_list[0].args[0])
        assert "o.active = 1" in sql
        assert "o.status" not in sql
