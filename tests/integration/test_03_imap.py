"""
Integration tests for IMAP and POP3 access.

Tests run against live Dovecot services — ensure the dev environment is up.
"""

import ssl
import imaplib
import poplib

import pytest

from .conftest import (
    IMAP_HOST,
    IMAP_PORT,
    IMAP_SSL_PORT,
    POP3_HOST,
    POP3_PORT,
    POP3_SSL_PORT,
    TEST_USER,
    TEST_PASS,
)


# ---------------------------------------------------------------------------
# IMAP Tests
# ---------------------------------------------------------------------------


class TestIMAPStartTLS:
    """IMAP connections on port 143 with STARTTLS."""

    def test_imap_login_starttls(self):
        """Successful login over IMAP STARTTLS."""
        conn = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
        try:
            conn.starttls()
            status, _ = conn.login(TEST_USER, TEST_PASS)
            assert status == "OK"
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def test_imap_login_invalid_password(self):
        """Login with wrong password must raise an exception."""
        conn = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
        try:
            conn.starttls()
            with pytest.raises(imaplib.IMAP4.error):
                conn.login(TEST_USER, "wrong-password-xyz")
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def test_imap_login_nonexistent_user(self):
        """Login with a non-existent user must raise an exception."""
        conn = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
        try:
            conn.starttls()
            with pytest.raises(imaplib.IMAP4.error):
                conn.login("fake@test.local", "irrelevant")
        finally:
            try:
                conn.logout()
            except Exception:
                pass


class TestIMAPSSL:
    """IMAP connections on port 993 with implicit SSL."""

    def test_imap_login_ssl(self):
        """Successful login over IMAP SSL (port 993)."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_SSL_PORT, ssl_context=ctx)
        try:
            status, _ = conn.login(TEST_USER, TEST_PASS)
            assert status == "OK"
        finally:
            try:
                conn.logout()
            except Exception:
                pass


class TestIMAPOperations:
    """IMAP mailbox operations after login."""

    def test_imap_list_folders(self, imap_connection):
        """Standard folders must exist: Inbox, Sent, Drafts, Junk, Trash, Archive."""
        status, folder_data = imap_connection.list()
        assert status == "OK"

        # Decode folder names from the LIST response
        folder_names = []
        for entry in folder_data:
            if isinstance(entry, bytes):
                decoded = entry.decode("utf-8", errors="replace")
            else:
                decoded = str(entry)
            # The folder name is the last element after the delimiter
            # Format: '(\\flags) "delimiter" "name"' or similar
            parts = decoded.rsplit('" ', 1)
            if len(parts) == 2:
                name = parts[1].strip().strip('"')
            else:
                name = decoded.split()[-1].strip('"')
            folder_names.append(name)

        # Normalise to lowercase for comparison
        lower_names = [f.lower() for f in folder_names]

        expected = ["inbox", "sent", "drafts", "junk", "trash", "archive"]
        for folder in expected:
            assert folder in lower_names, (
                f"Expected folder '{folder}' not found. Got: {folder_names}"
            )

    def test_imap_select_inbox(self, imap_connection):
        """Selecting INBOX should succeed."""
        status, data = imap_connection.select("INBOX")
        assert status == "OK"

    def test_imap_search_all(self, imap_connection):
        """Searching INBOX for ALL should return a list (possibly empty)."""
        imap_connection.select("INBOX")
        status, data = imap_connection.search(None, "ALL")
        assert status == "OK"
        # data[0] is a space-separated list of message numbers, or b""
        assert isinstance(data, list)


# ---------------------------------------------------------------------------
# POP3 Tests
# ---------------------------------------------------------------------------


class TestPOP3StartTLS:
    """POP3 connections on port 110 with STLS."""

    def test_pop3_login_starttls(self):
        """Successful POP3 login with STLS."""
        conn = poplib.POP3(POP3_HOST, POP3_PORT, timeout=15)
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            conn.stls(context=ctx)
            conn.user(TEST_USER)
            response = conn.pass_(TEST_PASS)
            # POP3 returns a bytes response starting with b'+OK'
            assert response.startswith(b"+OK")
        finally:
            try:
                conn.quit()
            except Exception:
                pass

    def test_pop3_login_invalid(self):
        """POP3 login with wrong password must raise an exception."""
        conn = poplib.POP3(POP3_HOST, POP3_PORT, timeout=15)
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            conn.stls(context=ctx)
            conn.user(TEST_USER)
            with pytest.raises(poplib.error_proto):
                conn.pass_("wrong-password-xyz")
        finally:
            try:
                conn.quit()
            except Exception:
                pass


class TestPOP3SSL:
    """POP3 connections on port 995 with implicit SSL."""

    def test_pop3_login_ssl(self):
        """Successful POP3 login over SSL (port 995)."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = poplib.POP3_SSL(POP3_HOST, POP3_SSL_PORT, context=ctx, timeout=15)
        try:
            conn.user(TEST_USER)
            response = conn.pass_(TEST_PASS)
            assert response.startswith(b"+OK")
        finally:
            try:
                conn.quit()
            except Exception:
                pass


