#!/usr/bin/env python3
"""Start the API with independently configurable host, port and learning storage."""

import logging
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root / "src"))

from chatterpal.config.settings import get_settings
from chatterpal.web.api_server import create_api_server


def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    try:
        settings = get_settings()
        server = create_api_server(settings)
        logger.info("API address: http://%s:%s", settings.api_host, settings.api_port)
        server.run(host=settings.api_host, port=settings.api_port, debug=False)
    except KeyboardInterrupt:
        logger.info("Stopping API server")
    except Exception:
        logger.exception("API server failed")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
