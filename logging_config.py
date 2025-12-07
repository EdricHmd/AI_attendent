import logging
import logging.handlers
import os


def setup_logging(log_file: str = "logs/face_ai.log"):
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    ch.setFormatter(ch_formatter)

    # File handler with rotation
    fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    fh.setFormatter(fh_formatter)

    # avoid duplicate handlers
    if not logger.handlers:
        logger.addHandler(ch)
        logger.addHandler(fh)

    return logger
