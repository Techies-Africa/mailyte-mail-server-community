"""
FastAPI database dependency — provides MySQL connections to route handlers.
Usage:
    @router.get("/items")
    async def list_items(db = Depends(get_db)):
        cursor = db.cursor(dictionary=True)
        ...
"""

import os
import logging
import mysql.connector
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'mysql'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'database': os.getenv('DB_NAME', 'mailserver'),
    'user': os.getenv('DB_USER', 'mailuser'),
    'password': os.getenv('DB_PASSWORD', 'mailpassword'),
    'charset': 'utf8mb4',
}


def get_db():
    """FastAPI dependency that yields a MySQL connection and auto-closes it."""
    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_db_ctx():
    """Context manager for non-FastAPI use (scripts, background tasks)."""
    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        yield conn
    finally:
        conn.close()
