#!/usr/bin/env python3
"""
Move mailbox-holder settings out of mailyte-api and into the mail server.

Part of 04-mailyte-web/02-PRD-webmail-standalone Phase 6. One-time, but
idempotent -- safe to re-run, and safe to run before the cutover so the
window where the two disagree is as short as possible.

    python3 scripts/migrate_mailbox_settings_from_laravel.py --dry-run
    python3 scripts/migrate_mailbox_settings_from_laravel.py --apply

**Rows are matched on EMAIL ADDRESS, never on id.** The two databases assign
their own ULIDs, so the same mailbox has different ids in each -- verified
locally: support@tmahelp.ng is 01M0KXB4ETZ1KDSA82D9RG0XTH in `mailyte` and
01KZP5F12G4PZ3D4K65VMXV6ET in `mailserver`. Copying email_account_id across
would attach a person's settings to a different mailbox, or to none, and the
foreign key would not necessarily catch it.

**Sessions are deliberately NOT migrated.** A session row is a live
credential; carrying tokens between two systems to save people one sign-in is
a bad trade. Everyone signs in again once at cutover.

**Two-factor enrolments are reported, not moved.** Laravel keeps the secret on
email_accounts.two_factor_secret; the mail server keeps it in totp_secrets via
the totp service, under the `mailbox:` namespace. The secrets are both base32
and would technically transfer, but the recovery codes are hashed differently
and a half-migrated second factor is the one thing that must never be guessed
at. Anyone enrolled is listed so they can be asked to re-enrol.
"""

import argparse
import os
import sys

import pymysql

# Source: mailyte-api's database.
SRC = {
    "host": os.getenv("LARAVEL_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("LARAVEL_DB_PORT", "3306")),
    "user": os.getenv("LARAVEL_DB_USER", "root"),
    "password": os.getenv("LARAVEL_DB_PASSWORD", ""),
    "database": os.getenv("LARAVEL_DB_NAME", "mailyte"),
}

# Destination: this mail server's database.
DST = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "mailuser"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "mailserver"),
}

PREFERENCE_COLUMNS = (
    "signature_html",
    "signature_on_reply",
    "display_density",
    "undo_send_enabled",
    "undo_send_seconds",
)


def connect(config: dict, label: str):
    try:
        return pymysql.connect(cursorclass=pymysql.cursors.DictCursor, **config)
    except Exception as exc:
        sys.exit(f"Could not connect to the {label} database ({config['database']}): {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="report, change nothing")
    group.add_argument("--apply", action="store_true", help="write the rows")
    args = parser.parse_args()

    source = connect(SRC, "source (mailyte-api)")
    dest = connect(DST, "destination (mail server)")

    with source.cursor() as cursor:
        cursor.execute(
            """
            SELECT a.email_address, p.signature_html, p.signature_on_reply,
                   p.display_density, p.undo_send_enabled, p.undo_send_seconds
            FROM mailbox_preferences p
            JOIN email_accounts a ON a.id = p.email_account_id
            """
        )
        preferences = cursor.fetchall()

        cursor.execute(
            "SELECT email_address FROM email_accounts "
            "WHERE two_factor_secret IS NOT NULL AND two_factor_enabled = 1"
        )
        enrolled = [row["email_address"] for row in cursor.fetchall()]

    with dest.cursor() as cursor:
        cursor.execute("SELECT id, email FROM email_accounts")
        # Addresses are compared case-insensitively: a mailbox is the same
        # mailbox whether or not someone typed it in capitals.
        by_email = {row["email"].lower(): row["id"] for row in cursor.fetchall()}

    matched, orphaned = [], []
    for row in preferences:
        account_id = by_email.get((row["email_address"] or "").lower())
        (matched if account_id else orphaned).append((account_id, row))

    print(f"  preferences in mailyte-api : {len(preferences)}")
    print(f"  matched to a mailbox here  : {len(matched)}")
    print(f"  no matching mailbox        : {len(orphaned)}")
    for _, row in orphaned:
        # Not an error: a mailbox deleted from the mail server but still
        # carrying settings in Laravel is exactly what this should surface
        # rather than silently drop.
        print(f"      orphaned: {row['email_address']}")

    if enrolled:
        print(f"  two-factor enrolments to RE-DO manually: {len(enrolled)}")
        for address in enrolled:
            print(f"      re-enrol: {address}")
    else:
        print("  two-factor enrolments      : 0")

    if args.dry_run:
        print("\n  dry run -- nothing written.")
        return 0

    written = 0
    with dest.cursor() as cursor:
        for account_id, row in matched:
            # ON DUPLICATE KEY UPDATE keeps this re-runnable: the unique key on
            # email_account_id means a second run refreshes rather than fails.
            # ULID() is not available in MySQL, so the id comes from Python.
            from shared.ulid_utils import generate_ulid  # noqa: PLC0415

            cursor.execute(
                """
                INSERT INTO mailbox_preferences
                    (id, email_account_id, signature_html, signature_on_reply,
                     display_density, undo_send_enabled, undo_send_seconds)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    signature_html = VALUES(signature_html),
                    signature_on_reply = VALUES(signature_on_reply),
                    display_density = VALUES(display_density),
                    undo_send_enabled = VALUES(undo_send_enabled),
                    undo_send_seconds = VALUES(undo_send_seconds)
                """,
                (
                    generate_ulid(),
                    account_id,
                    *(row[column] for column in PREFERENCE_COLUMNS),
                ),
            )
            written += 1
    dest.commit()

    print(f"\n  wrote {written} preference rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
