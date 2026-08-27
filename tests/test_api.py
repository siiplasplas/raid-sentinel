"""HTTP arayuzu.

En kritik uc /health: dis bir izleme servisi buna bakip sistemin sessizce
oldugunu anlayacak. Saglikliyken 200, degilken 503 donmesi bir detay degil,
sessiz arizaya karsi tek dis savunma.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from sentinel.api import create_app
from sentinel.app import Sentinel
from sentinel.base_model import BaseGraph
from sentinel.config import Settings
from sentinel.models import Entity, EntityType, Event, EventKind, Severity

BASE = {
    "name": "Test Us",
    "target": "TC",
    "edges": [
        {"from": "Garaj", "to": "TC", "obstacles": [{"tier": "metal", "count": 1}]},
    ],
}


@pytest.fixture
async def sentinel(tmp_path):
    """Gercek Sentinel, ama start() cagrilmadan - FCM eslesmesi gerekmesin."""
    app = Sentinel(Settings(db_path=tmp_path / "api.db"))
    app.store.connect()
    app.bus.subscribe(app._store_event)
    app.base = BaseGraph.from_dict(BASE)
    app._build_escalation()
    app.started_at = time.time() - 60
    app._running.set()
    yield app
    app.store.close()


@pytest.fixture
def client(sentinel):
    return TestClient(create_app(sentinel))


# --- panel -----------------------------------------------------------------


def test_index_serves_panel(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Raid Sentinel" in response.text
    assert "text/html" in response.headers["content-type"]


def test_panel_is_self_contained(client):
    """Panel dis kaynak cekmemeli - VPS internetsiz de calisabilmeli."""
    body = client.get("/").text

    assert "http://" not in body.replace("http://127.0.0.1", "")
    assert "cdn" not in body.lower()


# --- saglik ----------------------------------------------------------------


def test_health_reports_503_when_fcm_is_dead(client):
    """FCM yoksa sistem saglikli degildir - dis izleme bunu gormeli."""
    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["ok"] is False


def test_health_includes_spend_and_escalation(client):
    body = client.get("/health").json()

    assert "spend" in body
    assert body["spend"]["cap_usd"] == 5.0
    assert "escalation" in body
    assert body["escalation"]["phone_min_threat"] == "HIGH"


# --- veri uclari -----------------------------------------------------------


async def test_events_endpoint_filters_by_severity(sentinel, client):
    await sentinel._publish(
        Event(kind=EventKind.ENTITY_CHANGED, severity=Severity.DEBUG, title="gurultu")
    )
    await sentinel._publish(
        Event(kind=EventKind.RAID_STARTED, severity=Severity.CRITICAL, title="raid")
    )

    loud = client.get("/api/events", params={"min_severity": "warn"}).json()
    assert [e["title"] for e in loud["events"]] == ["raid"]

    everything = client.get("/api/events").json()
    assert everything["count"] == 2


def test_events_endpoint_rejects_bad_severity(client):
    body = client.get("/api/events", params={"min_severity": "cok-yuksek"}).json()

    assert "error" in body
    assert body["events"] == []


async def test_entities_endpoint(sentinel, client):
    await sentinel.store.upsert_entity(
        Entity(entity_id=7, entity_type=EntityType.ALARM, name="Garaj S3",
               server_id="x", zone="Garaj")
    )

    body = client.get("/api/entities").json()
    assert body["count"] == 1
    assert body["entities"][0]["name"] == "Garaj S3"


def test_base_endpoint_exposes_costs(client):
    body = client.get("/api/base").json()

    assert body["loaded"] is True
    assert body["target"] == "TC"
    assert body["edges"][0]["cost_c4"] == 4
    assert body["edges"][0]["cost_rocket"] == 8


async def test_raids_endpoint_carries_threat_and_eta(sentinel, client):
    now = time.time()
    for index in range(5):
        await sentinel.raid.feed(
            zone="Garaj", entity_name="Garaj S3", seismic_level=3,
            ts=now - (4 - index) * 20,
        )

    raids = client.get("/api/raids").json()["raids"]

    assert len(raids) == 1
    assert raids[0]["zone"] == "Garaj"
    assert raids[0]["threat"] == "HIGH"
    assert raids[0]["eta"] is not None
    assert raids[0]["eta"]["remaining_explosives"] >= 0
    # Panel geri sayimi kendisi isletiyor, mutlak an gerekli
    assert raids[0]["eta"]["due_at"] > now


def test_raids_endpoint_empty_when_calm(client):
    assert client.get("/api/raids").json()["raids"] == []


# --- panelden yapilandirma -------------------------------------------------


def test_settings_endpoint_hides_secrets(client):
    fields = client.get("/api/settings").json()["fields"]
    secret = next(f for f in fields if f["key"] == "twilio_auth_token")

    assert "value" not in secret
    assert secret["type"] == "secret"


def test_settings_update_applies_live(sentinel, client):
    """Kaydettikten sonra surec yeniden baslamadan devreye girmeli."""
    assert [n.name for n in sentinel.router.notifiers] == []

    response = client.post("/api/settings", json={"ntfy_topic": "gizli-konu-7"})

    assert response.status_code == 200
    assert response.json()["changed"] == ["ntfy_topic"]
    assert "ntfy" in [n.name for n in sentinel.router.notifiers]


def test_settings_update_rejects_unknown_key(client):
    response = client.post("/api/settings", json={"db_path": "/etc/passwd"})

    assert response.status_code == 400
    assert "Bilinmeyen ayar" in response.json()["error"]


async def test_entity_config_can_be_assigned(sentinel, client):
    await sentinel.store.upsert_entity(
        Entity(entity_id=42, entity_type=EntityType.ALARM, name="Isimsiz",
               server_id="x")
    )

    response = client.patch(
        "/api/entities/42", json={"zone": "Garaj", "seismic_level": 3}
    )
    assert response.status_code == 200

    entity = client.get("/api/entities").json()["entities"][0]
    assert entity["zone"] == "Garaj"
    assert entity["seismic_level"] == 3


def test_entity_config_rejects_bad_level(client):
    response = client.patch("/api/entities/42", json={"seismic_level": 9})

    assert response.status_code == 400
    assert "1, 2 veya 3" in response.json()["error"]


def test_entity_config_404_for_unknown_device(client):
    assert client.patch("/api/entities/999999", json={"zone": "X"}).status_code == 404


def test_base_can_be_saved_from_panel(sentinel, client):
    new_base = {
        "name": "Panelden",
        "target": "TC",
        "edges": [{"from": "Garaj", "to": "TC",
                   "obstacles": [{"tier": "armored", "count": 1}]}],
    }

    assert client.put("/api/base", json=new_base).status_code == 200
    assert sentinel.base.name == "Panelden"
    assert client.get("/api/base").json()["edges"][0]["cost_c4"] == 8


def test_base_save_rejects_invalid_definition(sentinel, client):
    before = sentinel.base.name
    bad = {"target": "TC", "edges": [{"from": "A", "to": "B",
                                      "obstacles": [{"tier": "betonarme"}]}]}

    response = client.put("/api/base", json=bad)

    assert response.status_code == 400
    assert "Bilinmeyen duvar kademesi" in response.json()["error"]
    assert sentinel.base.name == before, "Gecersiz tanim mevcut ussu bozmamali"


def test_test_notify_action_reports_no_channels(client):
    body = client.post("/api/actions/test-notify").json()

    assert body["ok"] is False
    assert "kanal" in body["error"].lower()


# --- kurulum durumu ve cihaz eylemleri -------------------------------------


async def test_setup_flags_zone_name_mismatch(sentinel, client):
    """Sessiz yanlis yapilandirmanin gorunur oldugu tek yer burasi.

    Cihaz adindaki bolge us tanimindaki dugumle tutmuyorsa ETA sessizce
    kayboluyor - kullanici bunu baska turlu anlayamaz.
    """
    await sentinel.store.upsert_entity(
        Entity(entity_id=1, entity_type=EntityType.ALARM, name="Cati S3",
               server_id="x", zone="Cati")
    )

    body = client.get("/api/setup").json()

    assert body["unmatched_zones"] == ["Cati"]
    assert "Garaj" in body["known_zones"]


async def test_setup_reports_devices_without_zone(sentinel, client):
    await sentinel.store.upsert_entity(
        Entity(entity_id=2, entity_type=EntityType.ALARM, name="Isimsiz",
               server_id="x", zone=None)
    )

    body = client.get("/api/setup").json()
    assert body["devices_without_zone"] == ["Isimsiz"]


async def test_simulate_pushes_through_the_real_pipeline(sentinel, client):
    """Test tetiklemesi ayri bir yol degil - toplayici ve puanlama calismali."""
    await sentinel.store.upsert_entity(
        Entity(entity_id=3, entity_type=EntityType.ALARM, name="Garaj S3",
               server_id="x", zone="Garaj")
    )

    response = client.post("/api/entities/3/simulate")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["zone"] == "Garaj"
    assert body["seismic_level"] == 3

    raids = client.get("/api/raids").json()["raids"]
    assert [r["zone"] for r in raids] == ["Garaj"]
    assert raids[0]["threat"] == "HIGH", "Tek C4 patlamasi YUKSEK olmali"


def test_simulate_404_for_unknown_device(client):
    assert client.post("/api/entities/4242/simulate").status_code == 404


async def test_device_can_be_deleted(sentinel, client):
    await sentinel.store.upsert_entity(
        Entity(entity_id=5, entity_type=EntityType.ALARM, name="Eski",
               server_id="x")
    )

    assert client.delete("/api/entities/5").status_code == 200
    assert client.get("/api/entities").json()["count"] == 0


def test_delete_404_for_unknown_device(client):
    assert client.delete("/api/entities/4242").status_code == 404


def test_base_options_come_from_the_cost_tables(client):
    body = client.get("/api/base/options").json()

    tiers = {t["value"]: t["cost_c4"] for t in body["tiers"]}
    assert tiers["stone"] == 2
    assert tiers["armored"] == 8

    deployables = {d["value"]: d["cost_c4"] for d in body["deployables"]}
    assert deployables["garage_door"] == 2
    assert all(d["label"] for d in body["deployables"]), "Her secenegin etiketi olmali"
