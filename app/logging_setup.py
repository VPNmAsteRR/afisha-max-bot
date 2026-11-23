"""Логирование: файл с ротацией 14 дней + вывод в консоль (п. 6 ТЗ)."""

from __future__ import annotations

import logging
import logging.handlers
import sys

from .config import settings


def setup_logging() -> None:
    settings.ensure_dirs()
    log_file = settings.log_dir / "app.log"

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_file,
        when="midnight",
        backupCount=14,
        encoding="utf-8",
        utc=False,
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(settings.log_level.upper())
    root.addHandler(file_handler)
    root.addHandler(console)

    # Приглушаем слишком шумные библиотеки
    for name in ("httpx", "httpcore", "apscheduler.executors.default", "googleapiclient"):
        logging.getLogger(name).setLevel(logging.WARNING)
