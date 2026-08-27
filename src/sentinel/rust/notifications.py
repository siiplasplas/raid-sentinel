"""FCM bildirimlerini ayristirir ve siniflandirir.

Neden bu kadar savunmaci: Rust+ bildirimleri Expo uzerinden geliyor ve
gercek yuk ic ice JSON string olarak gomulu. Yapinin tam sekli ancak canli
bir hesapla dogrulanabilir, o yuzden birden fazla olasi sekli deniyoruz ve
tanimadigimiz her seyi ham haliyle sakliyoruz - bilinmeyen bir bildirim
sessizce kaybolmaktansa UNKNOWN olarak kaydedilsin.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

log = logging.getLogger(__name__)

# Ic ice gomulu JSON'un bulunabilecegi anahtarlar, olasilik sirasiyla.
_NESTED_KEYS = ("body", "data", "message")

# Gercek Rust+ yukunu tanimamizi saglayan alanlar.
_RUSTPLUS_MARKERS = frozenset(
    {"ip", "port", "playerToken", "playerId", "entityId", "entityType", "entityName", "type"}
)


class NotificationKind(StrEnum):
    SERVER_PAIRING = "server_pairing"
    ENTITY_PAIRING = "entity_pairing"
    ALARM = "alarm"
    PLAYER_DIED = "player_died"
    PLAYER_LOGGED_IN = "player_logged_in"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class RustNotification:
    kind: NotificationKind
    data: dict[str, Any] = field(default_factory=dict)
    title: str = ""
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    # --- kolay erisim ------------------------------------------------------

    @property
    def entity_id(self) -> int | None:
        value = self.data.get("entityId")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def entity_type(self) -> int | None:
        value = self.data.get("entityType")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def entity_name(self) -> str:
        return str(self.data.get("entityName") or "")

    @property
    def server_name(self) -> str:
        return str(self.data.get("name") or "")


def _coerce_dict(value: Any) -> dict[str, Any] | None:
    """String ise JSON olarak cozmeyi dener; dict ise oldugu gibi doner."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str | bytes):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def extract_payload(notification: dict[str, Any]) -> dict[str, Any]:
    """Ic ice gomulu Rust+ yukunu bulup duz bir sozluk olarak doner.

    Dis katmandaki alanlar (title, message, channelId) da korunur; ic yuk
    onlarin uzerine yazar.
    """
    flat: dict[str, Any] = {k: v for k, v in notification.items() if not isinstance(v, dict | list)}

    # Katman katman in: body -> icinde yine body olabilir.
    frontier: list[Any] = [notification.get(key) for key in _NESTED_KEYS]
    seen = 0
    while frontier and seen < 8:
        candidate = frontier.pop(0)
        seen += 1
        parsed = _coerce_dict(candidate)
        if parsed is None:
            continue

        if _RUSTPLUS_MARKERS & parsed.keys():
            flat.update({k: v for k, v in parsed.items() if not isinstance(v, dict | list)})

        for key in _NESTED_KEYS:
            if key in parsed:
                frontier.append(parsed[key])

        # title/message dis katmanda bos kalmis olabilir
        for key in ("title", "message"):
            if key in parsed and not flat.get(key):
                flat[key] = parsed[key]

    return flat


def classify(payload: dict[str, Any]) -> NotificationKind:
    kind_hint = str(payload.get("type") or "").lower()
    channel = str(payload.get("channelId") or "").lower()

    if kind_hint == "death" or channel == "player":
        if kind_hint == "death":
            return NotificationKind.PLAYER_DIED
    if kind_hint == "login":
        return NotificationKind.PLAYER_LOGGED_IN
    if kind_hint == "alarm" or channel == "alarm":
        return NotificationKind.ALARM

    has_entity = payload.get("entityId") is not None
    has_server = payload.get("ip") is not None and payload.get("playerToken") is not None

    if kind_hint == "entity" or (has_entity and channel == "pairing"):
        return NotificationKind.ENTITY_PAIRING
    if kind_hint == "server" or (has_server and not has_entity):
        return NotificationKind.SERVER_PAIRING

    # Eslestirme disinda entityId tasiyan tek sey alarm tetiklemesidir.
    if has_entity:
        return NotificationKind.ALARM
    if has_server:
        return NotificationKind.SERVER_PAIRING

    return NotificationKind.UNKNOWN


def parse(notification: dict[str, Any]) -> RustNotification:
    payload = extract_payload(notification)
    kind = classify(payload)

    if kind is NotificationKind.UNKNOWN:
        # Ilk canli calistirmada semayi dogrulayabilmek icin tam yuku logla.
        log.warning("Taninmayan FCM bildirimi: %s", json.dumps(notification, default=str)[:2000])

    return RustNotification(
        kind=kind,
        data=payload,
        title=str(payload.get("title") or ""),
        message=str(payload.get("message") or payload.get("body") or ""),
        raw=notification,
    )
