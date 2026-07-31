#!/usr/bin/env python3
"""
Mailyte Mail Server - Main Entry Point
"""

import os
import sys
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from shared.logging_config import setup_logging


def main():
    """Main entry point for the Mailyte Mail Server"""

    # Setup logging
    logger = setup_logging()
    logger.info("Starting Mailyte Mail Server")

    # Load environment variables
    from dotenv import load_dotenv

    # Load development environment if in development mode
    if os.getenv("DEVELOPMENT_MODE", "false").lower() == "true":
        env_file = ".env.development"
        if os.path.exists(env_file):
            load_dotenv(env_file)
            logger.info(f"Loaded development environment from {env_file}")
        else:
            logger.warning(f"Development environment file {env_file} not found")
    else:
        load_dotenv()

    # Import and start the API server
    try:
        import uvicorn

        from worker.api.app import app

        # Use 0.0.0.0 for Replit compatibility
        host = os.getenv("HOST", "0.0.0.0")
        port = int(os.getenv("PORT", 5000))

        logger.info(f"Starting API server on {host}:{port}")
        uvicorn.run(app, host=host, port=port, reload=True)

    except ImportError as e:
        logger.error(f"Failed to import required modules: {e}")
        logger.error("Make sure all required packages are installed")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
