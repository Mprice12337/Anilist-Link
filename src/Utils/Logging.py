"""Logging configuration for the application."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

NOISY_LOGGERS = [
    "selenium",
    "urllib3",
    "httpx",
    "httpcore",
    "undetected_chromedriver",
    "asyncio",
    "aiosqlite",
]


def setup_logging(debug: bool = False, log_path: Path | None = None) -> None:
    """Configure root logger with console and optional file handlers."""
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)

    # Clear existing handlers to avoid duplicates on re-init
    root.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT)

    # Console handler (always on)
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    root.addHandler(console)

    # File handler with rotation
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Suppress noisy third-party loggers
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger."""
    return logging.getLogger(name)
