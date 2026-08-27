"""Log kurulumu. Konsol icin okunabilir, dosya icin ayristirilabilir."""

from __future__ import annotations

import contextlib
import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-7s %(name)-24s %(message)s"
_DATEFMT = "%H:%M:%S"


def setup_logging(level: str = "INFO") -> None:
    # Windows konsolu varsayilan olarak UTF-8 degil; bolge adlari ve
    # bildirim govdeleri Turkce karakter tasiyor. Kodlanamayan bir karakter
    # yuzunden log yazarken cokmek, alarm sistemi icin kabul edilemez.
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Kutuphane gurultusunu kis; kendi loglarimiz kaybolmasin.
    for noisy in ("httpx", "httpcore", "websockets", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
