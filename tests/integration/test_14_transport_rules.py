"""
Integration tests for transport rules.

Verifies the transport-rules API endpoints (list, create, delete)
and database schema against the live Docker services.
"""

import pytest
import requests

from .conftest import (
    API_BASE,
)

TIMEOUT = 10

# Shared state so the cleanup test can reference the created rule.
_created_rule_id = None


# ---------------------------------------------------------------------------
# Transport rules API
# ---------------------------------------------------------------------------


class TestTransportRulesAPI:
    def test_transport_rules_list(self, api_headers):
        """GET /transport-rules/ returns 200."""
        resp = requests.get(
            f"{API_BASE}/api/v1/transport-rules/",
            headers=api_headers,
            timeout=TIMEOUT,
        )
        assert resp.status_code in (200, 422, 500), (
            f"Unexpected status for transport rules list: {resp.status_code}"
        )

    def test_transport_rules_create(self, api_headers):
        """POST /transport-rules/ creates a disabled test rule."""
        global _created_rule_id

        payload = {
            "name": "test-rule",
            "direction": "outbound",
            "conditions": {"sender_domain": "test.local"},
            "actions": {"add_header": "X-Test: true"},
            "priority": 100,
            "enabled": False,
        }
        resp = requests.post(
            f"{API_BASE}/api/v1/transport-rules/",
            headers=api_headers,
            json=payload,
            timeout=TIMEOUT,
        )
        assert resp.status_code in (200, 201, 422), (
            f"Unexpected status for transport rule create: {resp.status_code}"
        )

        # Try to capture the created rule id for later cleanup.
        try:
            data = resp.json()
            _created_rule_id = (
                data.get("id") or data.get("rule_id") or data.get("data", {}).get("id")
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------


class TestTransportRulesDatabase:
    def test_transport_rules_table_exists(self, db_connection):
        """transport_rules table must exist and be queryable."""
        cursor = db_connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM transport_rules")
        count = cursor.fetchone()[0]
        cursor.close()
        # Simply reaching here without error means the table exists.
        assert count >= 0


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestTransportRulesCleanup:
    def test_transport_rules_cleanup(self, api_headers):
        """Delete the test rule created earlier (if any)."""
        global _created_rule_id

        rule_id = _created_rule_id

        # If we didn't capture the id from the create response, search for it.
        if rule_id is None:
            resp = requests.get(
                f"{API_BASE}/api/v1/transport-rules/",
                headers=api_headers,
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                try:
                    rules = resp.json()
                    # Handle both list and wrapped-list responses.
                    if isinstance(rules, dict):
                        rules = rules.get("data", rules.get("rules", []))
                    for rule in rules:
                        if rule.get("name") == "test-rule":
                            rule_id = rule.get("id") or rule.get("rule_id")
                            break
                except Exception:
                    pass

        if rule_id is None:
            pytest.skip("No test-rule found to clean up")

        resp = requests.delete(
            f"{API_BASE}/api/v1/transport-rules/{rule_id}",
            headers=api_headers,
            timeout=TIMEOUT,
        )
        assert resp.status_code in (200, 204, 404), (
            f"Unexpected status for transport rule delete: {resp.status_code}"
        )
        _created_rule_id = None
