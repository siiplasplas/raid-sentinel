"""Eskalasyon zinciri: kimi, ne zaman, hangi sirayla arayacagiz.

Tek kisiyi aramak yetmez - uyuyor olabilirsin, telefon sessizdedir, mesguldur.
Zincir sirayla ilerler ve ilk cevap veren zinciri durdurur; kalan kisiler
bosuna aranmaz (hem gurultu hem para).

Uc emniyet supabi var ve ucu de bilerek "arama yapmama" yonunde hata yapar:
  - aylik butce tavani
  - ayni bolge icin bekleme suresi (ayni raid icin tekrar tekrar aramamak)
  - sessiz saatler (dusuk tehditte gece aramamak)
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sentinel.i18n import t
from sentinel.models import Event, EventKind, Severity
from sentinel.spend import MonthlySpend

log = logging.getLogger(__name__)

EventEmitter = Callable[[Event], Awaitable[None]]


class CallOutcome(StrEnum):
    ANSWERED = "answered"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    FAILED = "failed"
    MACHINE = "machine"


@dataclass(slots=True)
class CallResult:
    outcome: CallOutcome
    sid: str = ""
    duration_seconds: int = 0
    price_usd: float = 0.0
    error: str = ""

    @property
    def acknowledged(self) -> bool:
        return self.outcome is CallOutcome.ANSWERED


class Caller(Protocol):
    """Arama saglayicisi sozlesmesi. Twilio disinda bir saglayiciya
    gecmek bu arayuzu uygulamak demek."""

    async def place_call(self, to: str, message: str) -> CallResult: ...

    async def aclose(self) -> None: ...


@dataclass(slots=True)
class Contact:
    name: str
    phone: str

    @classmethod
    def parse_list(cls, raw: str) -> list[Contact]:
        """"Halit:+905...,Ahmet:+905..." bicimini ayristirir."""
        contacts: list[Contact] = []
        for chunk in raw.split(","):
            item = chunk.strip()
            if not item:
                continue
            name, _, phone = item.partition(":")
            phone = phone.strip()
            if not phone:
                log.warning("Kisi atlandi, telefon yok: %r", item)
                continue
            contacts.append(cls(name=name.strip() or phone, phone=phone))
        return contacts


@dataclass(slots=True)
class QuietHours:
    """Gece penceresi. Gece yarisini asan araliklar desteklenir."""

    start_hour: int
    end_hour: int

    @classmethod
    def parse(cls, raw: str) -> QuietHours | None:
        text = (raw or "").strip()
        if not text:
            return None
        try:
            start, _, end = text.partition("-")
            return cls(
                start_hour=int(start.strip().split(":")[0]),
                end_hour=int(end.strip().split(":")[0]),
            )
        except (ValueError, IndexError):
            log.error("Sessiz saat araligi okunamadi: %r (ornek: 23:00-08:00)", raw)
            return None

    def contains(self, moment: dt.datetime | None = None) -> bool:
        hour = (moment or dt.datetime.now()).hour
        if self.start_hour == self.end_hour:
            return False
        if self.start_hour < self.end_hour:
            return self.start_hour <= hour < self.end_hour
        # Gece yarisini asiyor (orn. 23-08)
        return hour >= self.start_hour or hour < self.end_hour


class EscalationEngine:
    def __init__(
        self,
        caller: Caller | None,
        contacts: list[Contact],
        emit: EventEmitter,
        spend: MonthlySpend,
        *,
        cooldown_seconds: float = 900.0,
        estimated_call_cost_usd: float = 0.30,
    ) -> None:
        self._caller = caller
        self._contacts = contacts
        self._emit = emit
        self._spend = spend
        self._cooldown = cooldown_seconds
        self._estimate = estimated_call_cost_usd

        self._last_escalation: dict[str, float] = {}
        self._active: set[str] = set()
        # Bolge -> susturmanin bitecegi an. "Ben ilgileniyorum, aramayi kes."
        self._suppressed: dict[str, float] = {}

    def acknowledge(self, zone: str | None, minutes: float) -> float:
        """Telefon zincirini elle susturur ve bitis anini doner.

        Raid sirasinda "geliyorum, beni arama" diyebilmek gerekiyor;
        bolge bekleme suresi otomatik ama elle onay onun yerine gecmez.
        `zone` None ise butun bolgeler susturulur.
        """
        until = time.time() + max(0.0, minutes) * 60
        if zone is None:
            for key in list(self._last_escalation) + list(self._active):
                self._suppressed[key] = until
            self._suppressed["*"] = until
        else:
            self._suppressed[zone] = until
        log.info("Telefon zinciri susturuldu (%s) %.0f dk", zone or t("esc.all_zones"), minutes)
        return until

    def suppressed_until(self, zone: str) -> float:
        """Bu bolge icin susturma ne zaman bitiyor (gecmisse 0)."""
        now = time.time()
        best = max(self._suppressed.get(zone, 0.0), self._suppressed.get("*", 0.0))
        return best if best > now else 0.0

    def clear_acknowledgement(self, zone: str | None = None) -> None:
        if zone is None:
            self._suppressed.clear()
        else:
            self._suppressed.pop(zone, None)

    @property
    def enabled(self) -> bool:
        return self._caller is not None and bool(self._contacts)

    async def escalate(self, zone: str, message: str, *, reason: str = "") -> None:
        """Zinciri baslatir. Cagiran taraf tehdit seviyesini zaten dogrulamis olmali."""
        if not self.enabled:
            log.debug("Eskalasyon kapalı (sağlayıcı veya kişi listesi yok)")
            return

        if zone in self._active:
            log.info("Bu bölge için zincir zaten çalışıyor: %s", zone)
            return

        if not await self._precheck(zone):
            return

        self._active.add(zone)
        self._last_escalation[zone] = time.time()
        try:
            await self._run_chain(zone, message, reason)
        finally:
            self._active.discard(zone)

    async def _precheck(self, zone: str) -> bool:
        until = self.suppressed_until(zone)
        if until:
            log.info(
                "Bölge susturulmuş (%s), %.0f dk kaldı", zone, (until - time.time()) / 60
            )
            return False

        last = self._last_escalation.get(zone, 0.0)
        elapsed = time.time() - last
        if last and elapsed < self._cooldown:
            log.info(
                "Bölge bekleme süresinde (%s): %.0f/%.0f sn",
                zone,
                elapsed,
                self._cooldown,
            )
            return False

        if not await self._spend.can_spend(self._estimate):
            summary = await self._spend.summary()
            await self._emit(
                Event(
                    kind=EventKind.CALL_BUDGET_EXCEEDED,
                    severity=Severity.CRITICAL,
                    title="Arama butcesi doldu - telefon aranmiyor",
                    body=(
                        f"Bu ay {summary['spent_usd']} USD harcandi, "
                        f"tavan {summary['cap_usd']} USD. "
                        "Discord ve ntfy calismaya devam ediyor."
                    ),
                    zone=zone,
                )
            )
            return False
        return True

    async def _run_chain(self, zone: str, message: str, reason: str) -> None:
        await self._emit(
            Event(
                kind=EventKind.ESCALATION_STARTED,
                severity=Severity.CRITICAL,
                title=t("esc.started", zone=zone),
                body=reason or message,
                zone=zone,
                raw={"contacts": [c.name for c in self._contacts]},
            )
        )

        assert self._caller is not None  # enabled kontrolu yapildi

        for index, contact in enumerate(self._contacts, start=1):
            if not await self._spend.can_spend(self._estimate):
                log.warning("Bütçe zincir ortasında doldu, kalan kişiler aranmıyor")
                break

            log.info("Araniyor (%d/%d): %s", index, len(self._contacts), contact.name)
            try:
                result = await self._caller.place_call(contact.phone, message)
            except Exception as exc:  # noqa: BLE001 - bir kisi digerlerini engellemesin
                log.exception("Arama hatasi: %s", contact.name)
                result = CallResult(outcome=CallOutcome.FAILED, error=str(exc))

            if result.price_usd:
                await self._spend.record(result.price_usd)

            if result.acknowledged:
                await self._emit(
                    Event(
                        kind=EventKind.ESCALATION_ACKNOWLEDGED,
                        severity=Severity.WARN,
                        title=t("esc.answered", zone=zone, name=contact.name),
                        body=t("esc.answered_body", n=result.duration_seconds),
                        zone=zone,
                        raw={"contact": contact.name, "sid": result.sid},
                    )
                )
                return

            log.info("Cevap yok (%s): %s", contact.name, result.outcome)

        await self._emit(
            Event(
                kind=EventKind.ESCALATION_EXHAUSTED,
                severity=Severity.CRITICAL,
                title=t("esc.nobody", zone=zone),
                body=t("esc.nobody_body", n=len(self._contacts)),
                zone=zone,
            )
        )

    async def aclose(self) -> None:
        if self._caller is not None:
            await self._caller.aclose()
