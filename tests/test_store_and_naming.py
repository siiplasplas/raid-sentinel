"""Depo ve cihaz adlandirma."""

from __future__ import annotations

import pytest

from sentinel.models import Entity, EntityType, Event, EventKind, Severity
from sentinel.naming import parse_entity_name
from sentinel.store import Store


@pytest.fixture
async def store(tmp_path):
    s = Store(tmp_path / "test.db")
    s.connect()
    yield s
    s.close()


# --- adlandirma ------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Garaj S3", ("Garaj", 3)),
        ("Airlock_S2", ("Airlock", 2)),
        ("Ana Us-S1", ("Ana Us", 1)),
        ("garaj s3", ("garaj", 3)),
        ("Cati HBHF", ("Cati HBHF", None)),
        ("S3", ("S3", None)),
        ("Garaj S4", ("Garaj S4", None)),
        ("  Garaj S3  ", ("Garaj", 3)),
        ("", ("", None)),
    ],
)
def test_entity_name_parsing(name, expected):
    assert parse_entity_name(name) == expected


# --- depo ------------------------------------------------------------------


async def test_events_round_trip_with_raw_payload(store):
    await store.add_event(
        Event(
            kind=EventKind.RAID_STARTED,
            severity=Severity.CRITICAL,
            title="Garaj saldırı altında",
            zone="Garaj",
            raw={"trigger_count": 3, "levels": {"3": 3}},
        )
    )
    rows = await store.recent_events()

    assert len(rows) == 1
    assert rows[0].raw["trigger_count"] == 3
    assert rows[0].zone == "Garaj"


async def test_severity_filter_excludes_noise(store):
    await store.add_event(
        Event(kind=EventKind.ENTITY_CHANGED, severity=Severity.DEBUG, title="gurultu")
    )
    await store.add_event(
        Event(kind=EventKind.RAID_STARTED, severity=Severity.CRITICAL, title="raid")
    )

    loud = await store.recent_events(min_severity=Severity.WARN)
    assert [e.title for e in loud] == ["raid"]


async def test_entity_upsert_preserves_manually_set_zone(store):
    """Yeniden eslestirmede elle atanmis bolge silinmemeli."""
    server = "1.2.3.4:28082"
    def entity(zone: str | None) -> Entity:
        return Entity(
            entity_id=1,
            entity_type=EntityType.ALARM,
            name="Alarm",
            server_id=server,
            zone=zone,
        )

    await store.upsert_entity(entity("Garaj"))
    await store.upsert_entity(entity(None))

    entities = await store.entities(server)
    assert entities[0].zone == "Garaj"


async def test_entity_value_update(store):
    server = "1.2.3.4:28082"
    await store.upsert_entity(
        Entity(entity_id=5, entity_type=EntityType.ALARM, name="Garaj S3", server_id=server)
    )
    await store.update_entity_value(5, server, True)

    entities = await store.entities(server)
    assert entities[0].last_value is True
    assert entities[0].last_seen is not None


async def test_state_survives_json_and_plain_values(store):
    await store.set_state("wipe", {"ts": 123})
    await store.set_state("note", "duz metin")

    assert await store.get_state("wipe") == {"ts": 123}
    assert await store.get_state("note") == "duz metin"
    assert await store.get_state("yok", "varsayilan") == "varsayilan"


def test_entity_type_from_proto():
    assert EntityType.from_proto(1) is EntityType.SWITCH
    assert EntityType.from_proto(2) is EntityType.ALARM
    assert EntityType.from_proto(3) is EntityType.STORAGE_MONITOR
    with pytest.raises(ValueError):
        EntityType.from_proto(99)


def test_severity_parsing():
    assert Severity.parse("warn") is Severity.WARN
    assert Severity.parse("CRITICAL") is Severity.CRITICAL
    assert Severity.parse(30) is Severity.WARN
    with pytest.raises(ValueError):
        Severity.parse("cok-yuksek")
