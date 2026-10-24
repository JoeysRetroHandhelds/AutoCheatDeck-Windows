"""Small rotating log in data/autocheatdeck.log, for diagnosing problems on other machines."""

import logging
import logging.handlers
import os

from . import config

_logger: logging.Logger | None = None


def get() -> logging.Logger:
    global _logger
    if _logger is None:
        _logger = logging.getLogger("autocheatdeck")
        _logger.setLevel(logging.INFO)
        try:
            handler = logging.handlers.RotatingFileHandler(
                os.path.join(config.data_dir(), "autocheatdeck.log"), maxBytes=256 * 1024, backupCount=1,
                encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            _logger.addHandler(handler)
        except OSError:
            _logger.addHandler(logging.NullHandler())
    return _logger
