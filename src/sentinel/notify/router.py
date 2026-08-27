"""Bildirim yonlendiricisi: hangi olay hangi kanala gider.

F1'de kural basit - siddet esigi. F2'de buraya sessiz saatler, eskalasyon
zinciri ve aylik maliyet tavani eklenecek; arayuz ayni kalacak.
"""

from __future__ import annotations

import asyncio
import logging

from sentinel.models import Event
from sentinel.notify.base import Notifier

log = logging.getLogger(__name__)


class NotificationRouter:
    def __init__(self, notifiers: list[Notifier] | None = None) -> None:
        self._notifiers = notifiers or []

    def add(self, notifier: Notifier) -> None:
        self._notifiers.append(notifier)
        log.info("Bildirim kanali eklendi: %r", notifier)

    @property
    def notifiers(self) -> tuple[Notifier, ...]:
        return tuple(self._notifiers)

    async def dispatch(self, event: Event) -> None:
        """Esigi gecen tum kanallara paralel gonderir.

        Bir kanalin hatasi digerlerini etkilemez ve yeni olay uretmez -
        aksi halde bildirim hatasi kendini besleyen bir donguye girer.
        """
        targets = [n for n in self._notifiers if n.accepts(event)]
        if not targets:
            return

        results = await asyncio.gather(
            *(n.send(event) for n in targets), return_exceptions=True
        )
        for notifier, result in zip(targets, results, strict=True):
            if isinstance(result, Exception):
                log.error(
                    "Bildirim gonderilemedi (%s): %s", notifier.name, result, exc_info=result
                )

    async def aclose(self) -> None:
        await asyncio.gather(
            *(n.aclose() for n in self._notifiers), return_exceptions=True
        )
