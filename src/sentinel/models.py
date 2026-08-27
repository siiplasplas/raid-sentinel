"""Sistemin ortak dili: olaylar, siddet seviyeleri, cihazlar.

Bu modul hicbir dis kutuphaneye bagli degil. Rust+ istemcisi de, bildirim
suruculeri de, panel de ayni Event tipini konusur.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any


class Severity(IntEnum):
    """Karsilastirilabilir olmasi icin IntEnum: esik kontrolu `>=` ile yapilir."""

    DEBUG = 10
    INFO = 20
    WARN = 30
    CRITICAL = 40

    @classmethod
    def parse(cls, value: str | int | Severity) -> Severity:
        if isinstance(value, Severity):
            return value
        if isinstance(value, int):
            return cls(value)
        try:
            return cls[value.strip().upper()]
        except KeyError as exc:
            valid = ", ".join(s.name.lower() for s in cls)
            raise ValueError(
                f"Gecersiz siddet seviyesi: {value!r}. Gecerli olanlar: {valid}"
            ) from exc


class EventKind(StrEnum):
    """Sistemde olabilecek her sey. Yeni tur eklemek geriye donuk uyumlu."""

    # Eskalasyon zinciri
    ESCALATION_STARTED = "escalation_started"
    ESCALATION_ACKNOWLEDGED = "escalation_acknowledged"
    ESCALATION_EXHAUSTED = "escalation_exhausted"
    CALL_BUDGET_EXCEEDED = "call_budget_exceeded"

    # Toplayici katmanin urettigi ozet olaylar
    RAID_STARTED = "raid_started"
    RAID_PROGRESS = "raid_progress"
    RAID_ENDED = "raid_ended"

    # Rust+ kaynakli
    ALARM_TRIGGERED = "alarm_triggered"
    ENTITY_CHANGED = "entity_changed"
    ENTITY_PAIRED = "entity_paired"
    SERVER_PAIRED = "server_paired"
    PLAYER_DIED = "player_died"
    PLAYER_LOGGED_IN = "player_logged_in"

    # Sistem kaynakli - sessiz ariza korumasi bunlarin uzerine kurulu
    CONNECTION_UP = "connection_up"
    CONNECTION_DOWN = "connection_down"
    HEALTHCHECK_FAILED = "healthcheck_failed"
    WIPE_SUSPECTED = "wipe_suspected"
    SETTINGS_CHANGED = "settings_changed"
    SENTINEL_STARTED = "sentinel_started"
    SENTINEL_STOPPING = "sentinel_stopping"


class EntityType(StrEnum):
    """Rust+ yalnizca bu ucunu taniyor (rustplus.proto: AppEntityType)."""

    SWITCH = "switch"
    ALARM = "alarm"
    STORAGE_MONITOR = "storage_monitor"

    @classmethod
    def from_proto(cls, value: int) -> EntityType:
        # AppEntityType: Switch = 1, Alarm = 2, StorageMonitor = 3
        mapping = {1: cls.SWITCH, 2: cls.ALARM, 3: cls.STORAGE_MONITOR}
        if value not in mapping:
            raise ValueError(f"Bilinmeyen entity tipi: {value}")
        return mapping[value]


@dataclass(slots=True)
class Entity:
    """Eslestirilmis bir Rust+ cihazi.

    `zone` F3'te uslerin graf modeline baglanacak; F1'de sadece etiket.
    """

    entity_id: int
    entity_type: EntityType
    name: str
    server_id: str
    zone: str | None = None
    # Panelden elle atanan sismik kademe. None ise cihaz adindan cikarilir
    # ("Garaj S3" -> 3). Elle atama adin onune gecer.
    seismic_level: int | None = None
    last_value: bool | None = None
    last_seen: float | None = None
    paired_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class Event:
    """Sistemde olan bitenin degismez kaydi.

    `raw` ham yuk - ileride model degisirse gecmisi yeniden yorumlayabilelim
    diye her zaman saklanir.
    """

    kind: EventKind
    severity: Severity
    title: str
    body: str = ""
    server_id: str | None = None
    entity_id: int | None = None
    entity_name: str | None = None
    zone: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def raw_json(self) -> str:
        try:
            return json.dumps(self.raw, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return json.dumps({"_serialize_error": repr(self.raw)}, ensure_ascii=False)

    def summary(self) -> str:
        """Tek satirlik log/konsol gosterimi."""
        where = f" [{self.zone}]" if self.zone else ""
        return f"{self.severity.name:<8} {self.kind}{where}  {self.title}"
