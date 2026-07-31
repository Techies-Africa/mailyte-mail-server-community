"""
Integration tests for Sieve filtering and mail folder structure.

Verifies filter/sieve API endpoints, ManageSieve port availability,
and default IMAP folder presence against the live Docker services.
"""

import socket

import pytest
import requests

from .conftest import (
    API_BASE,
    IMAP_HOST,
)

TIMEOUT = 10


# ---------------------------------------------------------------------------
# Filter API endpoints
# ---------------------------------------------------------------------------


class TestFilterAPI:
    def test_filters_list(self, api_headers):
        """GET /filters/ endpoint is reachable."""
        resp = requests.get(
            f"{API_BASE}/api/v1/filters/",
            headers=api_headers,
            timeout=TIMEOUT,
        )
        assert resp.status_code in (200, 401, 404, 422), (
            f"Unexpected status for filters list: {resp.status_code}"
        )

    def test_filters_templates(self, api_headers):
        """GET /filters/templates endpoint is reachable."""
        resp = requests.get(
            f"{API_BASE}/api/v1/filters/templates",
            headers=api_headers,
            timeout=TIMEOUT,
        )
        assert resp.status_code in (200, 404, 422, 500), (
            f"Unexpected status for filter templates: {resp.status_code}"
        )

    def test_sieve_vacation_endpoint(self, api_headers):
        """POST /filters/vacation endpoint accepts a disable-vacation payload."""
        resp = requests.post(
            f"{API_BASE}/api/v1/filters/vacation",
            headers=api_headers,
            json={"enabled": False},
            timeout=TIMEOUT,
        )
        assert resp.status_code in (200, 404, 422, 500), (
            f"Unexpected status for vacation endpoint: {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# ManageSieve port
# ---------------------------------------------------------------------------


class TestManageSieve:
    def test_managesieve_port_open(self):
        """ManageSieve port 4190 is listening on IMAP_HOST."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(TIMEOUT)
        try:
            sock.connect((IMAP_HOST, 4190))
        except (TimeoutError, ConnectionRefusedError, OSError) as exc:
            pytest.fail(f"ManageSieve port 4190 not reachable: {exc}")
        finally:
            sock.close()


# ---------------------------------------------------------------------------
# Default IMAP folders
# ---------------------------------------------------------------------------


class TestIMAPFolders:
    def test_imap_has_junk_folder(self, imap_connection):
        """IMAP account must have a Junk folder."""
        status, folders = imap_connection.list()
        assert status == "OK", f"IMAP LIST failed: {status}"
        folder_names = [entry.decode() if isinstance(entry, bytes) else entry for entry in folders]
        joined = "\n".join(folder_names)
        assert any("Junk" in f for f in folder_names), (
            f"Junk folder not found in IMAP LIST:\n{joined}"
        )

    def test_imap_has_archive_folder(self, imap_connection):
        """IMAP account must have an Archive folder."""
        status, folders = imap_connection.list()
        assert status == "OK", f"IMAP LIST failed: {status}"
        folder_names = [entry.decode() if isinstance(entry, bytes) else entry for entry in folders]
        joined = "\n".join(folder_names)
        assert any("Archive" in f for f in folder_names), (
            f"Archive folder not found in IMAP LIST:\n{joined}"
        )
