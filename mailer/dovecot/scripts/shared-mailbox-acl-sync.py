#!/usr/bin/env python3
"""
Shared-mailbox ACL syncer.

Turns the permission levels set in the dashboard into rights Dovecot actually
enforces, by writing a `dovecot-acl` file into each shared mailbox's Maildir.

WHY A SYNCER AT ALL
    Discovery -- which shared mailboxes to LIST for a user -- is answered
    straight from SQL (acl_shared_dict over the dovecot_shared_mailbox_acl
    view), so it needs no synchronisation. Rights cannot work that way:
    Dovecot's only ACL backend is `vfile`, plain files inside the Maildir.
    There is no SQL ACL backend, and `doveadm acl set` is a ver1 command so it
    is not reachable over the doveadm HTTP API the rest of the platform uses.
    That leaves writing the files, and only this container can -- the API
    service does not mount /var/mail/vhosts, and should not.

WHY POLLING
    A membership change has to reach the filesystem somehow. A poll every few
    seconds over a table this small costs nothing and cannot lose an event,
    which a fire-and-forget notification can. Nothing here writes unless the
    desired file content differs from what is on disk, so a steady state is
    pure reads.

SAFETY
    Every write is atomic (temp file + rename), so a reader never sees a
    half-written ACL file and a crash mid-sync cannot leave one truncated --
    which would silently widen or revoke access.

    A mailbox that stops being shared has its ACL file REMOVED rather than
    left behind: `acl_defaults_from_inbox = yes` means a stale INBOX file
    would keep granting rights on every folder underneath it.
"""

import contextlib
import logging
import os
import sys
import time

import mysql.connector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - shared-mailbox-acl-sync - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

VHOSTS = os.getenv("MAIL_VHOSTS_DIR", "/var/mail/vhosts")
INTERVAL = int(os.getenv("SHARED_MAILBOX_ACL_SYNC_INTERVAL", "15"))
ACL_FILENAME = "dovecot-acl"

# Maildir owner. Matches the vmail uid/gid the image creates and the uid/gid
# user_query hands Dovecot -- an ACL file Dovecot cannot read is an ACL file
# that silently denies everything.
VMAIL_UID = int(os.getenv("VMAIL_UID", "5000"))
VMAIL_GID = int(os.getenv("VMAIL_GID", "5000"))

# Permission level -> Dovecot ACL rights.
#
#   l lookup    r read       w write-flags   s write-seen   t write-deleted
#   i insert    p post       e expunge       k create-child x delete-mailbox
#   a admin
#
# read_only is `lr` alone and deliberately excludes `s`: \Seen is SHARED
# between members (Maildir keeps it in the filename), so granting write-seen
# to an observer would let them mark the team's mail read. The guide describes
# read-only as "a manager who wants visibility without touching the queue".
#
# send_as and send_on_behalf get identical MAILBOX rights -- the difference
# between them is which address appears in the From header when sending, which
# is decided at submission time, not here. Neither gets `e`/`x`: they work the
# queue without being able to destroy it.
RIGHTS = {
    "full_access": "lrwstipekxa",
    "send_as": "lrwstip",
    "send_on_behalf": "lrwstip",
    "read_only": "lr",
}

QUERY = """
    SELECT
        owner.email  AS shared_email,
        member.email AS member_email,
        smm.permission
    FROM shared_mailbox_members smm
    JOIN email_accounts owner
      ON owner.id = smm.shared_mailbox_id
     AND owner.mailbox_type = 'shared'
     AND owner.status = 'active'
    JOIN email_accounts member
      ON member.id = smm.email_account_id
     AND member.status = 'active'
    ORDER BY owner.email, member.email
"""

# Every shared mailbox, including ones with no members left -- those still
# need their ACL file removed.
SHARED_MAILBOXES = """
    SELECT email FROM email_accounts
    WHERE mailbox_type = 'shared' AND status = 'active'
"""


def _connect():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "mysql"),
        port=int(os.getenv("DB_PORT", "3306")),
        database=os.getenv("DB_NAME", "mailserver"),
        user=os.getenv("DB_USER", "mailuser"),
        password=os.getenv("DB_PASSWORD", ""),
        connection_timeout=10,
    )


def maildir_for(email: str) -> str | None:
    """Where user_query says this mailbox lives: /var/mail/vhosts/<domain>/<local>."""
    if "@" not in email:
        return None
    local_part, domain = email.rsplit("@", 1)
    if not local_part or not domain or "/" in email or ".." in email:
        return None
    return os.path.join(VHOSTS, domain, local_part)


