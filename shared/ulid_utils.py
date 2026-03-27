"""ULID generation utility for Mailyte.

ULIDs (Universally Unique Lexicographically Sortable Identifiers) are 26-character
strings that are globally unique, sortable by creation time, and URL-safe.
They replace auto-increment INT and VARCHAR primary keys across all tables.

Example ULID: 01ARZ3NDEKTSV4RRFFQ69G5FAV
"""
from ulid import ULID


def generate_ulid() -> str:
    """Generate a new ULID string (26 chars, sortable, globally unique)."""
    return str(ULID())


def is_valid_ulid(value: str) -> bool:
    """Check if a string is a valid ULID."""
    if not value or len(value) != 26:
        return False
    try:
        ULID.from_str(value)
        return True
    except (ValueError, TypeError):
        return False
