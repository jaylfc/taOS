import logging
import os


def configure_logging() -> None:
    root = logging.getLogger()
    log_level = os.environ.get("TAOS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level, logging.INFO)
    if not root.handlers:
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    else:
        root.setLevel(level)
