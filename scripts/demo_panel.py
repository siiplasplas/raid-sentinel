"""Paneli sahte veriyle ayaga kaldirir - Rust+ eslesmesi gerekmez.

    python scripts/demo_panel.py

Amac gelistirme sirasinda arayuzu gorebilmek. Gercek sistem icin
'sentinel run' kullan.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sentinel.api import EXAMPLE_BASE, create_app  # noqa: E402
from sentinel.app import Sentinel  # noqa: E402
from sentinel.base_model import BaseGraph  # noqa: E402
from sentinel.config import Settings  # noqa: E402
from sentinel.models import Entity, EntityType, Event, EventKind, Severity  # noqa: E402


async def build() -> Sentinel:
    tmp = Path(tempfile.mkdtemp())
    settings = Settings(db_path=tmp / "demo.db", rust_server_label="Demo Sunucu")
    sentinel = Sentinel(settings)

    sentinel.store.connect()
    sentinel.bus.subscribe(sentinel._store_event)
    sentinel.base = BaseGraph.load(EXAMPLE_BASE)
    sentinel._build_escalation()
    sentinel.started_at = time.time() - 7200
    sentinel._running.set()

    server_id = "10.0.0.1:28082"
    for eid, name, zone in [
        (1001, "Kompound S3", "Kompound"),
        (1002, "Garaj S3", "Garaj"),
        (1003, "Airlock S2", "Airlock"),
        (1004, "Çatı HBHF", "Çatı"),
    ]:
        await sentinel.store.upsert_entity(
            Entity(entity_id=eid, entity_type=EntityType.ALARM, name=name,
                   server_id=server_id, zone=zone, last_seen=time.time() - 30)
        )

    # Gecmis olaylar
    for kind, sev, title, body in [
        (EventKind.SENTINEL_STARTED, Severity.INFO, "Raid Sentinel calisiyor",
         "Sunucu: 10.0.0.1:28082 · Kanallar: discord, ntfy"),
        (EventKind.ENTITY_PAIRED, Severity.INFO, "Cihaz eşleştirildi: Garaj S3",
         "alarm · sismik kademe 3"),
        (EventKind.RAID_ENDED, Severity.INFO, "Çatı: saldırı durdu",
         "2 tetikleme · 4 dk süredir"),
    ]:
        await sentinel._publish(Event(kind=kind, severity=sev, title=title, body=body))

    # Suregelen bir raid uret
    now = time.time()
    for index in range(6):
        await sentinel.raid.feed(
            zone="Kompound",
            entity_name="Kompound S3",
            entity_id=1001,
            seismic_level=3,
            ts=now - (5 - index) * 22,
        )
    return sentinel


async def main() -> None:
    sentinel = await build()
    config = uvicorn.Config(
        create_app(sentinel), host="127.0.0.1", port=8788,
        log_level="warning", access_log=False,
    )
    print("\n  Demo panel: http://127.0.0.1:8788/\n")
    await uvicorn.Server(config).serve()


if __name__ == "__main__":
    with __import__("contextlib").suppress(KeyboardInterrupt):
        asyncio.run(main())
