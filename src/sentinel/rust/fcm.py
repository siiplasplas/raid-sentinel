"""FCM dinleyicisi ve gozcusu.

Neden kutuphanenin kendi FCMListener'ini kullanmiyoruz:
`FCMListener.start()` icinde `self.thread = Thread(...).start()` var -
`start()` None doner, yani thread referansi hep None kaliyor ve thread'in
yasayip yasamadigi disaridan hic kontrol edilemiyor.

`push_receiver.PushReceiver`'i dogrudan kullaninca elimize iki kaldirac
geciyor:

- `time_last_message_received`, alinan HER mesajda (heartbeat dahil)
  guncelleniyor. Gercek bir canlilik sinyali; bildirim gelmemesiyle
  baglantinin olmesini birbirinden ayirmamizi sagliyor.
- Kutuphanenin kendi `__status_check` gozcusu 1 saat sessizlikten sonra
  soketi kapatip yeniden baglaniyor. Yani ilk savunma hatti zaten var.

Bizim gozcumuz ikinci hat: kutuphanenin kendi sifirlamasi da ise
yaramadiysa devreye giriyor (bkz. olijeffers0n/rustplus#75 - 7/24 calisan
botlarda FCM baglantisinin sessizce olmesi). Alarm sistemi icin en kotu
ariza turu bu: her sey calisiyor gorunur, hicbir sey gelmez.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from push_receiver.push_receiver import PushReceiver

from sentinel.rust.notifications import RustNotification, parse

log = logging.getLogger(__name__)

NotificationHandler = Callable[[RustNotification], Awaitable[None]]

_MONITOR_INTERVAL = 60.0
_RESTART_BACKOFF = (5.0, 15.0, 60.0, 180.0)

# Kutuphanenin kendi esigi 1 saat. Onun sifirlamasina sans tanimak icin
# bizim esigimiz belirgin sekilde daha yuksek.
_SILENCE_SOFT_LIMIT = 90 * 60.0
_SILENCE_HARD_LIMIT = 150 * 60.0


class FcmSupervisor:
    def __init__(
        self,
        fcm_credentials: dict[str, Any],
        handler: NotificationHandler,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        if not fcm_credentials:
            raise ValueError("FCM kimlik bilgileri bos - once 'sentinel pair' calistir")

        self._credentials = fcm_credentials
        self._handler = handler
        self._loop = loop

        self._receiver: PushReceiver | None = None
        self._thread: threading.Thread | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._stopping = threading.Event()
        self._restart_count = 0
        self._soft_resets = 0

        self.started_at: float = 0.0
        self.last_notification_at: float = 0.0
        self.notification_count: int = 0

    # --- yasam dongusu -----------------------------------------------------

    async def start(self) -> None:
        self._loop = self._loop or asyncio.get_running_loop()
        self._stopping.clear()
        self._spawn()
        self._monitor_task = asyncio.create_task(self._monitor(), name="fcm-monitor")
        log.info("FCM dinleyicisi baslatildi")

    async def stop(self) -> None:
        self._stopping.set()
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task
            self._monitor_task = None
        self._close_socket()
        # Thread daemon: listen() bloke oldugu icin join beklemiyoruz,
        # surec kapanirken kendisi olur.
        log.info("FCM dinleyicisi durduruldu")

    # --- ic isleyis --------------------------------------------------------

    def _spawn(self) -> None:
        self._receiver = PushReceiver(credentials=self._credentials)
        self._thread = threading.Thread(
            target=self._listen_forever, args=(self._receiver,), name="fcm-listener", daemon=True
        )
        self.started_at = time.time()
        self._thread.start()

    def _listen_forever(self, receiver: PushReceiver) -> None:
        try:
            receiver.listen(callback=self._on_notification)
        except Exception:  # noqa: BLE001 - thread sinirinda her seyi yakalamaliyiz
            if not self._stopping.is_set():
                log.exception("FCM dinleyici thread'i hata ile sonlandi")
        else:
            if not self._stopping.is_set():
                log.warning("FCM dinleyicisi beklenmedik sekilde sonlandi")

    def _on_notification(self, _obj: Any, notification: dict[str, Any], _data: Any) -> None:
        """FCM thread'inde calisir - asyncio dunyasina koprule."""
        self.last_notification_at = time.time()
        self.notification_count += 1

        try:
            parsed = parse(notification)
        except Exception:  # noqa: BLE001 - bozuk bir bildirim dinleyiciyi oldurmesin
            log.exception("FCM bildirimi ayristirilamadi")
            return

        loop = self._loop
        if loop is None or loop.is_closed():
            log.error("Olay dongusu yok, bildirim dusuruldu: %s", parsed.kind)
            return

        asyncio.run_coroutine_threadsafe(self._safe_handle(parsed), loop)

    async def _safe_handle(self, notification: RustNotification) -> None:
        try:
            await self._handler(notification)
        except Exception:  # noqa: BLE001
            log.exception("Bildirim isleyicisi hata verdi (%s)", notification.kind)

    def _silence_seconds(self) -> float:
        """Son alinan mesajin uzerinden gecen sure (heartbeat dahil)."""
        receiver = self._receiver
        if receiver is None:
            return 0.0
        last = getattr(receiver, "time_last_message_received", None)
        if last is None:
            # Kutuphanenin ic yapisi degismis; canlilik olcemiyoruz.
            return 0.0
        return max(0.0, time.time() - float(last))

    def _close_socket(self) -> None:
        """Soketi kapatarak yeniden baglanmaya zorlar.

        Ozel metoda erisiyoruz - kutuphanenin acik bir kapatma yolu yok.
        Kirilgan oldugu icin tek yerde ve korumali.
        """
        receiver = self._receiver
        if receiver is None:
            return
        closer = getattr(receiver, "_PushReceiver__close_socket", None)
        if closer is None:
            log.debug("PushReceiver ic yapisi degismis, soket kapatilamadi")
            return
        with contextlib.suppress(Exception):
            closer()

    async def _monitor(self) -> None:
        while not self._stopping.is_set():
            await asyncio.sleep(_MONITOR_INTERVAL)
            if self._stopping.is_set():
                return

            thread = self._thread
            if thread is not None and not thread.is_alive():
                await self._restart_dead_thread()
                continue

            self._restart_count = 0
            await self._check_silence()

    async def _restart_dead_thread(self) -> None:
        delay = _RESTART_BACKOFF[min(self._restart_count, len(_RESTART_BACKOFF) - 1)]
        self._restart_count += 1
        log.warning(
            "FCM thread'i olmus, %.0f sn sonra yeniden baslatiliyor (deneme %d)",
            delay,
            self._restart_count,
        )
        await asyncio.sleep(delay)
        if not self._stopping.is_set():
            self._spawn()

    async def _check_silence(self) -> None:
        silence = self._silence_seconds()
        if silence < _SILENCE_SOFT_LIMIT:
            self._soft_resets = 0
            return

        if silence < _SILENCE_HARD_LIMIT:
            self._soft_resets += 1
            log.warning(
                "FCM %.0f dk sessiz - soket kapatilip yeniden baglanti zorlaniyor",
                silence / 60,
            )
            self._close_socket()
            return

        # Yumusak sifirlama da tutmadi: yeni bir alici ve thread ac.
        # Eski thread daemon, soket hatasi alinca kendisi olecek.
        log.error(
            "FCM %.0f dk sessiz ve yumusak sifirlama ise yaramadi - dinleyici yeniden kuruluyor",
            silence / 60,
        )
        self._close_socket()
        self._spawn()

    # --- saglik ------------------------------------------------------------

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def healthy(self) -> bool:
        return self.alive and self._silence_seconds() < _SILENCE_SOFT_LIMIT

    def health(self) -> dict[str, Any]:
        return {
            "alive": self.alive,
            "healthy": self.healthy,
            "silence_seconds": round(self._silence_seconds(), 1),
            "running_for_seconds": round(time.time() - self.started_at, 1)
            if self.started_at
            else None,
            "notifications": self.notification_count,
            "last_notification_at": self.last_notification_at or None,
            "restarts": self._restart_count,
            "soft_resets": self._soft_resets,
        }
