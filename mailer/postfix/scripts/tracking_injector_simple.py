#!/usr/bin/env python3
"""
Simple Tracking Injector for Postfix

A lightweight version of the tracking injector that works as a Postfix filter.
This script injects tracking into emails by calling the tracking service API.
"""

import logging
import os
import subprocess
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("/var/log/postfix_tracking_simple.log")],
)
logger = logging.getLogger("postfix_tracking_simple")


def main():
    """
    Simple pipe filter that injects tracking and forwards to final delivery.

    This script:
    1. Receives email content from Postfix
    2. Calls the full tracking injector
    3. Forwards the result back to Postfix for delivery
    """
    try:
        # Read the original email
        email_content = sys.stdin.buffer.read()

        if not email_content:
            logger.error("No email content received")
            sys.exit(75)  # EX_TEMPFAIL

        # Call the main tracking injector
        tracking_injector_path = "/usr/local/bin/tracking_injector.py"

        if not os.path.exists(tracking_injector_path):
            logger.warning("Tracking injector not found, passing email through")
            # Forward to delivery without tracking
            delivery_cmd = ["/usr/sbin/sendmail", "-G", "-i"] + sys.argv[1:]
            proc = subprocess.Popen(delivery_cmd, stdin=subprocess.PIPE)
            proc.communicate(input=email_content)
            sys.exit(proc.returncode)

        # Process with tracking injector
        proc = subprocess.Popen(
            ["/usr/bin/python3", tracking_injector_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        modified_content, error_output = proc.communicate(input=email_content)

        if proc.returncode != 0:
            logger.error(f"Tracking injector failed: {error_output.decode()}")
            # Fall back to original content
            modified_content = email_content

        # Forward the processed email for final delivery
        delivery_cmd = ["/usr/sbin/sendmail", "-G", "-i"] + sys.argv[1:]
        delivery_proc = subprocess.Popen(delivery_cmd, stdin=subprocess.PIPE)
        delivery_proc.communicate(input=modified_content)

        logger.info("Email processed and forwarded for delivery")
        sys.exit(delivery_proc.returncode)

    except Exception as e:
        logger.error(f"Error in tracking filter: {e}")
        sys.exit(75)  # EX_TEMPFAIL


if __name__ == "__main__":
    main()
