"""
A minimal ManageSieve (RFC 5804) client.

Why this exists
---------------
The Sieve endpoints in routes/filters.py used to read and write scripts
directly on the maildir filesystem (``/var/mail/vhosts/<domain>/<user>/sieve``
plus a ``.dovecot.sieve`` symlink, chowned to 5000). That has never worked in
a containerised deployment: this service does not mount the maildir volume,
and it runs as an unprivileged uid that cannot chown to the vmail user even
if it did. Every write failed with ``PermissionError: [Errno 13]``, while the
reads returned an empty list for a directory that was not there -- so the
whole surface looked healthy and silently did nothing.

ManageSieve is the protocol that exists for exactly this. Dovecot already
speaks it on port 4190 (``protocols = imap pop3 lmtp sieve``), it applies the
same per-user resolution and permissions Dovecot uses everywhere else, and it
compiles the script before accepting it -- so a syntactically invalid filter
is rejected at the API boundary instead of silently breaking a user's mail
delivery. It is also what SOGo uses to implement the same features.

Authentication is SASL PLAIN with a master user: authzid is the mailbox being
administered, authcid/password are the master credentials, which is the same
impersonation path worker/jmap already uses for IMAP.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import os
import re
import socket
import ssl
import threading
import time

logger = logging.getLogger(__name__)

SIEVE_HOST = os.getenv("SIEVE_HOST", os.getenv("IMAP_HOST", "dovecot"))
SIEVE_PORT = int(os.getenv("SIEVE_PORT", "4190"))
SIEVE_MASTER_USER = os.getenv("IMAP_MASTER_USER", "")
SIEVE_MASTER_PASSWORD = os.getenv("IMAP_MASTER_PASSWORD", "")
SIEVE_TIMEOUT = int(os.getenv("SIEVE_TIMEOUT", "15"))

# Availability probe (see sieve_available). A short timeout on purpose: the
# probe runs on the request path of /mailbox/capabilities when the cache is
# cold, and a dead Dovecot should cost that request a few seconds, not the
# full command timeout. Both outcomes are cached for SIEVE_PROBE_TTL seconds.
# SIEVE_PROBE_ACCOUNT, when set, upgrades the probe from "reachable and
# offers PLAIN" to a full master-user login as that mailbox -- see the
# docstring on probe_service for why a login cannot be probed without one.
SIEVE_PROBE_TTL = int(os.getenv("SIEVE_PROBE_TTL", "60"))
SIEVE_PROBE_TIMEOUT = float(os.getenv("SIEVE_PROBE_TIMEOUT", "3"))
SIEVE_PROBE_ACCOUNT = os.getenv("SIEVE_PROBE_ACCOUNT", "").strip()

# Script names are used directly in a quoted protocol string and, on the
# server, as a filename. Anything outside this set is refused rather than
# escaped -- there is no legitimate filter name that needs a quote or a slash.
SAFE_SCRIPT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")

# RFC 5804 section 1.3: a NO/BYE may carry a parenthesised response code
# before its human-readable text, e.g. ``NO (NONEXISTENT) "Sieve script
# `x' not found"`` or ``NO (QUOTA/MAXSIZE) "..."``. The code is the stable
# contract; the wording is Pigeonhole's and has already changed once.
_RESPONSE_CODE_RE = re.compile(rb"^(?:NO|BYE)\s*\(\s*([A-Za-z0-9/_\-]+)", re.IGNORECASE)

# Wording fallback for servers that omit the response code.
_MISSING_SCRIPT_WORDING = ("nonexistent", "does not exist", "not found", "no such script")


class ManageSieveError(Exception):
    """A ManageSieve command was refused, or the connection failed.

    ``code`` is the RFC 5804 response code (``NONEXISTENT``, ``ACTIVE``,
    ``QUOTA/MAXSIZE``, ``TRYLATER`` ...) when the server sent one, else None.
    Branch on it, not on the message: Pigeonhole says "Sieve script `x' not
    found" where the RFC examples say "does not exist", and matching the
    prose is how every GET on a mailbox that had never saved a filter came
    to 502 in production (2026-08-31).
    """

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


class ManageSieveUnavailableError(ManageSieveError):
    """The service could not be reached, or refused the connection or login.

    Distinct from a refused command because the two mean different things
    to a caller: a refused PUTSCRIPT is the user's script, this is the
    deployment. Routes map it to 503 and it flips the availability cache,
    so /mailbox/capabilities stops advertising the Sieve features.
    """


def valid_script_name(name: str) -> bool:
    return bool(SAFE_SCRIPT_NAME.match(name or ""))


def _script_missing(exc: ManageSieveError) -> bool:
    if exc.code == "NONEXISTENT":
        return True
    text = str(exc).lower()
    return any(wording in text for wording in _MISSING_SCRIPT_WORDING)


class ManageSieveClient:
    """
    One connection, one mailbox. Use as a context manager.

        with ManageSieveClient("user@example.com") as sieve:
            sieve.put_script("mailyte-rules", content)
            sieve.set_active("mailyte-rules")
    """

    def __init__(
        self,
        account: str,
        host: str | None = None,
        port: int | None = None,
        timeout: float | None = None,
    ):
        self.account = account
        self.host = host or SIEVE_HOST
        self.port = port or SIEVE_PORT
        self.timeout = timeout or SIEVE_TIMEOUT
        self._sock: socket.socket | None = None
        self._buf = b""
        self._capabilities: dict[str, str] = {}

    @property
    def capabilities(self) -> dict[str, str]:
        """The server's most recent capability announcement (post-TLS once negotiated)."""
        return dict(self._capabilities)

    # -- connection ---------------------------------------------------------

    def __enter__(self) -> ManageSieveClient:
        self.connect()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def connect(self) -> None:
        if not SIEVE_MASTER_USER or not SIEVE_MASTER_PASSWORD:
            raise ManageSieveUnavailableError(
                "Sieve master credentials are not configured "
                "(IMAP_MASTER_USER / IMAP_MASTER_PASSWORD)"
            )
        self.open()
        self._authenticate()

    def open(self) -> None:
        """Connect, read the greeting and negotiate STARTTLS -- but do not log in.

        Split from connect() so the availability probe can establish that the
        service is there and willing to take a PLAIN login without needing a
        mailbox to log in as.
        """
        # Every transport failure -- refused, unresolvable, timed out, TLS
        # handshake -- is the same thing to a caller: the service is not
        # there right now. The "Cannot reach" prefix is load-bearing for
        # routes/filters.py, which still matches on it.
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            raise ManageSieveUnavailableError(f"Cannot reach the Sieve service: {exc}") from exc

        self._read_greeting()

        # STARTTLS when offered. Inside the compose network this is one hop,
        # but the master password crosses it, so take the encryption when the
        # server has it rather than deciding the network is trustworthy.
        if "STARTTLS" in self._capabilities:
            self._command("STARTTLS")
            context = ssl.create_default_context()
            # The mail server presents its own certificate for its public
            # hostname; this connection is to the service name on the
            # container network, so hostname verification cannot succeed and
            # is not what is being relied on here.
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            try:
                self._sock = context.wrap_socket(self._sock)
            except OSError as exc:  # ssl.SSLError is an OSError
                raise ManageSieveUnavailableError(
                    f"Cannot reach the Sieve service: TLS negotiation failed: {exc}"
                ) from exc
            self._buf = b""
            self._read_greeting()

    def close(self) -> None:
        if self._sock is None:
            return
        with contextlib.suppress(Exception):
            self._send("LOGOUT")
        with contextlib.suppress(Exception):
            self._sock.close()
        self._sock = None

    # -- commands -----------------------------------------------------------

    def list_scripts(self) -> list[dict]:
        """[{name, active}] for every script belonging to this mailbox."""
        lines, _ = self._command("LISTSCRIPTS")
        scripts = []
        for line in lines:
            match = re.match(rb'^"((?:[^"\\]|\\.)*)"(\s+ACTIVE)?\s*$', line)
            if not match:
                continue
            scripts.append(
                {
                    "name": _unquote(match.group(1)),
                    "active": match.group(2) is not None,
                }
            )
        return scripts

    def get_script(self, name: str) -> str | None:
        if not valid_script_name(name):
            raise ManageSieveError(f"Invalid script name: {name!r}")
        try:
            lines, _ = self._command(f'GETSCRIPT "{name}"')
        except ManageSieveUnavailableError:
            raise
        except ManageSieveError as exc:
            # A script that was never saved is the normal state of a mailbox,
            # not an error. Pigeonhole answers
            #   NO (NONEXISTENT) "Sieve script `mailyte-rules' not found"
            # and this used to match only the RFC's example prose ("does not
            # exist"), so every such mailbox 502'd on GET.
            if _script_missing(exc):
                return None
            raise
        # The script arrives as a literal, which _command has already
        # collected; anything after it is protocol noise.
        return (
            "\n".join(line.decode("utf-8", errors="replace") for line in lines).strip("\r\n") or ""
        )

    def put_script(self, name: str, content: str) -> None:
        """
        Upload a script. Dovecot COMPILES it here, so an invalid script is
        rejected now rather than quietly breaking delivery later -- the
        compiler's message is passed back to the caller.
        """
        if not valid_script_name(name):
            raise ManageSieveError(f"Invalid script name: {name!r}")
        payload = content.encode("utf-8")
        self._command(f'PUTSCRIPT "{name}" {{{len(payload)}+}}', literal=payload)

    def set_active(self, name: str | None) -> None:
        """Activate a script, or pass None to deactivate whatever is active."""
        if name is None:
            self._command('SETACTIVE ""')
            return
        if not valid_script_name(name):
            raise ManageSieveError(f"Invalid script name: {name!r}")
        self._command(f'SETACTIVE "{name}"')

    def delete_script(self, name: str, *, missing_ok: bool = False) -> None:
        """Delete a script. With missing_ok, a script that is not there is not an error.

        Note the server refuses to delete the ACTIVE script (response code
        ACTIVE); deactivate first.
        """
        if not valid_script_name(name):
            raise ManageSieveError(f"Invalid script name: {name!r}")
        try:
            self._command(f'DELETESCRIPT "{name}"')
        except ManageSieveUnavailableError:
            raise
        except ManageSieveError as exc:
            if missing_ok and _script_missing(exc):
                return
            raise

    # -- protocol -----------------------------------------------------------

    def _authenticate(self) -> None:
        # SASL PLAIN: authzid \0 authcid \0 password. authzid is the mailbox
        # being administered; the master user authenticates on its behalf.
        token = base64.b64encode(
            f"{self.account}\0{SIEVE_MASTER_USER}\0{SIEVE_MASTER_PASSWORD}".encode()
        ).decode()
        try:
            self._command(f'AUTHENTICATE "PLAIN" "{token}"')
        except ManageSieveUnavailableError:
            # A dropped connection or timeout mid-login is a transport fact;
            # calling it an authentication failure would send an operator
            # to check a password that was never evaluated.
            raise
        except ManageSieveError as exc:
            # The server's own reason ("Authentication failed.") is kept --
            # it is what distinguishes a wrong master password from a
            # mailbox that does not exist in the SQL passdb. Never the token.
            raise ManageSieveUnavailableError(
                f"Sieve authentication failed for {self.account}: {exc}", code=exc.code
            ) from None

    def _read_greeting(self) -> None:
        lines, _ = self._read_response()
        self._capabilities = {}
        for line in lines:
            match = re.match(rb'^"([A-Z0-9\-]+)"(?:\s+"([^"]*)")?', line)
            if match:
                self._capabilities[match.group(1).decode()] = (
                    match.group(2).decode() if match.group(2) else ""
                )

    def _send(self, text: str) -> None:
        assert self._sock is not None
        self._sendall(text.encode("utf-8") + b"\r\n")

    def _sendall(self, data: bytes) -> None:
        assert self._sock is not None
        try:
            self._sock.sendall(data)
        except OSError as exc:
            raise ManageSieveUnavailableError(f"Cannot reach the Sieve service: {exc}") from exc

    def _recv(self) -> bytes:
        """One recv, with socket errors and timeouts turned into our exception.

        A raw socket.timeout escaping here used to surface as an unhandled
        exception -- a 500 with a traceback instead of a clear 503.
        """
        assert self._sock is not None
        try:
            return self._sock.recv(8192)
        except OSError as exc:
            raise ManageSieveUnavailableError(f"Cannot reach the Sieve service: {exc}") from exc

    def _command(self, command: str, literal: bytes | None = None) -> tuple[list[bytes], bytes]:
        if self._sock is None:
            raise ManageSieveError("Not connected")
        self._send(command)
        if literal is not None:
            # Non-synchronising literal ({n+}) -- no continuation to wait for.
            self._sendall(literal + b"\r\n")
        return self._read_response()

    def _read_line(self) -> bytes:
        while b"\r\n" not in self._buf:
            chunk = self._recv()
            if not chunk:
                raise ManageSieveUnavailableError(
                    "Cannot reach the Sieve service: connection closed unexpectedly"
                )
            self._buf += chunk
        line, self._buf = self._buf.split(b"\r\n", 1)
        return line

    def _read_exact(self, count: int) -> bytes:
        while len(self._buf) < count:
            chunk = self._recv()
            if not chunk:
                raise ManageSieveUnavailableError(
                    "Cannot reach the Sieve service: connection closed mid-literal"
                )
            self._buf += chunk
        data, self._buf = self._buf[:count], self._buf[count:]
        return data

    def _read_response(self) -> tuple[list[bytes], bytes]:
        """
        Collect data lines until the OK/NO/BYE that ends the response.

        Raises on NO/BYE, carrying the server's own message -- for PUTSCRIPT
        that message is the Sieve compiler's error, which is the most useful
        thing we can give a user who has written a bad filter.
        """
        lines: list[bytes] = []
        while True:
            line = self._read_line()

            # A data line may be introduced by a literal length, in which case
            # the payload follows as raw bytes rather than a line.
            literal = re.match(rb"^\{(\d+)\+?\}$", line.strip())
            if literal:
                payload = self._read_exact(int(literal.group(1)))
                # The literal is followed by its own CRLF.
                if self._buf.startswith(b"\r\n"):
                    self._buf = self._buf[2:]
                lines.append(payload)
                continue

            upper = line.upper()
            if upper.startswith(b"OK"):
                return lines, line
            if upper.startswith(b"NO") or upper.startswith(b"BYE"):
                raise self._failure(line)

            lines.append(line)

    def _failure(self, line: bytes) -> ManageSieveError:
        """Build the exception for a NO/BYE line, keeping its response code.

        BYE means the server is closing the connection on us (shutdown, too
        many failures, idle timeout) -- nothing about the command, so it is
        the unavailable flavour.
        """
        code_match = _RESPONSE_CODE_RE.match(line.strip())
        code = code_match.group(1).decode("ascii", errors="replace").upper() if code_match else None
        message = self._failure_message(line)
        if line.upper().startswith(b"BYE"):
            return ManageSieveUnavailableError(
                f"Cannot reach the Sieve service: {message}", code=code
            )
        return ManageSieveError(message, code=code)

    def _failure_message(self, line: bytes) -> str:
        """
        The human-readable reason a command was refused.

        Dovecot returns the Sieve compiler's diagnostics as a LITERAL rather
        than a quoted string (``NO {115}`` followed by the text), so reading
        only the response line yields "{115}" -- which tells a user with a
        broken filter nothing at all. Pull the literal when one is announced.
        """
        trailing = re.search(rb"\{(\d+)\+?\}\s*$", line.strip())
        if trailing:
            payload = self._read_exact(int(trailing.group(1)))
            if self._buf.startswith(b"\r\n"):
                self._buf = self._buf[2:]
            detail = payload.decode("utf-8", errors="replace").strip()
            if detail:
                return detail

        return _response_message(line) or "Sieve command refused"


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def probe_service(
    host: str | None = None,
    port: int | None = None,
    timeout: float | None = None,
    account: str | None = None,
) -> tuple[bool, str]:
    """Can ManageSieve be served right now? Returns (ok, detail).

    Without ``account`` this connects, reads the greeting, negotiates STARTTLS
    when offered and checks that the server advertises SASL PLAIN -- which is
    everything short of the login itself. A login cannot be probed in the
    abstract: the master passdb is ``pass = yes``, so Dovecot goes on to look
    the authzid up in the SQL passdb, and "no such mailbox" is indistinguishable
    from "wrong master password" on the wire. With ``account`` (an existing
    mailbox, e.g. SIEVE_PROBE_ACCOUNT=postmaster@example.com) the probe is a
    full login as that mailbox and therefore also proves the master credential.

    Login failures seen by real requests are fed back through report_failure(),
    so a wrong master password still turns the capability off -- after the
    first request that hits it rather than before.
    """
    client = ManageSieveClient(account or "", host, port, timeout or SIEVE_PROBE_TIMEOUT)
    try:
        if account:
            client.connect()
            return True, f"login as {account} ok"
        client.open()
        offered = client.capabilities.get("SASL", "").upper().split()
        if "PLAIN" not in offered:
            return False, "server does not offer SASL PLAIN on this connection"
        return True, "reachable, PLAIN offered"
    except ManageSieveError as exc:
        return False, str(exc)
    finally:
        client.close()