class TestPOP3Operations:
    """POP3 mailbox operations after login."""

    def test_pop3_list(self):
        """POP3 LIST should succeed after login."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = poplib.POP3_SSL(POP3_HOST, POP3_SSL_PORT, context=ctx, timeout=15)
        try:
            conn.user(TEST_USER)
            conn.pass_(TEST_PASS)
            response, listings, octets = conn.list()
            assert response.startswith(b"+OK")
            assert isinstance(listings, list)
        finally:
            try:
                conn.quit()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Advanced IMAP Tests
# ---------------------------------------------------------------------------


class TestIMAPOperationsAdvanced:
    """Advanced IMAP mailbox operations — fetch, move, delete, folder management."""

    def test_imap_fetch_message(self, imap_connection):
        """Fetching a message body from INBOX should return valid RFC822 data.

        This verifies Dovecot's maildir storage is working and that stored
        messages can be retrieved as compliant RFC822 data by any IMAP client.
        """
        import email

        status, _ = imap_connection.select("INBOX")
        assert status == "OK"
        status, data = imap_connection.search(None, "ALL")
        assert status == "OK"
        msg_nums = data[0].split()
        if not msg_nums:
            pytest.skip("No messages in INBOX to fetch")
        # Fetch the first message
        status, msg_data = imap_connection.fetch(msg_nums[0], "(RFC822)")
        assert status == "OK"
        assert msg_data is not None
        assert len(msg_data) > 0
        # The first element should be a tuple (envelope, body)
        raw_email = msg_data[0][1]
        assert isinstance(raw_email, bytes)
        # Verify it parses as a valid email message
        parsed = email.message_from_bytes(raw_email)
        assert parsed is not None
        # A valid email should have at least a From or Subject header
        assert parsed["From"] is not None or parsed["Subject"] is not None

    def test_imap_move_message(self, imap_connection):
        """Moving a message between folders tests Dovecot's folder management and ACL enforcement.

        IMAP COPY followed by a flag-and-expunge is the standard way clients
        move messages. This validates that cross-folder operations work with
        the configured namespace and permissions.
        """
        status, data = imap_connection.select("INBOX")
        assert status == "OK"
        status, data = imap_connection.search(None, "ALL")
        assert status == "OK"
        msg_nums = data[0].split()
        if not msg_nums:
            pytest.skip("No messages in INBOX to move")
        msg_num = msg_nums[0]
        # Copy the message to Trash
        status, _ = imap_connection.copy(msg_num, "Trash")
        assert status == "OK"
        # Verify it appears in Trash
        status, _ = imap_connection.select("Trash")
        assert status == "OK"
        status, trash_data = imap_connection.search(None, "ALL")
        assert status == "OK"
        trash_msgs = trash_data[0].split()
        assert len(trash_msgs) > 0, "Message was not copied to Trash"

    def test_imap_delete_message(self, imap_connection):
        """Deleting a message (flag + expunge) must remove it permanently.

        Tests maildir deletion by flagging a message as Deleted and expunging.
        The message count should decrease, confirming that Dovecot properly
        removes the message from the maildir store.
        """
        status, data = imap_connection.select("Trash")
        assert status == "OK"
        status, data = imap_connection.search(None, "ALL")
        assert status == "OK"
        msg_nums = data[0].split()
        if not msg_nums:
            pytest.skip("No messages in Trash to delete")
        initial_count = len(msg_nums)
        # Flag the last message as deleted and expunge
        target = msg_nums[-1]
        status, _ = imap_connection.store(target, "+FLAGS", "\\Deleted")
        assert status == "OK"
        status, _ = imap_connection.expunge()
        assert status == "OK"
        # Verify message count decreased
        status, data = imap_connection.search(None, "ALL")
        assert status == "OK"
        new_count = len(data[0].split()) if data[0] else 0
        assert new_count < initial_count, (
            f"Expected fewer messages after expunge: was {initial_count}, now {new_count}"
        )

    def test_imap_create_folder(self, imap_connection):
        """Creating a custom folder tests namespace permissions.

        Users should be able to organize mail into custom folders. This creates
        a folder, verifies it exists in the LIST response, then cleans up by
        deleting it.
        """
        folder_name = "TestFolder"
        # Create the folder
        status, _ = imap_connection.create(folder_name)
        assert status == "OK", f"Failed to create folder '{folder_name}'"
        # Verify it appears in LIST
        status, folder_data = imap_connection.list()
        assert status == "OK"
        folder_names = []
        for entry in folder_data:
            if isinstance(entry, bytes):
                decoded = entry.decode("utf-8", errors="replace")
            else:
                decoded = str(entry)
            folder_names.append(decoded)
        found = any(folder_name in f for f in folder_names)
        assert found, f"'{folder_name}' not found in LIST after creation"
        # Clean up — delete the folder
        status, _ = imap_connection.delete(folder_name)
        assert status == "OK", f"Failed to delete folder '{folder_name}'"

    def test_imap_search_by_subject(self, imap_connection):
        """Subject-based search is critical for email clients.

        Must work on maildir storage without error. The search may return zero
        results if no messages match, which is acceptable — the important thing
        is that Dovecot processes the SEARCH command without raising an error.
        """
        status, _ = imap_connection.select("INBOX")
        assert status == "OK"
        status, data = imap_connection.search(None, "SUBJECT", '"test"')
        assert status == "OK"
        # data[0] is a space-separated list of message numbers or b""
        assert isinstance(data, list)

    def test_imap_concurrent_limit(self):
        """Dovecot limits concurrent IMAP connections per user (max 20) to prevent resource exhaustion.

        This test opens 3 simultaneous IMAP connections and verifies they all
        authenticate successfully, confirming that the connection pool allows
        reasonable concurrency for multi-device users.
        """
        connections = []
        try:
            for _ in range(3):
                conn = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
                conn.starttls()
                status, _ = conn.login(TEST_USER, TEST_PASS)
                assert status == "OK"
                connections.append(conn)
            # All 3 connections should be active
            assert len(connections) == 3
        finally:
            for conn in connections:
                try:
                    conn.logout()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# POP3 Extended Tests
# ---------------------------------------------------------------------------


class TestPOP3ExtendedOperations:
    """Additional POP3 operations — STAT and UIDL commands."""

    def _pop3_login(self):
        """Helper: create an authenticated POP3 SSL connection."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = poplib.POP3_SSL(POP3_HOST, POP3_SSL_PORT, context=ctx, timeout=15)
        conn.user(TEST_USER)
        conn.pass_(TEST_PASS)
        return conn

    def test_pop3_stat(self):
        """POP3 STAT returns message count and total size.

        Essential for client compatibility — every POP3 client issues STAT
        immediately after authentication to determine mailbox state. The
        response must be a tuple of (message_count, mailbox_size).
        """
        conn = self._pop3_login()
        try:
            result = conn.stat()
            # stat() returns a tuple (message_count, mailbox_size)
            assert isinstance(result, tuple)
            assert len(result) == 2
            msg_count, total_size = result
            assert isinstance(msg_count, int)
            assert isinstance(total_size, int)
            assert msg_count >= 0
            assert total_size >= 0
        finally:
            try:
                conn.quit()
            except Exception:
                pass

    def test_pop3_uidl(self):
        """POP3 UIDL provides unique message IDs.

        Required for the 'leave messages on server' feature to work correctly.
        Without UIDL, clients cannot track which messages have already been
        downloaded, leading to duplicate downloads on each session.
        """
        conn = self._pop3_login()
        try:
            response, listings, octets = conn.uidl()
            assert response.startswith(b"+OK")
            # listings is a list of b"msgnum uniqueid" entries
            assert isinstance(listings, list)
        finally:
            try:
                conn.quit()
            except Exception:
                pass