def desired_acl(members: list[tuple[str, str]]) -> str:
    """The dovecot-acl file body for one shared mailbox.

    Members whose permission is not one we recognise are skipped rather than
    guessed at -- an unknown level must not silently become full access.
    """
    lines = [
        "# Managed by shared-mailbox-acl-sync.py -- edits here are overwritten.",
        "# Change membership in the dashboard (Mail Setup > Shared Mailboxes).",
    ]
    for member_email, permission in members:
        rights = RIGHTS.get(permission)
        if not rights:
            logger.warning("Unknown permission %r for %s -- skipped", permission, member_email)
            continue
        lines.append(f"user={member_email} {rights}")
    return "\n".join(lines) + "\n"


def ensure_maildir(maildir: str) -> bool:
    """Create the Maildir skeleton if it is not there yet. Returns True if created.

    Without this a brand-new shared mailbox is unreachable until its first
    message arrives, and cannot bootstrap out of it: Dovecot creates the
    Maildir on delivery, the ACL file lives INSIDE that Maildir, and with no
    ACL file there are no rights, so opening the folder to trigger creation is
    itself denied. Members would add themselves to a mailbox that stayed
    invisible for no stated reason.

    cur/new/tmp is the standard Maildir layout Dovecot would create itself, so
    pre-creating it is not racing its format -- an existing, correct Maildir is
    exactly what it expects to find.
    """
    if os.path.isdir(maildir):
        return False

    for subdir in ("", "cur", "new", "tmp"):
        target = os.path.join(maildir, subdir) if subdir else maildir
        os.makedirs(target, exist_ok=True)
        # Already running as vmail, or an environment where it does not apply.
        with contextlib.suppress(PermissionError):
            os.chown(target, VMAIL_UID, VMAIL_GID)
        os.chmod(target, 0o700)

    return True


def write_atomically(path: str, content: str) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    # Already running as vmail, or an environment where it does not apply.
    with contextlib.suppress(PermissionError):
        os.chown(tmp, VMAIL_UID, VMAIL_GID)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def sync_once(cursor) -> int:
    """Bring every shared mailbox's ACL file in line. Returns files changed."""
    cursor.execute(QUERY)
    by_mailbox: dict[str, list[tuple[str, str]]] = {}
    for row in cursor.fetchall():
        by_mailbox.setdefault(row["shared_email"], []).append(
            (row["member_email"], row["permission"])
        )

    cursor.execute(SHARED_MAILBOXES)
    all_shared = [row["email"] for row in cursor.fetchall()]

    changed = 0

    for email in all_shared:
        maildir = maildir_for(email)
        if not maildir:
            logger.warning("Skipping %r -- not a usable mailbox path", email)
            continue

        path = os.path.join(maildir, ACL_FILENAME)
        members = by_mailbox.get(email, [])

        if not members:
            if os.path.isdir(maildir) and os.path.exists(path):
                os.remove(path)
                changed += 1
                logger.info("Removed ACL for %s -- no members remain", email)
            continue

        # Only once there is somebody to grant rights to -- an unshared
        # mailbox gets its Maildir from Dovecot on first delivery as usual.
        if ensure_maildir(maildir):
            logger.info("Created Maildir for %s", email)

        content = desired_acl(members)

        try:
            with open(path) as handle:
                if handle.read() == content:
                    continue
        except FileNotFoundError:
            pass

        write_atomically(path, content)
        changed += 1
        logger.info("Wrote ACL for %s (%d member(s))", email, len(members))

    return changed


def main() -> int:
    logger.info("Starting; vhosts=%s interval=%ss", VHOSTS, INTERVAL)

    while True:
        connection = None
        try:
            connection = _connect()
            cursor = connection.cursor(dictionary=True)
            sync_once(cursor)
            cursor.close()
        except mysql.connector.Error as exc:
            # The database being briefly unreachable must not kill the
            # syncer: existing ACL files stay valid, so the correct response
            # is to leave them alone and try again.
            logger.error("Database error: %s", exc)
        except Exception as exc:  # noqa: BLE001 -- a supervised loop must not exit
            logger.exception("Sync failed: %s", exc)
        finally:
            if connection is not None:
                with contextlib.suppress(Exception):
                    connection.close()

        time.sleep(INTERVAL)


if __name__ == "__main__":
    # `--once` runs a single pass and exits, for deploys and for verifying a
    # change by hand without waiting out the interval.
    if "--once" in sys.argv:
        conn = _connect()
        cur = conn.cursor(dictionary=True)
        count = sync_once(cur)
        cur.close()
        conn.close()
        logger.info("Single pass complete; %d file(s) changed", count)
        sys.exit(0)

    sys.exit(main())