class _Availability:
    """Process-wide, time-limited memory of whether ManageSieve is usable.

    Cheap on purpose: one probe per SIEVE_PROBE_TTL per process, both
    outcomes cached, and real requests overwrite it with what they actually
    saw. The lock also single-flights the probe -- concurrent cold callers
    wait for the one in progress (bounded by SIEVE_PROBE_TIMEOUT) instead of
    each opening a connection.
    """

    def __init__(self, clock=time.monotonic) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._ok: bool | None = None
        self._detail = ""
        self._checked_at = 0.0

    def current(self, ttl: float, probe) -> bool:
        with self._lock:
            now = self._clock()
            if self._ok is not None and now - self._checked_at < ttl:
                return self._ok
            ok, detail = probe()
            self._set(ok, detail)
            return ok

    def record(self, ok: bool, detail: str = "") -> None:
        with self._lock:
            self._set(ok, detail)

    def reset(self) -> None:
        with self._lock:
            self._ok, self._detail, self._checked_at = None, "", 0.0

    def _set(self, ok: bool, detail: str) -> None:
        if not ok and self._ok is not False:
            logger.warning("ManageSieve unavailable: %s", detail)
        elif ok and self._ok is False:
            logger.info("ManageSieve available again: %s", detail)
        self._ok, self._detail, self._checked_at = ok, detail, self._clock()

    @property
    def detail(self) -> str:
        return self._detail


