"""
Miracle Cloud Gateway -- Centralized logging.

Provides the singleton `log` for all modules to import. Configures the
root logger to write to LOG_PATH with a uniform format. Import as:

    from logger import log
    log.info("hello")

Configuration is done at import time so the first import wins. Safe to
import from any module (no dependency on Flask, the DB, etc).
"""

import logging

from config import LOG_PATH

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

log = logging.getLogger("miracle-router")
