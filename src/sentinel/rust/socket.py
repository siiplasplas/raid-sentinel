"""Rust+ WebSocket baglantisinin gozetimi.

Kutuphanede otomatik yeniden baglanma yok: `ws.py::run()` icinde
`ConnectionClosedError` yakalaninca dongu `break` ediyor ve bir daha
denemiyor. 7/24 calisacak bir alarm sistemi icin bu kabul edilemez, o yuzden
yeniden baglanma, yeniden abone olma ve saglik kontrolu burada.

Ayrica: bir entity'ye abone olabilmek icin once o entity uzerinde
`get_entity_info` cagrilmasi sart - sunucu aboneligi ancak o zaman aciyor.
Yeniden baglandiktan sonra bu adim tekrarlanmazsa olaylar sessizce gelmez.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from rustplus import EntityEvent, EntityEventPayload, RustError, RustSocket, ServerDetails

from sentinel.rust.credentials import PairedServer

log = logging.getLogger(__name__)

EntityChangeHandler = Callable[[int, EntityEventPayload], Awaitable[None]]
StatusHandler = Callable[[bool, str], Awaitable[None]]

_CONNECT_BACKOFF = (5.0, 15.0, 30.0, 60.0, 120.0, 300.0)


class RustSocketSupervisor:
    def __init__(
        self,
        server: PairedServer,
        *,
        on_entity_change: EntityChangeHandler,
        on_status: StatusHandler | None = None,
        healthcheck_interval: float = 300.0,
        use_proxy: bool = False,
    ) -> None:
        self._server = server
        self._on_entity_change = on_entity_change
        self._on_status = on_status
        self._healthcheck_interval = healthcheck_interval
        self._use_proxy = use_proxy

        self._details = ServerDetails(
            ip=server.ip,
            port=server.port,
            player_id=int(server.player_id),
            player_token=int(server.player_token),
        )
        self._socket = self._new_socket()

        self._tracked: set[int] = set()
        self._listeners: dict[int, Any] = {}
        self._connected = False
        self._stopping = asyncio.Event()
        self._health_task: asyncio.Task[None] | None = None

        self.connected_at: float = 0.0
        self.last_ok_at: float = 0.0
        self.reconnects: int = 0

    # --- yasam dongusu -----------------------------------------------------

    async def start(self) -> None:
        self._stopping.clear()
        await self._connect_with_retry()
        self._health_task = asyncio.create_task(self._healthcheck_loop(), name="rust-health")

    async def stop(self) -> None:
        self._stopping.set()
        if self._health_task is not None:
            self._health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_task
            self._health_task = None

        for entity_id, listener in list(self._listeners.items()):
            with contextlib.suppress(Exception):
                EntityEventPayload.HANDLER_LIST.unregister(listener, self._details)
            self._listeners.pop(entity_id, None)

        if self._connected:
            with contextlib.suppress(Exception):
                await self._socket.disconnect()
            self._connected = False
        log.info("Rust+ baglantisi kapatildi")

    def _new_socket(self) -> RustSocket:
        return RustSocket(self._details, use_fp_proxy=self._use_proxy)

    # --- baglanti ----------------------------------------------------------

    async def _connect_with_retry(self) -> bool:
        attempt = 0
        while not self._stopping.is_set():
            try:
                await self._socket.connect()
            except Exception as exc:  # noqa: BLE001 - her hata turu yeniden denemeyi hak ediyor
                delay = _CONNECT_BACKOFF[min(attempt, len(_CONNECT_BACKOFF) - 1)]
                attempt += 1
                log.warning(
                    "Rust+ baglantisi kurulamadi (%s), %.0f sn sonra tekrar", exc, delay
                )
                await self._notify_status(False, f"Baglanti kurulamadi: {exc}")
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                except TimeoutError:
                    continue
                return False

            self._connected = True
            self.connected_at = time.time()
            self.last_ok_at = time.time()
            log.info("Rust+ baglantisi kuruldu: %s", self._server.server_id)
            await self._notify_status(True, "Baglanti kuruldu")
            await self._resubscribe_all()
            return True
        return False

    async def _reconnect(self, reason: str) -> None:
        if self._stopping.is_set():
            return
        self.reconnects += 1
        self._connected = False
        log.warning("Yeniden baglaniliyor (%s) - toplam %d", reason, self.reconnects)
        await self._notify_status(False, reason)

        with contextlib.suppress(Exception):
            await self._socket.disconnect()

        # Yeni bir soket ornegi: eski ornegin ic durumu kapali baglantiya ait.
        self._socket = self._new_socket()
        await self._connect_with_retry()

    async def _notify_status(self, up: bool, reason: str) -> None:
        if self._on_status is None:
            return
        with contextlib.suppress(Exception):
            await self._on_status(up, reason)

    # --- entity abonelikleri ----------------------------------------------

    async def track_entity(self, entity_id: int) -> bool:
        """Bir cihazi izlemeye alir. Yeniden baglanmalarda otomatik yenilenir."""
        self._tracked.add(entity_id)

        if entity_id not in self._listeners:
            handler = self._make_handler(entity_id)
            # EntityEvent bir dekorator fabrikasi; programatik cagriyoruz.
            self._listeners[entity_id] = EntityEvent(self._details, entity_id)(handler)

        return await self._subscribe(entity_id)

    def _make_handler(self, entity_id: int) -> Callable[[EntityEventPayload], Awaitable[None]]:
        async def handler(event: EntityEventPayload) -> None:
            try:
                await self._on_entity_change(entity_id, event)
            except Exception:  # noqa: BLE001
                log.exception("Entity olayi islenirken hata (id=%s)", entity_id)

        handler.__qualname__ = f"entity_handler_{entity_id}"
        return handler

    async def _subscribe(self, entity_id: int) -> bool:
        """Sunucu tarafinda aboneligi acar.

        `get_entity_info` cagrisi sart - onsuz `AppEntityChanged` yayini
        gelmiyor. Bu sira kutuphanenin dokumantasyonunda da belirtilmis.
        """
        try:
            info = await self._socket.get_entity_info(entity_id)
        except Exception as exc:  # noqa: BLE001
            log.error("Entity bilgisi alinamadi (id=%s): %s", entity_id, exc)
            return False

        if isinstance(info, RustError):
            log.error(
                "Entity bilgisi reddedildi (id=%s): %s / %s", entity_id, info.method, info.reason
            )
            return False

        try:
            await self._socket.set_subscription_to_entity(entity_id, True)
        except Exception as exc:  # noqa: BLE001
            log.error("Entity aboneligi acilamadi (id=%s): %s", entity_id, exc)
            return False

        log.info("Cihaz izleniyor: %s (deger=%s)", entity_id, getattr(info, "value", None))
        return True

    async def _resubscribe_all(self) -> None:
        if not self._tracked:
            return
        ok = 0
        for entity_id in sorted(self._tracked):
            if await self._subscribe(entity_id):
                ok += 1
        log.info("Abonelikler yenilendi: %d/%d", ok, len(self._tracked))

    # --- saglik ------------------------------------------------------------

    async def _healthcheck_loop(self) -> None:
        """Sessiz arizaya karsi tek savunma.

        Baglanti kopmus gorunmeden de olebilir; duzenli olarak gercek bir
        istek atip cevap alabildigimizi dogruluyoruz.
        """
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._healthcheck_interval
                )
                return
            except TimeoutError:
                pass

            if not self._connected:
                continue

            try:
                info = await self._socket.get_info()
            except Exception as exc:  # noqa: BLE001
                await self._reconnect(f"Saglik kontrolu hata verdi: {exc}")
                continue

            if isinstance(info, RustError):
                await self._reconnect(f"Saglik kontrolu reddedildi: {info.reason}")
                continue

            self.last_ok_at = time.time()
            log.debug("Saglik kontrolu tamam: %s", getattr(info, "name", "?"))

    @property
    def connected(self) -> bool:
        return self._connected

    def health(self) -> dict[str, Any]:
        return {
            "connected": self._connected,
            "server": self._server.server_id,
            "connected_for_seconds": round(time.time() - self.connected_at, 1)
            if self.connected_at
            else None,
            "last_ok_at": self.last_ok_at or None,
            "reconnects": self.reconnects,
            "tracked_entities": len(self._tracked),
        }
