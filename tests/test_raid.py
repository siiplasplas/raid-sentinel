"""Raid toplayicisinin davranisi.

En kritik ozellik: bir raid sirasinda gelen onlarca tetikleme tek bir
"saldırı başladı" olayina inmeli. Aksi halde hem Discord okunmaz olur hem
de her tetikleme icin telefon calarsa fatura patlar.
"""

from __future__ import annotations

import asyncio

import pytest

from sentinel.models import EventKind, Severity
from sentinel.raid import RaidAggregator
from sentinel.raiddata import SeismicLevel


def make_aggregator(collected, **kwargs):
    async def emit(event):
        collected.append(event)

    defaults = {
        "progress_interval": 0.05,
        "quiet_timeout": 0.15,
        "sweep_interval": 0.02,
    }
    defaults.update(kwargs)
    return RaidAggregator(emit, **defaults)


async def test_first_trigger_emits_single_critical_event():
    events = []
    agg = make_aggregator(events)

    await agg.feed(zone="Garaj", entity_name="Garaj S3", seismic_level=3)

    assert len(events) == 1
    assert events[0].kind is EventKind.RAID_STARTED
    assert events[0].severity is Severity.CRITICAL
    assert events[0].zone == "Garaj"


async def test_burst_of_triggers_does_not_spam():
    """Onlarca tetikleme tek bir baslangic olayina inmeli."""
    events = []
    agg = make_aggregator(events, progress_interval=999)

    for _ in range(50):
        await agg.feed(zone="Garaj", entity_name="Garaj S3", seismic_level=3)

    assert len(events) == 1, "Patlama serisi tek olaya indirgenmeliydi"


async def test_progress_event_after_interval():
    events = []
    agg = make_aggregator(events, progress_interval=0.05)

    await agg.feed(zone="Garaj", seismic_level=3)
    await asyncio.sleep(0.06)
    await agg.feed(zone="Garaj", seismic_level=3)

    kinds = [e.kind for e in events]
    assert kinds == [EventKind.RAID_STARTED, EventKind.RAID_PROGRESS]


async def test_separate_zones_are_separate_sessions():
    events = []
    agg = make_aggregator(events)

    await agg.feed(zone="Garaj", seismic_level=3)
    await agg.feed(zone="Airlock", seismic_level=2)

    started = [e for e in events if e.kind is EventKind.RAID_STARTED]
    assert {e.zone for e in started} == {"Garaj", "Airlock"}


async def test_session_ends_after_quiet_period():
    events = []
    agg = make_aggregator(events, quiet_timeout=0.1, sweep_interval=0.02)
    await agg.start()
    try:
        await agg.feed(zone="Garaj", seismic_level=3)
        await asyncio.sleep(0.25)
    finally:
        await agg.stop()

    kinds = [e.kind for e in events]
    assert EventKind.RAID_ENDED in kinds
    assert agg.active_zones == []


async def test_sulfur_estimate_uses_seismic_tier():
    """3. kademe C4 (2200) veya roket (1400) - ortalamasi 1800."""
    events = []
    agg = make_aggregator(events, progress_interval=0)

    for _ in range(4):
        await agg.feed(zone="Garaj", seismic_level=int(SeismicLevel.HEAVY))

    progress = [e for e in events if e.kind is EventKind.RAID_PROGRESS][-1]
    assert progress.raw["estimated_sulfur"] == 4 * 1800


async def test_unknown_seismic_level_contributes_zero_sulfur():
    """Kademe bilinmiyorsa uydurmak yerine sifir sayilir."""
    events = []
    agg = make_aggregator(events, progress_interval=0)

    await agg.feed(zone="Cati", seismic_level=None)
    await agg.feed(zone="Cati", seismic_level=None)

    progress = [e for e in events if e.kind is EventKind.RAID_PROGRESS][-1]
    assert progress.raw["estimated_sulfur"] == 0
    assert progress.raw["trigger_count"] == 2


async def test_missing_zone_falls_back_without_crashing():
    events = []
    agg = make_aggregator(events)

    await agg.feed(zone=None, entity_name="Isimsiz alarm")

    assert len(events) == 1
    assert events[0].zone


@pytest.mark.parametrize(
    ("level", "expected_fragment"),
    [(1, "el bombası"), (2, "satchel"), (3, "C4")],
)
async def test_describe_names_heaviest_explosive(level, expected_fragment):
    events = []
    agg = make_aggregator(events, progress_interval=0)

    await agg.feed(zone="Garaj", seismic_level=level)
    await agg.feed(zone="Garaj", seismic_level=level)

    progress = [e for e in events if e.kind is EventKind.RAID_PROGRESS][-1]
    assert expected_fragment in progress.body


async def test_severity_comes_from_resolver_not_hardcoded():
    """Sahte alarm kaydedilmeli ama kimseyi uyandirmamali.

    Toplayici kendi basina her seye CRITICAL derse puanlama bosa gider:
    tek bir HBHF tetiklemesi gece 4'te ntfy acil push'u gonderir.
    """
    from sentinel.scoring import severity_for

    events = []
    agg = make_aggregator(events, severity_for=severity_for)

    await agg.feed(zone="Cati", entity_name="Cati HBHF")
    assert events[0].severity is Severity.DEBUG

    events.clear()
    await agg.feed(zone="Garaj", entity_name="Garaj S3", seismic_level=3)
    assert events[0].severity is Severity.CRITICAL


async def test_presence_sensor_is_not_reported_as_an_explosion():
    """En kritik durustluk kurali.

    Kullanici tek bir HBHF alarmina elle 'kademe 3' atayabiliyor (yuksek
    tehdit uretsin diye). Bu, bildirimde "C4/roket kademesinde patlama"
    yazmasini hakli kilmaz - sensor patlama gormedi, insan gordu.
    """
    from sentinel.models import SensorKind
    from sentinel.scoring import assess, severity_for

    events = []
    agg = make_aggregator(
        events, severity_for=severity_for,
        context_for=lambda s: {"summary": s.describe(),
                               "threat_reasons": assess(s).reasons},
    )

    await agg.feed(zone="Garaj", entity_name="Garaj", seismic_level=3,
                   sensor_kind=str(SensorKind.PRESENCE))

    # Discord govdesi bu ozetten uretiliyor
    summary = events[0].raw["summary"]
    assert "hareket algılandı" in summary
    assert "C4" not in summary
    assert not any("C4" in r for r in events[0].raw["threat_reasons"]), (
        "Puanlama gerekcesi de patlama iddia etmemeli"
    )


async def test_unverified_tier_is_marked_as_assumed():
    """Sismik oldugu dogrulanmamis bir kademe, beyan olarak isaretlenmeli."""
    events = []
    agg = make_aggregator(events, progress_interval=0)

    await agg.feed(zone="Garaj", seismic_level=3)
    await agg.feed(zone="Garaj", seismic_level=3)

    progress = [e for e in events if e.kind is EventKind.RAID_PROGRESS][-1]
    assert "kademe elle atanmış" in progress.body


async def test_confirmed_seismic_tier_is_stated_plainly():
    from sentinel.models import SensorKind

    events = []
    agg = make_aggregator(events, progress_interval=0)

    for _ in range(2):
        await agg.feed(zone="Garaj", seismic_level=3,
                       sensor_kind=str(SensorKind.EXPLOSION))

    progress = [e for e in events if e.kind is EventKind.RAID_PROGRESS][-1]
    assert "C4/roket" in progress.body
    assert "elle atanmis" not in progress.body
