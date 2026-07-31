#!/usr/bin/env python3
"""
Database Migration Manager - Alembic Integration

This script provides a wrapper around Alembic for easier migration management.
It handles initialization, migration generation, and application with proper
environment variable loading.

Usage:
    python migrate.py status          # Show migration status
    python migrate.py generate <msg>  # Generate new migration
    python migrate.py upgrade         # Apply pending migrations
    python migrate.py downgrade       # Rollback last migration
    python migrate.py reset           # Reset to base (dangerous)
"""

import os
import subprocess
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def load_env_file():
    """Load environment variables from .env file"""
    env_file = project_root / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())


def run_alembic_command(args):
    """Run alembic command with proper environment"""
    load_env_file()

    # Ensure we're in the project root
    os.chdir(project_root)

    cmd = ["alembic"] + args
    print(f"Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error running alembic command: {e}")
        if e.stdout:
            print("STDOUT:", e.stdout)
        if e.stderr:
            print("STDERR:", e.stderr)
        return False


def status():
    """Show current migration status"""
    print("=== Database Migration Status ===\n")

    # Show current revision
    print("Current revision:")
    run_alembic_command(["current", "-v"])

    print("\nPending migrations:")
    run_alembic_command(["heads"])

    print("\nMigration history:")
    run_alembic_command(["history", "--indicate-current"])


def generate_migration(message):
    """Generate a new migration based on model changes"""
    if not message:
        print("Error: Migration message is required")
        return False

    print(f"Generating migration: {message}")
    return run_alembic_command(["revision", "--autogenerate", "-m", message])


def upgrade():
    """Apply all pending migrations"""
    print("Applying pending migrations...")
    return run_alembic_command(["upgrade", "head"])


def downgrade():
    """Rollback the last migration"""
    print("Rolling back last migration...")
    return run_alembic_command(["downgrade", "-1"])


def reset_database():
    """Reset database to base (removes all data!)"""
    response = input(
        "⚠️  WARNING: This will reset the entire database and remove ALL data!\n"
        "Are you sure you want to continue? Type 'RESET' to confirm: "
    )

    if response != "RESET":
        print("Reset cancelled.")
        return False

    print("Resetting database to base...")
    return run_alembic_command(["downgrade", "base"])


def init_alembic():
    """Initialize Alembic (if not already done)"""
    alembic_dir = project_root / "alembic"
    if alembic_dir.exists():
        print("Alembic already initialized.")
        return True

    print("Initializing Alembic...")
    return run_alembic_command(["init", "alembic"])


def check_environment():
    """Check if required environment variables are set"""
    required_vars = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing_vars = []

    load_env_file()

    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)

    if missing_vars:
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        print("Please check your .env file or environment configuration")
        return False

    print("✅ Environment variables validation passed")
    return True


def main():
    if len(sys.argv) < 2:
        print("Database Migration Manager - Alembic Integration\n")
        print("Usage: python migrate.py <command> [options]\n")
        print("Commands:")
        print("  status              - Show migration status")
        print("  generate <message>  - Generate new migration")
        print("  upgrade             - Apply pending migrations")
        print("  downgrade           - Rollback last migration")
        print("  reset               - Reset database to base (dangerous!)")
        print("  init                - Initialize Alembic")
        print("  check-env           - Check environment variables")
        sys.exit(1)

    command = sys.argv[1]

    # Check environment for most commands
    if command not in ["init", "check-env"]:
        if not check_environment():
            sys.exit(1)

    success = True

    if command == "status":
        status()

    elif command == "generate":
        if len(sys.argv) < 3:
            print("Error: Migration message required")
            print("Usage: python migrate.py generate <message>")
            sys.exit(1)

        message = " ".join(sys.argv[2:])
        success = generate_migration(message)

    elif command == "upgrade":
        success = upgrade()

    elif command == "downgrade":
        success = downgrade()

    elif command == "reset":
        success = reset_database()

    elif command == "init":
        success = init_alembic()

    elif command == "check-env":
        success = check_environment()

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

    if not success:
        print(f"\n❌ Command '{command}' failed")
        sys.exit(1)
    else:
        print(f"\n✅ Command '{command}' completed successfully")


if __name__ == "__main__":
    main()
