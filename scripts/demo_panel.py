"""Paneli sahte veriyle ayaga kaldirir - Rust+ eslesmesi gerekmez.

    python scripts/demo_panel.py
    python scripts/demo_panel.py --lang en --port 8789

Amac gelistirme sirasinda arayuzu gorebilmek. Gercek sistem icin
'sentinel run' kullan.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sentinel.api import create_app, example_base_path  # noqa: E402
from sentinel.app import Sentinel  # noqa: E402
from sentinel.base_model import BaseGraph  # noqa: E402
from sentinel.config import Settings  # noqa: E402
from sentinel.models import Entity, EntityType, Event, EventKind, Severity  # noqa: E402


async def build(lang: str = "tr") -> Sentinel:
    tmp = Path(tempfile.mkdtemp())
    label = "Demo Server" if lang == "en" else "Demo Sunucu"
    settings = Settings(db_path=tmp / "demo.db", rust_server_label=label,
                        language=lang)
    sentinel = Sentinel(settings)

    sentinel.store.connect()
    sentinel.bus.subscribe(sentinel._store_event)
    sentinel.base = BaseGraph.load(example_base_path())
    sentinel._build_escalation()
    sentinel.started_at = time.time() - 7200
    sentinel._running.set()

    server_id = "10.0.0.1:28082"
    # Bolge adlari ornek us tanimiyla ayni olmali, yoksa ETA hesaplanmaz.
    devices = {
        "tr": [(1001, "Kompound S3", "Kompound"), (1002, "Garaj S3", "Garaj"),
               (1003, "Airlock S2", "Airlock"), (1004, "Çatı HBHF", "Çatı")],
        "en": [(1001, "Compound S3", "Compound"), (1002, "Garage S3", "Garage"),
               (1003, "Airlock S2", "Airlock"), (1004, "Roof HBHF", "Roof")],
    }[lang]
    for eid, name, zone in devices:
        await sentinel.store.upsert_entity(
            Entity(entity_id=eid, entity_type=EntityType.ALARM, name=name,
                   server_id=server_id, zone=zone, last_seen=time.time() - 30)
        )

    # Gecmis olaylar
    history = {
        "tr": [
            (EventKind.SENTINEL_STARTED, Severity.INFO, "Raid Sentinel çalışıyor",
             "Sunucu: 10.0.0.1:28082 · Kanallar: discord, ntfy"),
            (EventKind.ENTITY_PAIRED, Severity.INFO, "Cihaz eşleştirildi: Garaj S3",
             "alarm · sismik kademe 3"),
            (EventKind.RAID_ENDED, Severity.INFO, "Çatı: saldırı durdu",
             "2 tetikleme · 4 dk süredir"),
        ],
        "en": [
            (EventKind.SENTINEL_STARTED, Severity.INFO, "Raid Sentinel running",
             "Server: 10.0.0.1:28082 · Channels: discord, ntfy"),
            (EventKind.ENTITY_PAIRED, Severity.INFO, "Device paired: Garage S3",
             "alarm · seismic tier 3"),
            (EventKind.RAID_ENDED, Severity.INFO, "Roof: attack stopped",
             "2 triggers · 4 min so far"),
        ],
    }[lang]
    for kind, sev, title, body in history:
        await sentinel._publish(Event(kind=kind, severity=sev, title=title, body=body))

    # Suregelen bir raid uret
    now = time.time()
    zone, entity = devices[0][2], devices[0][1]
    for index in range(6):
        await sentinel.raid.feed(
            zone=zone,
            entity_name=entity,
            entity_id=1001,
            seismic_level=3,
            ts=now - (5 - index) * 22,
        )
    return sentinel


async def main() -> None:
    parser = argparse.ArgumentParser(description="Demo panel")
    parser.add_argument("--lang", choices=("tr", "en"), default="tr")
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args()

    sentinel = await build(args.lang)
    config = uvicorn.Config(
        create_app(sentinel), host="127.0.0.1", port=args.port,
        log_level="warning", access_log=False,
    )
    print(f"\n  Demo panel ({args.lang}): http://127.0.0.1:{args.port}/\n")
    await uvicorn.Server(config).serve()


if __name__ == "__main__":
    with __import__("contextlib").suppress(KeyboardInterrupt):
        asyncio.run(main())
