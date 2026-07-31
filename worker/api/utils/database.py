#!/usr/bin/env python3
"""
Database utilities for API Gateway
"""

import os
import logging
import mysql.connector
from datetime import datetime

logger = logging.getLogger(__name__)

# Database configuration
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "autocommit": True,
    "charset": "utf8mb4",
}


def get_db_connection():
    """Get database connection with proper error handling"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except mysql.connector.Error as e:
        logger.error(f"Database connection failed: {e}")
        return None


def init_database():
    """Initialize database tables if they don't exist"""
    conn = get_db_connection()
    if not conn:
        logger.error("Cannot initialize database - connection failed")
        return False

    try:
        cursor = conn.cursor()

        # Create API keys table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id INT PRIMARY KEY AUTO_INCREMENT,
                api_key VARCHAR(255) UNIQUE NOT NULL,
                description TEXT,
                permissions VARCHAR(50) DEFAULT 'read',
                admin_access BOOLEAN DEFAULT FALSE,
                read_only BOOLEAN DEFAULT TRUE,
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used TIMESTAMP NULL,
                expires_at TIMESTAMP NULL,
                expires_at TIMESTAMP NULL,
                INDEX idx_api_key (api_key),
                INDEX idx_active (active)
            )
        """)

        # Create domains table if not exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS domains (
                id INT PRIMARY KEY AUTO_INCREMENT,
                domain VARCHAR(255) UNIQUE NOT NULL,
                description TEXT,
                aliases INT DEFAULT 400,
                mailboxes INT DEFAULT 10,
                maxquota BIGINT DEFAULT 10240,
                quota BIGINT DEFAULT 10240,
                defquota INT DEFAULT 3072,
                transport VARCHAR(255) DEFAULT 'virtual',
                backupmx BOOLEAN DEFAULT FALSE,
                active BOOLEAN DEFAULT TRUE,
                created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_domain (domain),
                INDEX idx_active (active)
            )
        """)

        # Create users table if not exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT PRIMARY KEY AUTO_INCREMENT,
                email VARCHAR(255) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                name VARCHAR(255),
                domain VARCHAR(255) NOT NULL,
                quota INT DEFAULT 3072,
                quota_used BIGINT DEFAULT 0,
                local_part VARCHAR(255) NOT NULL,
                active BOOLEAN DEFAULT TRUE,
                created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_email (email),
                INDEX idx_domain (domain),
                INDEX idx_active (active),
                FOREIGN KEY (domain) REFERENCES domains(domain) ON DELETE CASCADE
            )
        """)

        # Create aliases table if not exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS aliases (
                id INT PRIMARY KEY AUTO_INCREMENT,
                address VARCHAR(255) NOT NULL,
                goto TEXT NOT NULL,
                domain VARCHAR(255) NOT NULL,
                active BOOLEAN DEFAULT TRUE,
                created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_address (address),
                INDEX idx_domain (domain),
                INDEX idx_active (active)
            )
        """)

        logger.info("✅ Database tables initialized successfully")
        return True

    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        return False
    finally:
        conn.close()


def log_api_usage(
    api_key, endpoint, method, ip_address, user_agent, response_code, response_time_ms
):
    """Log API usage for monitoring and analytics"""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO api_usage_logs 
            (api_key, endpoint, method, ip_address, user_agent, response_code, response_time_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
            (api_key, endpoint, method, ip_address, user_agent, response_code, response_time_ms),
        )
    except Exception as e:
        logger.error(f"Failed to log API usage: {e}")
    finally:
        conn.close()


def check_rate_limit(api_key, limit_per_hour=1000):
    """Check if API key has exceeded rate limit"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cursor = conn.cursor()

        # Get current hour bucket
        current_hour = datetime.now().replace(minute=0, second=0, microsecond=0)

        # Get current usage for this hour
        cursor.execute(
            """
            SELECT request_count FROM api_rate_limits 
            WHERE api_key = %s AND hour_bucket = %s
        """,
            (api_key, current_hour),
        )

        result = cursor.fetchone()
        current_count = result[0] if result else 0

        if current_count >= limit_per_hour:
            return False

        # Increment counter
        cursor.execute(
            """
            INSERT INTO api_rate_limits (api_key, hour_bucket, request_count)
            VALUES (%s, %s, 1)
            ON DUPLICATE KEY UPDATE request_count = request_count + 1
        """,
            (api_key, current_hour),
        )

        return True

    except Exception as e:
        logger.error(f"Rate limit check failed: {e}")
        return True  # Allow on error
    finally:
        conn.close()
