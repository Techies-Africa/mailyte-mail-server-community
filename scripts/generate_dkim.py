#!/usr/bin/env python3
"""
DKIM Key Generator

Generates RSA DKIM key pairs for email domains.
- Stores keys in MySQL (dkim_keys table)
- Writes private keys to filesystem for Rspamd
- Updates the Rspamd selector map
- Outputs DNS TXT record for domain configuration

Usage:
    python3 generate_dkim.py <domain> [--selector default] [--key-size 2048]
    python3 generate_dkim.py --all                 # Generate for all active domains
    python3 generate_dkim.py --rotate <domain>     # Rotate key for domain
    python3 generate_dkim.py --dns <domain>        # Show DNS record for domain
"""

import os
import sys
import argparse
import textwrap
import mysql.connector
from datetime import datetime
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from pathlib import Path


# Configuration from environment
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'mysql'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'database': os.getenv('DB_NAME', 'mailserver'),
    'user': os.getenv('DB_USER', 'mailuser'),
    'password': os.getenv('DB_PASSWORD', 'mailpassword'),
}

# Where Rspamd reads DKIM keys
DKIM_KEY_DIR = os.getenv('DKIM_KEY_DIR', '/var/lib/rspamd/dkim')

# Rspamd selector map file
SELECTOR_MAP_PATH = os.getenv('DKIM_SELECTOR_MAP', '/etc/rspamd/dkim_selectors.map')


def get_db():
    return mysql.connector.connect(**DB_CONFIG)


def generate_key_pair(key_size=2048):
    """Generate an RSA key pair and return (private_pem, public_pem)."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=key_size,
        backend=default_backend()
    )

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    ).decode('utf-8')

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode('utf-8')

    return private_pem, public_pem


def public_pem_to_dns_value(public_pem):
    """Extract the base64 key data from a PEM public key for DNS TXT record."""
    lines = public_pem.strip().split('\n')
    # Remove BEGIN/END lines
    key_data = ''.join(line for line in lines if not line.startswith('-----'))
    return key_data


def generate_dkim_for_domain(domain, selector='default', key_size=2048):
    """Generate DKIM keys for a domain and store them."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    # Look up domain_id
    cursor.execute("SELECT id FROM domains WHERE domain = %s AND active = 1", (domain,))
    row = cursor.fetchone()
    if not row:
        print(f"Error: Domain '{domain}' not found or not active in database")
        cursor.close()
        conn.close()
        return False

    domain_id = row['id']

    # Generate key pair
    private_pem, public_pem = generate_key_pair(key_size)
    dns_value = public_pem_to_dns_value(public_pem)

    # Deactivate existing keys for this domain/selector
    cursor.execute(
        "UPDATE dkim_keys SET active = 0 WHERE domain_id = %s AND selector = %s",
        (domain_id, selector)
    )

    # Insert new key
    now = datetime.now()
    cursor.execute(
        "INSERT INTO dkim_keys (domain_id, selector, private_key, public_key, active, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, 1, %s, %s)",
        (domain_id, selector, private_pem, public_pem, now, now)
    )
    conn.commit()

    # Write private key to filesystem for Rspamd
    key_dir = Path(DKIM_KEY_DIR)
    key_dir.mkdir(parents=True, exist_ok=True)

    key_file = key_dir / f"{domain}.{selector}.key"
    key_file.write_text(private_pem)
    key_file.chmod(0o640)

    # Update selector map
    update_selector_map(domain, selector)

    cursor.close()
    conn.close()

    # Output DNS record
    dns_record = format_dns_record(domain, selector, dns_value)
    print(f"DKIM key generated for {domain} (selector: {selector})")
    print()
    print("Add this DNS TXT record:")
    print(dns_record)
    print()

    return True


def format_dns_record(domain, selector, dns_value):
    """Format a DKIM DNS TXT record."""
    # Split into 255-char chunks for DNS TXT record compliance
    chunks = textwrap.wrap(dns_value, 255)
    txt_value = ' '.join(f'"{chunk}"' for chunk in chunks)

    record_name = f"{selector}._domainkey.{domain}"
    record_value = f'"v=DKIM1; k=rsa; p={dns_value}"'

    return f"{record_name} IN TXT {record_value}"


def update_selector_map(domain=None, selector=None):
    """Update the Rspamd DKIM selector map from database."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT d.domain, dk.selector
        FROM dkim_keys dk
        JOIN domains d ON dk.domain_id = d.id
        WHERE dk.active = 1 AND d.active = 1
    """)

    map_path = Path(SELECTOR_MAP_PATH)
    map_path.parent.mkdir(parents=True, exist_ok=True)

    with open(map_path, 'w') as f:
        f.write("# DKIM selector map — auto-generated by generate_dkim.py\n")
        f.write(f"# Updated: {datetime.now().isoformat()}\n")
        for row in cursor.fetchall():
            f.write(f"{row['domain']} {row['selector']}\n")

    cursor.close()
    conn.close()


def show_dns_record(domain):
    """Show the DNS TXT record for a domain's active DKIM key."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT dk.selector, dk.public_key
        FROM dkim_keys dk
        JOIN domains d ON dk.domain_id = d.id
        WHERE d.domain = %s AND dk.active = 1
        ORDER BY dk.created_at DESC LIMIT 1
    """, (domain,))

    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row:
        print(f"No active DKIM key found for {domain}")
        return False

    dns_value = public_pem_to_dns_value(row['public_key'])
    print(format_dns_record(domain, row['selector'], dns_value))
    return True


def generate_all():
    """Generate DKIM keys for all active domains that don't have one."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT d.domain
        FROM domains d
        WHERE d.active = 1
        AND d.id NOT IN (
            SELECT domain_id FROM dkim_keys WHERE active = 1
        )
    """)

    domains = [row['domain'] for row in cursor.fetchall()]
    cursor.close()
    conn.close()

    if not domains:
        print("All active domains already have DKIM keys")
        return

    print(f"Generating DKIM keys for {len(domains)} domains...")
    for domain in domains:
        generate_dkim_for_domain(domain)
        print("---")


def rotate_key(domain, selector='default'):
    """Rotate DKIM key for a domain (generates new key, deactivates old)."""
    print(f"Rotating DKIM key for {domain}...")
    return generate_dkim_for_domain(domain, selector)


def main():
    parser = argparse.ArgumentParser(description='DKIM Key Generator for Mailyte')
    parser.add_argument('domain', nargs='?', help='Domain to generate DKIM key for')
    parser.add_argument('--selector', default='default', help='DKIM selector (default: "default")')
    parser.add_argument('--key-size', type=int, default=2048, help='RSA key size (default: 2048)')
    parser.add_argument('--all', action='store_true', help='Generate keys for all active domains without one')
    parser.add_argument('--rotate', metavar='DOMAIN', help='Rotate DKIM key for domain')
    parser.add_argument('--dns', metavar='DOMAIN', help='Show DNS record for domain')
    parser.add_argument('--update-map', action='store_true', help='Rebuild the selector map from database')

    args = parser.parse_args()

    if args.all:
        generate_all()
    elif args.rotate:
        rotate_key(args.rotate, args.selector)
    elif args.dns:
        show_dns_record(args.dns)
    elif args.update_map:
        update_selector_map()
        print(f"Selector map updated: {SELECTOR_MAP_PATH}")
    elif args.domain:
        generate_dkim_for_domain(args.domain, args.selector, args.key_size)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
