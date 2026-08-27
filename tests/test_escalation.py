"""Eskalasyon zinciri ve emniyet supaplari.

Buradaki testler para harcayan bir yolu koruyor. Her biri "yanlislikla
arama yapmama" yonunde bir garanti.
"""

from __future__ import annotations

import datetime as dt

import pytest

from sentinel.escalation import (
    CallOutcome,
    CallResult,
    Contact,
    EscalationEngine,
    QuietHours,
)
from sentinel.models import EventKind
from sentinel.spend import MonthlySpend
from sentinel.store import Store


class FakeCaller:
    """Sirayla verilen sonuclari dondurur ve kimlerin arandigini kaydeder."""

    def __init__(self, outcomes: list[CallOutcome], price: float = 0.29) -> None:
        self._outcomes = list(outcomes)
        self._price = price
        self.called: list[str] = []
        self.closed = False

    async def place_call(self, to: str, message: str) -> CallResult:
        self.called.append(to)
        outcome = self._outcomes.pop(0) if self._outcomes else CallOutcome.NO_ANSWER
        return CallResult(
            outcome=outcome,
            sid=f"CA{len(self.called)}",
            duration_seconds=12 if outcome is CallOutcome.ANSWERED else 0,
            price_usd=self._price,
        )

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
async def store(tmp_path):
    s = Store(tmp_path / "esc.db")
    s.connect()
    yield s
    s.close()


def build(store, caller, *, cap=10.0, cooldown=900.0):
    events = []

    async def emit(event):
        events.append(event)

    contacts = [
        Contact("Halit", "+900000000001"),
        Contact("Ahmet", "+900000000002"),
        Contact("Mehmet", "+900000000003"),
    ]
    engine = EscalationEngine(
        caller,
        contacts,
        emit,
        MonthlySpend(store, cap),
        cooldown_seconds=cooldown,
    )
    return engine, events


# --- zincir davranisi ------------------------------------------------------


async def test_chain_stops_at_first_answer(store):
    caller = FakeCaller([CallOutcome.NO_ANSWER, CallOutcome.ANSWERED])
    engine, events = build(store, caller)

    await engine.escalate("Garaj", "Garaj saldiri altinda")

    assert len(caller.called) == 2, "Cevap alininca kalan kisi aranmamali"
    kinds = [e.kind for e in events]
    assert EventKind.ESCALATION_ACKNOWLEDGED in kinds
    assert EventKind.ESCALATION_EXHAUSTED not in kinds


async def test_chain_exhausts_when_nobody_answers(store):
    caller = FakeCaller([CallOutcome.NO_ANSWER] * 3)
    engine, events = build(store, caller)

    await engine.escalate("Garaj", "mesaj")

    assert len(caller.called) == 3
    assert events[-1].kind is EventKind.ESCALATION_EXHAUSTED


async def test_call_failure_does_not_stop_the_chain(store):
    """Bir numara patlarsa digerleri yine aranmali."""

    class ExplodingCaller(FakeCaller):
        async def place_call(self, to, message):
            self.called.append(to)
            if len(self.called) == 1:
                raise RuntimeError("saglayici hatasi")
            return CallResult(outcome=CallOutcome.ANSWERED, price_usd=0.29)

    caller = ExplodingCaller([])
    engine, events = build(store, caller)

    await engine.escalate("Garaj", "mesaj")

    assert len(caller.called) == 2
    assert any(e.kind is EventKind.ESCALATION_ACKNOWLEDGED for e in events)


# --- emniyet supaplari -----------------------------------------------------


async def test_cooldown_blocks_repeat_escalation_for_same_zone(store):
    caller = FakeCaller([CallOutcome.NO_ANSWER] * 6)
    engine, _ = build(store, caller, cooldown=900)

    await engine.escalate("Garaj", "mesaj")
    first_round = len(caller.called)
    await engine.escalate("Garaj", "mesaj")

    assert len(caller.called) == first_round, "Ayni bolge icin tekrar aranmamali"


async def test_cooldown_is_per_zone(store):
    caller = FakeCaller([CallOutcome.ANSWERED, CallOutcome.ANSWERED])
    engine, _ = build(store, caller)

    await engine.escalate("Garaj", "mesaj")
    await engine.escalate("Airlock", "mesaj")

    assert len(caller.called) == 2


async def test_budget_cap_prevents_calls_entirely(store):
    caller = FakeCaller([CallOutcome.ANSWERED])
    engine, events = build(store, caller, cap=0.10)

    # Tavani onceden doldur
    await MonthlySpend(store, 0.10).record(0.10)
    await engine.escalate("Garaj", "mesaj")

    assert caller.called == [], "Butce dolunca hic arama yapilmamali"
    assert events[-1].kind is EventKind.CALL_BUDGET_EXCEEDED


async def test_budget_stops_chain_midway(store):
    """Zincir ortasinda tavan dolarsa kalan kisiler aranmaz."""
    caller = FakeCaller([CallOutcome.NO_ANSWER] * 3, price=0.50)
    engine, _ = build(store, caller, cap=1.20)

    await engine.escalate("Garaj", "mesaj")

    assert len(caller.called) == 2, "Ucuncu arama butceyi asardi"


async def test_spend_is_recorded_and_survives_new_instance(store):
    caller = FakeCaller([CallOutcome.ANSWERED], price=0.29)
    engine, _ = build(store, caller, cap=10.0)

    await engine.escalate("Garaj", "mesaj")

    fresh = MonthlySpend(store, 10.0)
    assert await fresh.total() == pytest.approx(0.29)


async def test_disabled_without_caller_or_contacts(store):
    engine = EscalationEngine(None, [], lambda e: None, MonthlySpend(store, 10.0))
    assert not engine.enabled


# --- yardimcilar -----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "hour", "expected"),
    [
        ("23:00-08:00", 2, True),
        ("23:00-08:00", 23, True),
        ("23:00-08:00", 12, False),
        ("23:00-08:00", 8, False),
        ("09:00-17:00", 12, True),
        ("09:00-17:00", 20, False),
    ],
)
def test_quiet_hours_window(raw, hour, expected):
    window = QuietHours.parse(raw)
    assert window is not None
    moment = dt.datetime(2026, 8, 27, hour, 0)
    assert window.contains(moment) is expected


def test_quiet_hours_invalid_input_returns_none():
    assert QuietHours.parse("") is None
    assert QuietHours.parse("saçma") is None


def test_contact_parsing():
    contacts = Contact.parse_list("Halit:+905001, Ahmet:+905002 ,  , bozuk")

    assert [c.name for c in contacts] == ["Halit", "Ahmet"]
    assert contacts[0].phone == "+905001"
