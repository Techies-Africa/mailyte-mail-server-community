#!/usr/bin/env python3
"""
Mailyte Email Server — Database Migration Manager

Laravel-style CLI for managing Alembic database migrations.

Usage:
    python manage.py migrate                    Run all pending migrations
    python manage.py migrate:rollback           Roll back the last migration
    python manage.py migrate:rollback --steps=3 Roll back N migrations
    python manage.py migrate:status             Show migration status
    python manage.py migrate:create <name>      Create a new migration file
    python manage.py migrate:fresh              Drop all tables and re-run migrations
    python manage.py migrate:reset              Roll back all migrations
    python manage.py migrate:current            Show current revision
    python manage.py db:seed                    Run database seeders (placeholder)
"""

import argparse
import os
import subprocess
import sys

# Ensure project root is on the path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)


def get_alembic_cmd():
    """Get the alembic command — prefer the local venv, fall back to system."""
    venv_alembic = os.path.join(PROJECT_ROOT, ".venv", "bin", "alembic")
    if os.path.exists(venv_alembic):
        return [venv_alembic]
    return [sys.executable, "-m", "alembic"]


def run_alembic(*args):
    """Execute an alembic subcommand, forwarding stdout/stderr."""
    cmd = get_alembic_cmd() + list(args)
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    return result.returncode


def cmd_migrate(args):
    """Run all pending migrations (like `php artisan migrate`)."""
    print("Running migrations...")
    return run_alembic("upgrade", "head")


def cmd_rollback(args):
    """Roll back migrations (like `php artisan migrate:rollback`)."""
    steps = args.steps or 1
    target = f"-{steps}"
    print(f"Rolling back {steps} migration(s)...")
    return run_alembic("downgrade", target)


def cmd_status(args):
    """Show migration status (like `php artisan migrate:status`)."""
    print("Migration status:")
    print("-" * 60)
    return run_alembic("history", "--verbose")


def cmd_current(args):
    """Show current migration revision."""
    print("Current revision:")
    return run_alembic("current", "--verbose")


def cmd_create(args):
    """Create a new migration file (like `php artisan make:migration`)."""
    if not args.name:
        print("Error: Please provide a migration name.")
        print("Usage: python manage.py migrate:create <name>")
        return 1

    name = args.name.replace(" ", "_").lower()
    print(f"Creating migration: {name}")
    return run_alembic("revision", "--autogenerate", "-m", name)


def cmd_fresh(args):
    """Drop all tables and re-run migrations (like `php artisan migrate:fresh`)."""
    print("WARNING: This will drop ALL tables and re-run all migrations!")
    if not args.force:
        confirm = input("Are you sure? Type 'yes' to confirm: ")
        if confirm.lower() != "yes":
            print("Aborted.")
            return 0

    print("Dropping all tables...")
    rc = run_alembic("downgrade", "base")
    if rc != 0:
        print("Error during downgrade.")
        return rc

    print("Running all migrations...")
    return run_alembic("upgrade", "head")


def cmd_reset(args):
    """Roll back all migrations (like `php artisan migrate:reset`)."""
    print("WARNING: This will roll back ALL migrations!")
    if not args.force:
        confirm = input("Are you sure? Type 'yes' to confirm: ")
        if confirm.lower() != "yes":
            print("Aborted.")
            return 0

    print("Rolling back all migrations...")
    return run_alembic("downgrade", "base")


def cmd_seed(args):
    """Run database seeders (placeholder for future implementation)."""
    print("Database seeding is not yet implemented.")
    print("Add seeders in database/seeders/ when ready.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Mailyte Email Server — Database Migration Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # migrate
    sub = subparsers.add_parser("migrate", help="Run all pending migrations")
    sub.set_defaults(func=cmd_migrate)

    # migrate:rollback
    sub = subparsers.add_parser("migrate:rollback", help="Roll back migrations")
    sub.add_argument(
        "--steps", type=int, default=None, help="Number of migrations to roll back (default: 1)"
    )
    sub.set_defaults(func=cmd_rollback)

    # migrate:status
    sub = subparsers.add_parser("migrate:status", help="Show migration status")
    sub.set_defaults(func=cmd_status)

    # migrate:current
    sub = subparsers.add_parser("migrate:current", help="Show current revision")
    sub.set_defaults(func=cmd_current)

    # migrate:create
    sub = subparsers.add_parser("migrate:create", help="Create a new migration file")
    sub.add_argument("name", nargs="?", help="Migration name (e.g. add_user_preferences)")
    sub.set_defaults(func=cmd_create)

    # migrate:fresh
    sub = subparsers.add_parser("migrate:fresh", help="Drop all and re-run migrations")
    sub.add_argument("--force", action="store_true", help="Skip confirmation prompt")
    sub.set_defaults(func=cmd_fresh)

    # migrate:reset
    sub = subparsers.add_parser("migrate:reset", help="Roll back all migrations")
    sub.add_argument("--force", action="store_true", help="Skip confirmation prompt")
    sub.set_defaults(func=cmd_reset)

    # db:seed
    sub = subparsers.add_parser("db:seed", help="Run database seeders")
    sub.set_defaults(func=cmd_seed)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
