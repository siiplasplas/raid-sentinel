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
