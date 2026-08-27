"""FCM bildirim ayristirma.

Gercek yuk Expo uzerinden ic ice JSON string olarak geliyor ve tam sekli
ancak canli bir hesapla dogrulanabilir. Bu testler, karsilasabilecegimiz
sekillerin hepsinde dogru siniflandirma yapildigini garanti ediyor.
"""

from __future__ import annotations

import json

from sentinel.rust.notifications import NotificationKind, parse


def test_server_pairing_from_nested_body():
    payload = {
        "ip": "1.2.3.4",
        "port": "28082",
        "playerId": "76561198000000000",
        "playerToken": "-1234567",
        "type": "server",
        "name": "Benim Sunucum",
    }
    result = parse({"body": json.dumps(payload)})

    assert result.kind is NotificationKind.SERVER_PAIRING
    assert result.server_name == "Benim Sunucum"
    assert result.data["ip"] == "1.2.3.4"


def test_entity_pairing_carries_entity_id_and_type():
    payload = {
        "entityId": "25743493",
        "entityType": "2",
        "entityName": "Garaj S3",
        "type": "entity",
    }
    result = parse({"body": json.dumps(payload)})

    assert result.kind is NotificationKind.ENTITY_PAIRING
    assert result.entity_id == 25743493
    assert result.entity_type == 2
    assert result.entity_name == "Garaj S3"


def test_alarm_is_distinguished_from_pairing():
    payload = {"entityId": "42", "entityName": "Airlock S2", "type": "alarm"}
    result = parse({"title": "Alarm!", "body": json.dumps(payload)})

    assert result.kind is NotificationKind.ALARM
    assert result.entity_id == 42


def test_double_nested_payload_is_unwrapped():
    """Expo bazen yuku bir kat daha sarabiliyor."""
    inner = {"entityId": "7", "entityName": "Cati", "type": "alarm"}
    result = parse({"body": json.dumps({"body": json.dumps(inner)})})

    assert result.kind is NotificationKind.ALARM
    assert result.entity_id == 7


def test_already_parsed_dict_is_accepted():
    result = parse({"data": {"entityId": 9, "type": "entity", "entityName": "Kapi"}})

    assert result.kind is NotificationKind.ENTITY_PAIRING
    assert result.entity_id == 9


def test_unrecognised_payload_is_kept_not_dropped():
    """Bilinmeyen bildirim sessizce kaybolmamali - ham hali saklanmali."""
    raw = {"foo": "bar", "title": "?"}
    result = parse(raw)

    assert result.kind is NotificationKind.UNKNOWN
    assert result.raw == raw


def test_malformed_json_does_not_raise():
    result = parse({"body": "{bu json degil"})
    assert result.kind is NotificationKind.UNKNOWN


def test_non_numeric_entity_id_returns_none():
    result = parse({"body": json.dumps({"entityId": "abc", "type": "alarm"})})
    assert result.entity_id is None


# --- Discord gomulu mesaji -------------------------------------------------


def _raid_event():
    from sentinel.models import Event, EventKind, Severity

    return Event(
        kind=EventKind.RAID_STARTED,
        severity=Severity.CRITICAL,
        title="maintc: saldiri basladi",
        zone="maintc",
        raw={
            "summary": "3 tetikleme",
            "threat": "HIGH",
            "threat_score": 70,
            "threat_reasons": ["C4/roket kademesinde patlama"],
            "trigger_count": 3,
            "estimated_sulfur": 5400,
            "eta_seconds": 64,
            "eta_low": 45,
            "eta_high": 92,
            "eta_confidence": "orta",
            "remaining_explosives": 2,
            "path": [{"to": "TC", "cost": 2, "label": "1x stone"}],
        },
    )


def test_discord_embed_carries_the_decision_data():
    """Telefonda tek bakista karar verilebilmeli: tehdit, sure, neden."""
    from sentinel.notify.discord import DiscordNotifier

    embed = DiscordNotifier("https://example/webhook")._embed(_raid_event())
    names = {f["name"] for f in embed["fields"]}

    assert {"Bölge", "Tetikleme", "Tool cupboard'a", "Kalan yol", "Neden"} <= names
    assert "YÜKSEK" in embed["description"]
    assert "█" in embed["description"], "Tehdit olcegi gorunmeli"

    eta = next(f for f in embed["fields"] if f["name"] == "Tool cupboard'a")
    assert "1:04" in eta["value"]
    assert "2 patlayıcı kaldı" in eta["value"]


def test_discord_embed_survives_missing_context():
    """Baglam olmadan da (ornegin sistem olaylari) cokmemeli."""
    from sentinel.models import Event, EventKind, Severity
    from sentinel.notify.discord import DiscordNotifier

    plain = Event(kind=EventKind.CONNECTION_DOWN, severity=Severity.WARN,
                  title="Baglanti koptu")
    embed = DiscordNotifier("https://example/webhook")._embed(plain)

    assert embed["title"].endswith("Baglanti koptu")
    assert embed["color"]


def test_discord_embed_has_no_external_assets():
    """Barindirilan gorsel yok - internet yokken kirik gorunmesin."""
    from sentinel.notify.discord import DiscordNotifier

    embed = DiscordNotifier("https://example/webhook")._embed(_raid_event())

    assert "image" not in embed
    assert "thumbnail" not in embed
