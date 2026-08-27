"""Tehdit puanlamasi.

Buradaki her test bir maliyet kararidir: YUKSEK cikan her degerlendirme
telefon calmasi, yani para demek. Esiklerin gevsemesi hem fatura hem de
guven kaybi.
"""

from __future__ import annotations

import time

from sentinel.raid import RaidSession
from sentinel.scoring import ThreatLevel, assess


def session(
    *,
    triggers: int = 1,
    levels: dict[int, int] | None = None,
    entities: set[str] | None = None,
    age_seconds: float = 0.0,
) -> RaidSession:
    now = time.time()
    s = RaidSession(zone="Garaj", started_at=now - age_seconds, last_trigger_at=now)
    s.trigger_count = triggers
    s.entities = entities or {"Garaj S3"}
    for level, count in (levels or {}).items():
        s.levels[level] = count
    return s


def test_single_c4_is_immediately_high():
    """Ussune C4 yapistiran biri tanim geregi raid - ikinci kanit beklenmez."""
    result = assess(session(levels={3: 1}))

    assert result.level is ThreatLevel.HIGH
    assert "C4/roket" in result.explanation


def test_single_hbhf_trigger_is_not_an_alarm():
    """Yanindan gecen biri gece 4'te telefon caldirmamali."""
    result = assess(session(triggers=1, levels=None))

    assert result.level is ThreatLevel.NONE


def test_satchel_tier_reaches_medium_alone():
    result = assess(session(levels={2: 1}))
    assert result.level is ThreatLevel.MEDIUM


def test_two_distinct_sensors_escalate_without_explosives():
    """Patlayici yok ama iki ayri sensor - birinin ici geziyor."""
    result = assess(session(triggers=2, entities={"Garaj", "Airlock"}))

    assert result.level is ThreatLevel.MEDIUM
    assert "2 ayri sensor" in result.explanation


def test_sustained_activity_escalates_over_time():
    quick = assess(session(triggers=3, age_seconds=5))
    sustained = assess(session(triggers=3, age_seconds=120))

    assert sustained.score > quick.score
    assert "dakikadir suruyor" in sustained.explanation


def test_teammate_nearby_never_suppresses_explosive_evidence():
    """Kimse kendi ussunu C4'lemez - arkadas yakin olsa da bu raid."""
    without = assess(session(levels={3: 1}))
    with_mate = assess(session(levels={3: 1}), teammate_nearby=True)

    assert with_mate.score == without.score
    assert with_mate.level is ThreatLevel.HIGH


def test_teammate_nearby_cancels_weak_signal():
    result = assess(
        session(triggers=2, entities={"Garaj", "Airlock"}), teammate_nearby=True
    )
    assert result.level < ThreatLevel.MEDIUM


def test_heaviest_tier_wins_when_mixed():
    """Once satchel sonra C4 - degerlendirme en agirina gore."""
    result = assess(session(triggers=5, levels={2: 4, 3: 1}))

    assert result.level is ThreatLevel.HIGH
    assert "C4/roket" in result.explanation


def test_score_never_goes_negative():
    result = assess(session(triggers=1), teammate_nearby=True)
    assert result.score >= 0


def test_reasons_are_always_human_readable():
    """Bildirimde 'neden uyandirildim' cevabi her zaman olmali."""
    result = assess(session(triggers=4, levels={3: 4}, age_seconds=200))

    assert result.reasons
    assert all(isinstance(r, str) and r for r in result.reasons)
