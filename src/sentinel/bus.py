"""Olay veri yolu: uretici ile tuketicileri birbirinden ayirir.

Rust+ istemcisi olayi buraya birakir; depo, bildirim yonlendiricisi ve
(F5'te) panelin SSE akisi ayni olayi burdan alir. Boylece panel ile bildirim
her zaman ayni gercegi gorur.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

from sentinel.models import Event

log = logging.getLogger(__name__)

Handler = Callable[[Event], Awaitable[None]]

# Yavas bir tuketici yuzunden bellek sismesin: kuyruk dolarsa en eski dusurulur.
_STREAM_MAXSIZE = 256


class EventBus:
    def __init__(self) -> None:
        self._handlers: list[Handler] = []
        self._streams: set[asyncio.Queue[Event]] = set()

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    async def publish(self, event: Event) -> None:
        """Tum tuketicilere dagitir.

        Bir tuketicinin hatasi digerlerini durdurmaz - alarm sisteminde
        Discord'un cokmesi ntfy'yi susturmamali.
        """
        if self._handlers:
            results = await asyncio.gather(
                *(h(event) for h in self._handlers), return_exceptions=True
            )
            for handler, result in zip(self._handlers, results, strict=True):
                if isinstance(result, Exception):
                    name = getattr(handler, "__qualname__", repr(handler))
                    log.exception("Olay tuketicisi hata verdi: %s", name, exc_info=result)

        for queue in list(self._streams):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    async def stream(self) -> AsyncIterator[Event]:
        """Panelin SSE akisi icin. Iptal edildiginde kuyruk temizlenir."""
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=_STREAM_MAXSIZE)
        self._streams.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._streams.discard(queue)

    @property
    def stream_count(self) -> int:
        return len(self._streams)