_availability = _Availability()


def sieve_available() -> bool:
    """Whether the Sieve features can be served right now (cached, see _Availability).

    This is what /mailbox/capabilities must ask, not "are the env vars set":
    a capability that is advertised and then 5xxs is the fail-open the
    webmail contract forbids -- the client renders nothing for a capability
    that is not advertised, and a broken control for one that is.
    """
    return _availability.current(
        SIEVE_PROBE_TTL,
        lambda: probe_service(timeout=SIEVE_PROBE_TIMEOUT, account=SIEVE_PROBE_ACCOUNT or None),
    )


def report_failure(detail: str) -> None:
    """A real request found the service unusable; stop advertising it for a TTL."""
    _availability.record(False, detail)


def report_success() -> None:
    """A real request used the service; that is a better probe than a probe."""
    _availability.record(True, "request succeeded")


def reset_availability() -> None:
    """Forget the cached state (tests, and anything that reconfigures at runtime)."""
    _availability.reset()


def _unquote(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").replace('\\"', '"').replace("\\\\", "\\")


def _response_message(line: bytes) -> str:
    text = line.decode("utf-8", errors="replace")
    quoted = re.search(r'"((?:[^"\\]|\\.)*)"\s*$', text)
    if quoted:
        return quoted.group(1).replace('\\"', '"').replace("\\\\", "\\")
    return text.split(" ", 1)[1].strip() if " " in text else text
