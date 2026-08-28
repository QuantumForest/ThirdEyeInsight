"""
backend.logging_setup
----------------------
Configure un logger unique pour toute l'application, écrit dans
data/logs/ygoprob.log (rotation simple à 1 Mo x 3 fichiers) plutôt que de
perdre les erreurs dans la console à la fermeture de l'app.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from .paths import LOGS_DIR

_LOGGER_NAME = "ygoprob"
_initialized = False


def get_logger() -> logging.Logger:
    """Retourne le logger applicatif, en le configurant une seule fois (idempotent)."""
    global _initialized
    logger = logging.getLogger(_LOGGER_NAME)

    if not _initialized:
        logger.setLevel(logging.INFO)

        try:
            os.makedirs(LOGS_DIR, exist_ok=True)
            file_handler = RotatingFileHandler(
                os.path.join(LOGS_DIR, "ygoprob.log"),
                maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
            ))
            logger.addHandler(file_handler)
        except OSError:
            pass  # Répertoire non inscriptible : on continue avec la console seulement

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(console_handler)

        _initialized = True

    return logger
