"""Olay deposu ve cihaz kaydi (SQLite, WAL).

Neden SQLite: olay hacmi saniyede birkac tane. Postgres'in isletme maliyeti
bu is icin gereksiz. WAL modu, panel okurken yazmayi bloke etmiyor.

Her sey `raw` JSON ile birlikte saklanir; F3'teki ETA modeli gecmis olaylari
yeniden yorumlayacak, o yuzden hicbir sey kaybedilmez.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sentinel.models import Entity, EntityType, Event, EventKind, SensorKind, Severity

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    ts          REAL NOT NULL,
    kind        TEXT NOT NULL,
    severity    INTEGER NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    server_id   TEXT,
    entity_id   INTEGER,
    entity_name TEXT,
    zone        TEXT,
    raw         TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_entity_ts ON events(entity_id, ts DESC);

CREATE TABLE IF NOT EXISTS entities (
    entity_id   INTEGER NOT NULL,
    server_id   TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    name          TEXT NOT NULL,
    zone          TEXT,
    seismic_level INTEGER,
    sensor_kind   TEXT,
    last_value  INTEGER,
    last_seen   REAL,
    paired_at   REAL NOT NULL,
    PRIMARY KEY (entity_id, server_id)
);

CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


class Store:
    """Senkron SQLite'i asyncio'ya `to_thread` ile baglar.

    Tek baglanti + kilit: bu yuk seviyesinde havuzun karmasikligi gereksiz.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = asyncio.Lock()
        self._conn: sqlite3.Connection | None = None

    # --- yasam dongusu -----------------------------------------------------

    def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
        self._conn = conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Store baglanmadi - once connect() cagir")
        return self._conn

    # --- olaylar -----------------------------------------------------------

    def _insert_event(self, event: Event) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO events
               (id, ts, kind, severity, title, body, server_id, entity_id, entity_name, zone, raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id,
                event.ts,
                str(event.kind),
                int(event.severity),
                event.title,
                event.body,
                event.server_id,
                event.entity_id,
                event.entity_name,
                event.zone,
                event.raw_json(),
            ),
        )
        self.conn.commit()

    async def add_event(self, event: Event) -> None:
        async with self._lock:
            await asyncio.to_thread(self._insert_event, event)

    def _select_events(
        self,
        limit: int,
        since: float | None,
        kinds: tuple[str, ...] | None,
        min_severity: int,
    ) -> list[Event]:
        sql = "SELECT * FROM events WHERE severity >= ?"
        params: list[Any] = [min_severity]
        if since is not None:
            sql += " AND ts >= ?"
            params.append(since)
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_event(r) for r in rows]

    async def recent_events(
        self,
        *,
        limit: int = 100,
        since: float | None = None,
        kinds: Iterable[EventKind] | None = None,
        min_severity: Severity = Severity.DEBUG,
    ) -> list[Event]:
        kind_tuple = tuple(str(k) for k in kinds) if kinds else None
        async with self._lock:
            return await asyncio.to_thread(
                self._select_events, limit, since, kind_tuple, int(min_severity)
            )

    # --- cihazlar ----------------------------------------------------------

    def _upsert_entity(self, entity: Entity) -> None:
        self.conn.execute(
            """INSERT INTO entities
               (entity_id, server_id, entity_type, name, zone, seismic_level,
                sensor_kind, last_value, last_seen, paired_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(entity_id, server_id) DO UPDATE SET
                   entity_type = excluded.entity_type,
                   name        = excluded.name,
                   -- zone ve kademe panelden elle atanmis olabilir:
                   -- yeni deger bossa eskisini koru
                   zone          = COALESCE(excluded.zone, entities.zone),
                   seismic_level = COALESCE(excluded.seismic_level,
                                            entities.seismic_level),
                   sensor_kind   = COALESCE(excluded.sensor_kind,
                                            entities.sensor_kind),
                   last_value  = excluded.last_value,
                   last_seen   = excluded.last_seen""",
            (
                entity.entity_id,
                entity.server_id,
                str(entity.entity_type),
                entity.name,
                entity.zone,
                entity.seismic_level,
                str(entity.sensor_kind) if entity.sensor_kind else None,
                None if entity.last_value is None else int(entity.last_value),
                entity.last_seen,
                entity.paired_at,
            ),
        )
        self.conn.commit()

    async def upsert_entity(self, entity: Entity) -> None:
        async with self._lock:
            await asyncio.to_thread(self._upsert_entity, entity)

    def _select_entities(self, server_id: str | None) -> list[Entity]:
        if server_id:
            rows = self.conn.execute(
                "SELECT * FROM entities WHERE server_id = ? ORDER BY name", (server_id,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM entities ORDER BY name").fetchall()
        return [_row_to_entity(r) for r in rows]

    async def entities(self, server_id: str | None = None) -> list[Entity]:
        async with self._lock:
            return await asyncio.to_thread(self._select_entities, server_id)

    def _update_entity_value(
        self, entity_id: int, server_id: str, value: bool | None, seen: float
    ) -> None:
        self.conn.execute(
            "UPDATE entities SET last_value = ?, last_seen = ?"
            " WHERE entity_id = ? AND server_id = ?",
            (None if value is None else int(value), seen, entity_id, server_id),
        )
        self.conn.commit()

    async def update_entity_value(
        self, entity_id: int, server_id: str, value: bool | None
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._update_entity_value, entity_id, server_id, value, time.time()
            )

    def _set_entity_config(
        self, entity_id: int, zone: str | None, level: int | None, kind: str | None
    ) -> int:
        cursor = self.conn.execute(
            "UPDATE entities SET zone = ?, seismic_level = ?, sensor_kind = ?"
            " WHERE entity_id = ?",
            (zone, level, kind, entity_id),
        )
        self.conn.commit()
        return cursor.rowcount

    async def set_entity_config(
        self,
        entity_id: int,
        *,
        zone: str | None,
        seismic_level: int | None,
        sensor_kind: str | None = None,
    ) -> bool:
        """Panelden bolge/kademe atamasi. Degistirilen satir varsa True.

        Bilerek yalnizca entity_id ile eslesiyor, server_id ile degil.
        Wipe sonrasi yeniden eslesmede sunucunun ip:port'u degisebiliyor;
        server_id sarti konursa eski kayitlar oksuz kalir ve panelden
        duzeltilemez hale gelir. Entity id'leri zaten cihaza ozgu.
        """
        async with self._lock:
            rows = await asyncio.to_thread(
                self._set_entity_config, entity_id, zone, seismic_level, sensor_kind
            )
        return rows > 0

    def _delete_entity(self, entity_id: int) -> int:
        cursor = self.conn.execute(
            "DELETE FROM entities WHERE entity_id = ?", (entity_id,)
        )
        self.conn.commit()
        return cursor.rowcount

    async def delete_entity(self, entity_id: int) -> bool:
        """Cihaz kaydini siler.

        Oyunda yok edilmis ya da wipe sonrasi gecersiz kalmis cihazlar
        listeyi kirletiyor. Silmek Rust+ tarafini etkilemez - cihaz hala
        eslesmisse bir sonraki bildirimde yeniden eklenir.
        """
        async with self._lock:
            rows = await asyncio.to_thread(self._delete_entity, entity_id)
        return rows > 0

    # --- gecmis ve analitik ------------------------------------------------

    def _raid_stats(self, since: float) -> list[dict[str, Any]]:
        """Bolge basina raid ozeti.

        "Yanlis alarm" = kritik seviyeye hic ulasmamis oturum. Sensor
        yerlesimini iyilestirmek icin bakilacak sayi bu.
        """
        rows = self.conn.execute(
            """SELECT zone,
                      COUNT(*) AS sessions,
                      SUM(CASE WHEN severity >= ? THEN 1 ELSE 0 END) AS serious,
                      MAX(ts) AS last_ts
               FROM events
               WHERE kind = 'raid_started' AND ts >= ? AND zone IS NOT NULL
               GROUP BY zone
               ORDER BY sessions DESC""",
            (int(Severity.CRITICAL), since),
        ).fetchall()
        return [
            {
                "zone": r["zone"],
                "sessions": r["sessions"],
                "serious": r["serious"] or 0,
                "false_alarms": r["sessions"] - (r["serious"] or 0),
                "last_ts": r["last_ts"],
            }
            for r in rows
        ]

    async def raid_stats(self, *, since: float = 0.0) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._raid_stats, since)

    def _device_stats(self, since: float) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT entity_name, COUNT(*) AS triggers, MAX(ts) AS last_ts
               FROM events
               WHERE kind IN ('alarm_triggered', 'entity_changed')
                 AND ts >= ? AND entity_name IS NOT NULL AND entity_name != ''
               GROUP BY entity_name
               ORDER BY triggers DESC""",
            (since,),
        ).fetchall()
        return [
            {"name": r["entity_name"], "triggers": r["triggers"], "last_ts": r["last_ts"]}
            for r in rows
        ]

    async def device_stats(self, *, since: float = 0.0) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._device_stats, since)

    # --- kucuk durum anahtarlari ------------------------------------------

    def _kv_set(self, key: str, value: str) -> None:
        self.conn.execute(
            """INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                              updated_at = excluded.updated_at""",
            (key, value, time.time()),
        )
        self.conn.commit()

    def _kv_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    async def set_state(self, key: str, value: Any) -> None:
        payload = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        async with self._lock:
            await asyncio.to_thread(self._kv_set, key, payload)

    async def get_state(self, key: str, default: Any = None) -> Any:
        async with self._lock:
            raw = await asyncio.to_thread(self._kv_get, key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw


def _migrate(conn: sqlite3.Connection) -> None:
    """Eski veritabanlarina eksik sutunlari ekler.

    CREATE TABLE IF NOT EXISTS mevcut tabloyu degistirmiyor, o yuzden
    surum yukseltmelerinde sutunlari ayrica kontrol etmek gerekiyor.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(entities)")}
    if "seismic_level" not in existing:
        conn.execute("ALTER TABLE entities ADD COLUMN seismic_level INTEGER")
        log.info("Veritabani guncellendi: entities.seismic_level eklendi")
    if "sensor_kind" not in existing:
        conn.execute("ALTER TABLE entities ADD COLUMN sensor_kind TEXT")
        log.info("Veritabani guncellendi: entities.sensor_kind eklendi")


def _row_to_event(row: sqlite3.Row) -> Event:
    try:
        raw = json.loads(row["raw"])
    except json.JSONDecodeError:
        raw = {}
    return Event(
        id=row["id"],
        ts=row["ts"],
        kind=EventKind(row["kind"]),
        severity=Severity(row["severity"]),
        title=row["title"],
        body=row["body"],
        server_id=row["server_id"],
        entity_id=row["entity_id"],
        entity_name=row["entity_name"],
        zone=row["zone"],
        raw=raw,
    )


def _row_to_entity(row: sqlite3.Row) -> Entity:
    return Entity(
        entity_id=row["entity_id"],
        server_id=row["server_id"],
        entity_type=EntityType(row["entity_type"]),
        name=row["name"],
        zone=row["zone"],
        seismic_level=row["seismic_level"],
        sensor_kind=SensorKind(row["sensor_kind"]) if row["sensor_kind"] else SensorKind.UNKNOWN,
        last_value=None if row["last_value"] is None else bool(row["last_value"]),
        last_seen=row["last_seen"],
        paired_at=row["paired_at"],
    )
