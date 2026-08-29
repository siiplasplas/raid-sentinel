"""Raid toplayici: ham tetiklemeleri anlamli olaylara cevirir.

Ham alarm akisini oldugu gibi Discord'a dokmek ise yaramaz - bir raid
sirasinda saniyeler icinde onlarca tetikleme gelir ve kanal okunmaz hale
gelir. Daha kotusu: her tetikleme icin telefon calarsa fatura patlar.

Burada tetiklemeler bolgeye gore oturumlarda toplanir ve uc olay uretilir:
ilk temas (RAID_STARTED), duzenli ilerleme ozeti (RAID_PROGRESS) ve sessizlik
sonrasi kapanis (RAID_ENDED).

F2'deki skorlama motoru bu sinifin uzerine binecek: sahte alarm filtresi,
eskalasyon ve arama kararlari oturum durumuna bakarak verilecek.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sentinel.i18n import Lang, language, t
from sentinel.models import Event, EventKind, SensorKind, Severity
from sentinel.raiddata import SEISMIC_AVG_SULFUR, SeismicLevel

log = logging.getLogger(__name__)

EventEmitter = Callable[[Event], Awaitable[None]]

# Her tetiklemeden sonra cagrilir. Telefon zinciri karari bunun uzerine
# kuruluyor - toplayici o karari bilmiyor.
SessionObserver = Callable[["RaidSession"], Awaitable[None]]

# Olayin siddetini belirler. Toplayici puanlama kurallarini bilmez;
# enjekte edilmezse "her sey kritik" varsayimina duser ve tek bir sahte
# HBHF tetiklemesi gece 4'te ntfy acil push'u gonderir. Bu yuzden
# uygulama tehdit degerlendirmesini buraya baglar.
SeverityResolver = Callable[["RaidSession"], Severity]

# Olay govdesine eklenecek ek bilgi (ornegin TC'ye ETA). Toplayici us
# modelini bilmez; uygulama bagladiginda bildirimlerde gorunur.
DetailResolver = Callable[["RaidSession"], str]

# Olayin `raw` alanina eklenecek baglam (tehdit, ETA, sulfur...). Bildirim
# kanallari bunu okuyup zengin gosterim uretiyor.
ContextResolver = Callable[["RaidSession"], dict[str, object]]

DEFAULT_ZONE = t("zone.unknown")

_SWEEP_INTERVAL = 15.0

# Hiz tahmininde kullanilacak en fazla olcum sayisi
_MAX_INTERVALS = 20


@dataclass(slots=True)
class RaidSession:
    """Tek bir bolgedeki suregelen saldiri."""

    zone: str
    started_at: float
    last_trigger_at: float
    trigger_count: int = 0
    levels: Counter[int] = field(default_factory=Counter)
    entities: set[str] = field(default_factory=set)
    last_progress_at: float = 0.0
    # Ardisik tetiklemeler arasi saniye. ETA'nin hiz tahmini buna dayaniyor;
    # oturum ortalamasi degil son olcumler kullanilir ki hizlanma/yavaslama
    # gorunsun.
    intervals: list[float] = field(default_factory=list)
    # Tetiklemeyi ureten sensor turleri. Bildirim metni buna gore
    # yaziliyor - HBHF tetiklemesine 'C4 patladi' demek yalan olur.
    kinds: set[str] = field(default_factory=set)

    @property
    def duration(self) -> float:
        return max(0.0, self.last_trigger_at - self.started_at)

    @property
    def explosive_triggers(self) -> int:
        """Yalnizca sismik kademesi bilinen (yani patlama olan) tetiklemeler."""
        return sum(self.levels.values())

    @property
    def estimated_sulfur(self) -> int:
        """Kademe basina ortalama maliyetle kaba tahmin.

        Kademe bilinmiyorsa (duz alarm, sismik yok) sifir sayilir -
        uydurmaktansa eksik gostermeyi tercih ediyoruz.
        """
        total = 0
        for level, count in self.levels.items():
            try:
                tier = SeismicLevel(level)
            except ValueError:
                continue
            total += SEISMIC_AVG_SULFUR.get(tier, 0) * count
        return total

    @property
    def heaviest_level(self) -> SeismicLevel | None:
        if not self.levels:
            return None
        return SeismicLevel(max(self.levels))

    def rate_per_minute(self) -> float:
        if self.duration < 1.0:
            return 0.0
        return self.trigger_count / (self.duration / 60.0)

    @property
    def only_presence(self) -> bool:
        """Butun tetiklemeler hareket sensorunden mi geldi."""
        return bool(self.kinds) and self.kinds == {str(SensorKind.PRESENCE)}

    def describe(self) -> str:
        parts = [t("desc.triggers", n=self.trigger_count)]

        level = self.heaviest_level
        if self.only_presence:
            # Hareket sensoru patlama gormez; kademe elle atanmış olsa bile
            # "C4 patladi" demek yanlis olur.
            parts.append(t("desc.presence"))
        elif level is not None:
            keys = {
                SeismicLevel.LIGHT: "weapon.light",
                SeismicLevel.MEDIUM: "weapon.medium",
                SeismicLevel.HEAVY: "weapon.heavy",
            }
            label = t(keys[level])
            if str(SensorKind.EXPLOSION) not in self.kinds:
                # Sismik oldugu doğrulanmadı - kademe kullanicinin beyani
                label += t("desc.manual")
            parts.append(t("desc.heaviest", label=label))

        if self.duration >= 60:
            parts.append(t("desc.minutes", n=f"{self.duration / 60:.0f}"))
        elif self.duration >= 1:
            parts.append(t("desc.seconds", n=f"{self.duration:.0f}"))

        sulfur = self.estimated_sulfur
        if sulfur:
            # Turkce binlik ayraci nokta, Ingilizce virgul.
            grouped = f"{sulfur:,}"
            if language() is Lang.TR:
                grouped = grouped.replace(",", ".")
            parts.append(f"~{grouped} sulfur")

        return " · ".join(parts)


class RaidAggregator:
    def __init__(
        self,
        emit: EventEmitter,
        *,
        progress_interval: float = 60.0,
        quiet_timeout: float = 300.0,
        sweep_interval: float = _SWEEP_INTERVAL,
        on_session_update: SessionObserver | None = None,
        severity_for: SeverityResolver | None = None,
        detail_for: DetailResolver | None = None,
        context_for: ContextResolver | None = None,
    ) -> None:
        self._emit = emit
        self._on_session_update = on_session_update
        self._severity_for = severity_for
        self._detail_for = detail_for
        self._context_for = context_for
        self._progress_interval = progress_interval
        self._quiet_timeout = quiet_timeout
        self._sweep_interval = sweep_interval
        self._sessions: dict[str, RaidSession] = {}
        self._sweeper: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    # --- yasam dongusu -----------------------------------------------------

    async def start(self) -> None:
        self._stopping.clear()
        self._sweeper = asyncio.create_task(self._sweep_loop(), name="raid-sweeper")

    async def stop(self) -> None:
        self._stopping.set()
        if self._sweeper is not None:
            self._sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sweeper
            self._sweeper = None

    # --- giris -------------------------------------------------------------

    async def feed(
        self,
        *,
        zone: str | None,
        entity_name: str = "",
        entity_id: int | None = None,
        seismic_level: int | None = None,
        sensor_kind: str | None = None,
        ts: float | None = None,
    ) -> None:
        """Bir alarm tetiklemesini oturuma isler."""
        now = ts or time.time()
        key = zone or DEFAULT_ZONE

        session = self._sessions.get(key)
        if session is None:
            session = RaidSession(zone=key, started_at=now, last_trigger_at=now)
            self._sessions[key] = session
            self._update(session, now, entity_name, seismic_level, sensor_kind)
            await self._emit_started(session, entity_id)
            await self._observe(session)
            return

        self._update(session, now, entity_name, seismic_level, sensor_kind)

        if now - session.last_progress_at >= self._progress_interval:
            session.last_progress_at = now
            await self._emit_progress(session)

        await self._observe(session)

    async def _observe(self, session: RaidSession) -> None:
        """Gozlemciyi cagirir. Hatasi olay akisini durdurmamali."""
        if self._on_session_update is None:
            return
        try:
            await self._on_session_update(session)
        except Exception:  # noqa: BLE001
            log.exception("Oturum gozlemcisi hata verdi (%s)", session.zone)

    def _update(
        self,
        session: RaidSession,
        now: float,
        entity_name: str,
        seismic_level: int | None,
        sensor_kind: str | None = None,
    ) -> None:
        if session.trigger_count > 0:
            gap = max(0.0, now - session.last_trigger_at)
            session.intervals.append(gap)
            del session.intervals[:-_MAX_INTERVALS]

        session.last_trigger_at = now
        session.trigger_count += 1
        if entity_name:
            session.entities.add(entity_name)
        if seismic_level is not None:
            session.levels[seismic_level] += 1
        if sensor_kind and sensor_kind != str(SensorKind.UNKNOWN):
            session.kinds.add(sensor_kind)

    # --- cikti -------------------------------------------------------------

    def _body(self, session: RaidSession, base_text: str) -> str:
        """Govdeye ETA gibi ek bilgileri ekler."""
        if self._detail_for is None:
            return base_text
        try:
            extra = self._detail_for(session)
        except Exception:  # noqa: BLE001 - ETA hatasi alarmi susturmasin
            log.exception("Detay cozumleyicisi hata verdi (%s)", session.zone)
            return base_text
        if not extra:
            return base_text
        return f"{base_text}\n{extra}" if base_text else extra

    def _context(self, session: RaidSession) -> dict[str, object]:
        if self._context_for is None:
            return {}
        try:
            return self._context_for(session)
        except Exception:  # noqa: BLE001 - baglam hatasi alarmi susturmasin
            log.exception("Baglam cozumleyicisi hata verdi (%s)", session.zone)
            return {}

    def _severity(self, session: RaidSession, default: Severity) -> Severity:
        if self._severity_for is None:
            return default
        try:
            return self._severity_for(session)
        except Exception:  # noqa: BLE001 - puanlama hatasi olayi susturmasin
            log.exception("Siddet cozumleyicisi hata verdi (%s)", session.zone)
            return default

    async def _emit_started(self, session: RaidSession, entity_id: int | None) -> None:
        session.last_progress_at = session.started_at
        await self._emit(
            Event(
                kind=EventKind.RAID_STARTED,
                severity=self._severity(session, Severity.CRITICAL),
                title=t("raid.started", zone=session.zone),
                body=self._body(
                    session, ", ".join(sorted(session.entities)) or t("raid.alarm")
                ),
                zone=session.zone,
                entity_id=entity_id,
                raw={"trigger_count": session.trigger_count, **self._context(session)},
            )
        )

    async def _emit_progress(self, session: RaidSession) -> None:
        await self._emit(
            Event(
                kind=EventKind.RAID_PROGRESS,
                severity=self._severity(session, Severity.WARN),
                title=t("raid.progress", zone=session.zone),
                body=self._body(session, session.describe()),
                zone=session.zone,
                raw={
                    "trigger_count": session.trigger_count,
                    "levels": dict(session.levels),
                    "estimated_sulfur": session.estimated_sulfur,
                    "rate_per_minute": round(session.rate_per_minute(), 2),
                    **self._context(session),
                },
            )
        )

    async def _emit_ended(self, session: RaidSession) -> None:
        await self._emit(
            Event(
                kind=EventKind.RAID_ENDED,
                severity=Severity.INFO,
                title=t("raid.ended", zone=session.zone),
                body=session.describe(),
                zone=session.zone,
                raw={
                    "trigger_count": session.trigger_count,
                    "levels": dict(session.levels),
                    "estimated_sulfur": session.estimated_sulfur,
                    "duration_seconds": round(session.duration, 1),
                },
            )
        )

    # --- sessizlik takibi --------------------------------------------------

    async def _sweep_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._sweep_interval)
                return
            except TimeoutError:
                pass
            await self._sweep()

    async def _sweep(self) -> None:
        now = time.time()
        for key, session in list(self._sessions.items()):
            if now - session.last_trigger_at < self._quiet_timeout:
                continue
            self._sessions.pop(key, None)
            try:
                await self._emit_ended(session)
            except Exception:  # noqa: BLE001 - bir oturum digerlerini bozmasin
                log.exception("Raid kapanis olayi gonderilemedi (%s)", key)

    # --- durum -------------------------------------------------------------

    @property
    def active_zones(self) -> list[str]:
        return sorted(self._sessions)

    @property
    def active_sessions(self) -> list[RaidSession]:
        """Panelin ETA hesaplayabilmesi icin oturum nesnelerinin kendisi."""
        return sorted(self._sessions.values(), key=lambda s: s.started_at)

    def snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "zone": s.zone,
                "started_at": s.started_at,
                "last_trigger_at": s.last_trigger_at,
                "trigger_count": s.trigger_count,
                "levels": dict(s.levels),
                "estimated_sulfur": s.estimated_sulfur,
                "summary": s.describe(),
            }
            for s in self._sessions.values()
        ]
