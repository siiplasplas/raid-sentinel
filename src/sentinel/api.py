"""HTTP arayuzu: panel, canli olay akisi ve saglik ucu.

Panel bilerek derleme gerektirmeyen tek bir sayfa. React/Vite kurmak, tek
kullanicilik kendi kendine barindirilan bir arac icin node_modules ve ayri
bir build adimi demek olurdu; sayfa Python paketiyle birlikte dagitiliyor.

Canli veri tek bir SSE baglantisindan geliyor: olaylar olustuklari anda,
durum (saglik + aktif raidler) birkac saniyede bir. Boylece panel ile
bildirimler ayni gercegi goruyor - ikisi de ayni olay veri yolundan besleniyor.

Varsayilan olarak yalnizca 127.0.0.1'e baglanir. Disariya acacaksan onune
ters vekil ve kimlik dogrulama koy - burada oturum yonetimi yok.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Body, FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from sentinel.base_model import BaseGraph, BaseModelError, base_path
from sentinel.escalation import Contact
from sentinel.models import Event, EventKind, Severity
from sentinel.raiddata import DEPLOYABLE_COST, WALL_COST, Tier, WeaponClass
from sentinel.settings_store import SettingsError, apply_updates, describe
from sentinel.twilio_caller import TwilioCaller

if TYPE_CHECKING:
    from sentinel.app import Sentinel

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "web"
EXAMPLE_BASE = Path(__file__).parent / "base.example.json"

# Panelde gorunecek okunabilir adlar. Anahtarlar raid maliyet tablosundan.
_TIER_LABELS: dict[Tier, str] = {
    Tier.TWIG: "Çıta (twig)",
    Tier.WOOD: "Ahşap",
    Tier.STONE: "Taş",
    Tier.METAL: "Sac",
    Tier.ARMORED: "Zırhlı",
}

_DEPLOYABLE_LABELS: dict[str, str] = {
    "wooden_door": "Ahşap kapı",
    "sheet_metal_door": "Sac kapı",
    "garage_door": "Garaj kapısı",
    "armored_door": "Zırhlı kapı",
    "ladder_hatch": "Ladder hatch",
    "high_stone_wall": "Yüksek taş duvar",
    "high_wood_wall": "Yüksek ahşap duvar",
    "metal_embrasure": "Metal embrasure",
    "window_bars": "Pencere demiri",
    "auto_turret": "Auto turret",
}

# Durum kaç saniyede bir itilecek. Vekil sunucularin baglantiyi bosta
# kesmemesi icin ayni zamanda canlilik sinyali gorevi goruyor.
_STATE_INTERVAL = 3.0


def _serialize(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "ts": event.ts,
        "kind": str(event.kind),
        "severity": event.severity.name.lower(),
        "title": event.title,
        "body": event.body,
        "zone": event.zone,
        "entity_id": event.entity_id,
        "entity_name": event.entity_name,
    }


def create_app(sentinel: Sentinel) -> FastAPI:
    app = FastAPI(title="Raid Sentinel", version="0.1.0", docs_url="/api/docs")

    # --- panel -------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        page = STATIC_DIR / "index.html"
        if not page.exists():
            return HTMLResponse("<h1>Panel dosyasi bulunamadi</h1>", status_code=500)
        return HTMLResponse(page.read_text(encoding="utf-8"))

    # --- canli akis --------------------------------------------------------

    @app.get("/api/stream")
    async def stream() -> StreamingResponse:
        async def generate():
            queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=512)

            async def pump_events() -> None:
                async for event in sentinel.bus.stream():
                    with contextlib.suppress(asyncio.QueueFull):
                        queue.put_nowait(("event", _serialize(event)))

            async def pump_state() -> None:
                while True:
                    with contextlib.suppress(asyncio.QueueFull):
                        queue.put_nowait(("state", await sentinel.health()))
                    await asyncio.sleep(_STATE_INTERVAL)

            tasks = [
                asyncio.create_task(pump_events(), name="sse-events"),
                asyncio.create_task(pump_state(), name="sse-state"),
            ]
            try:
                while True:
                    name, payload = await queue.get()
                    yield f"event: {name}\ndata: {json.dumps(payload, default=str)}\n\n"
            except asyncio.CancelledError:
                raise
            finally:
                for task in tasks:
                    task.cancel()

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # nginx arkasinda tamponlamayi kapat
            },
        )

    # --- veri uclari -------------------------------------------------------

    @app.get("/health")
    async def health() -> JSONResponse:
        """Dis izleme servisleri (dead-man switch) icin.

        Saglikli degilse 503 doner - boylece sistem sessizce olduginde
        haberin olur.
        """
        data = await sentinel.health()

        fcm = data.get("fcm") or {}
        socket = data.get("socket") or {}
        ok = bool(data.get("running")) and bool(fcm.get("healthy", False))
        if data.get("server"):
            ok = ok and bool(socket.get("connected", False))

        return JSONResponse(status_code=200 if ok else 503, content={"ok": ok, **data})

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        return await sentinel.health()

    @app.get("/api/raids")
    async def raids() -> dict[str, Any]:
        return {"raids": sentinel.raid_snapshot()}

    @app.get("/api/events")
    async def events(
        limit: int = Query(default=100, ge=1, le=1000),
        min_severity: str = Query(default="debug"),
    ) -> dict[str, Any]:
        try:
            threshold = Severity.parse(min_severity)
        except ValueError as exc:
            return {"error": str(exc), "events": []}

        rows = await sentinel.store.recent_events(limit=limit, min_severity=threshold)
        return {"count": len(rows), "events": [_serialize(e) for e in rows]}

    @app.get("/api/entities")
    async def entities() -> dict[str, Any]:
        rows = await sentinel.store.entities()
        return {
            "count": len(rows),
            "entities": [
                {
                    "entity_id": e.entity_id,
                    "name": e.name,
                    "type": str(e.entity_type),
                    "zone": e.zone,
                    "seismic_level": e.seismic_level,
                    "last_value": e.last_value,
                    "last_seen": e.last_seen,
                }
                for e in rows
            ],
        }

    # --- ayarlar -----------------------------------------------------------

    @app.get("/api/settings")
    async def get_settings_view() -> dict[str, Any]:
        return {"fields": describe(sentinel.settings)}

    @app.post("/api/settings")
    async def update_settings(updates: dict[str, Any] = Body(...)) -> JSONResponse:
        try:
            _, changed = apply_updates(sentinel.data_dir, updates, sentinel.settings)
        except SettingsError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})

        if changed:
            await sentinel.reload_settings()

        return JSONResponse(
            content={
                "changed": changed,
                "fields": describe(sentinel.settings),
            }
        )

    # --- cihaz yapilandirmasi ---------------------------------------------

    @app.patch("/api/entities/{entity_id}")
    async def update_entity(
        entity_id: int, body: dict[str, Any] = Body(...)
    ) -> JSONResponse:
        zone = body.get("zone")
        zone = str(zone).strip() if zone not in (None, "") else None

        raw_level = body.get("seismic_level")
        level: int | None = None
        if raw_level not in (None, "", "auto"):
            try:
                level = int(raw_level)
            except (TypeError, ValueError):
                return JSONResponse(
                    status_code=400, content={"error": "Sismik kademe sayi olmali"}
                )
            if level not in (1, 2, 3):
                return JSONResponse(
                    status_code=400,
                    content={"error": "Sismik kademe 1, 2 veya 3 olmali"},
                )

        ok = await sentinel.store.set_entity_config(
            entity_id, zone=zone, seismic_level=level
        )
        if not ok:
            return JSONResponse(status_code=404, content={"error": "Cihaz bulunamadi"})
        return JSONResponse(content={"ok": True})

    @app.delete("/api/entities/{entity_id}")
    async def delete_entity(entity_id: int) -> JSONResponse:
        ok = await sentinel.store.delete_entity(entity_id)
        if not ok:
            return JSONResponse(status_code=404, content={"error": "Cihaz bulunamadi"})
        return JSONResponse(content={"ok": True})

    @app.post("/api/entities/{entity_id}/simulate")
    async def simulate_entity(entity_id: int) -> JSONResponse:
        """Cihazi sahte bir tetiklemeyle gercek boru hattindan gecirir.

        Toplayici, puanlama ve bildirim kanallari dahil her sey calisir -
        raid beklemeden tum zincirin dogru kuruldugunu gorebilmek icin.
        """
        entity = next(
            (e for e in await sentinel.store.entities() if e.entity_id == entity_id),
            None,
        )
        if entity is None:
            return JSONResponse(status_code=404, content={"error": "Cihaz bulunamadi"})

        zone, level = await sentinel.resolve_zone_for(entity)
        await sentinel.raid.feed(
            zone=zone, entity_name=entity.name, entity_id=entity_id, seismic_level=level
        )
        return JSONResponse(
            content={"ok": True, "zone": zone, "seismic_level": level}
        )

    # --- kurulum durumu ----------------------------------------------------

    @app.get("/api/setup")
    async def setup_status() -> dict[str, Any]:
        """Panelin kurulum kontrol listesi.

        Sessiz yanlis yapilandirmayi gorunur kilmak icin: ozellikle cihaz
        adindaki bolge ile us tanimindaki dugum adi tutmuyorsa ETA sessizce
        kayboluyor, kullanici bunu bilmiyor.
        """
        entities = await sentinel.store.entities()
        graph = sentinel.base
        known_zones = sorted(graph.zones) if graph else []

        device_zones = sorted({e.zone for e in entities if e.zone})
        unmatched = (
            [z for z in device_zones if z not in known_zones] if graph else []
        )
        orphan_zones = (
            [z for z in known_zones if z not in device_zones and z != graph.target]
            if graph
            else []
        )

        return {
            "paired_server": sentinel.server_id(),
            "device_count": len(entities),
            "devices_without_zone": [e.name for e in entities if not e.zone],
            "base_loaded": graph is not None,
            "base_target": graph.target if graph else None,
            "known_zones": known_zones,
            "device_zones": device_zones,
            "unmatched_zones": unmatched,
            "zones_without_device": orphan_zones,
            "channels": [n.name for n in sentinel.router.notifiers],
            "escalation_enabled": bool(
                sentinel.escalation and sentinel.escalation.enabled
            ),
        }

    # --- eylemler ----------------------------------------------------------

    @app.post("/api/actions/test-notify")
    async def action_test_notify() -> dict[str, Any]:
        channels = [n.name for n in sentinel.router.notifiers]
        if not channels:
            return {"ok": False, "error": "Yapilandirilmis kanal yok"}

        await sentinel.router.dispatch(
            Event(
                kind=EventKind.RAID_STARTED,
                severity=Severity.CRITICAL,
                title="Deneme: Garaj saldiri altinda",
                body="Bu bir testtir. Gercek bir raid degil.",
                zone="Garaj",
                entity_name="Garaj S3",
            )
        )
        return {"ok": True, "channels": channels}

    @app.post("/api/actions/test-call")
    async def action_test_call(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        settings = sentinel.settings
        if not (settings.twilio_account_sid and settings.twilio_auth_token):
            return {"ok": False, "error": "Twilio ayarlari eksik"}

        contacts = Contact.parse_list(settings.escalation_contacts)
        target = str(body.get("to") or "").strip() or (
            contacts[0].phone if contacts else ""
        )
        if not target:
            return {"ok": False, "error": "Aranacak numara yok"}

        try:
            caller = TwilioCaller(
                settings.twilio_account_sid,
                settings.twilio_auth_token,
                settings.twilio_from_number,
                language=settings.twilio_language,
                voice=settings.twilio_voice,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        try:
            result = await caller.place_call(
                target,
                "Dikkat. Garaj bolgesi saldiri altinda. "
                "Bu bir testtir, gercek bir raid degil.",
            )
        finally:
            await caller.aclose()

        return {
            "ok": not result.error,
            "outcome": str(result.outcome),
            "duration": result.duration_seconds,
            "price_usd": result.price_usd,
            "error": result.error,
            "to": target,
        }

    # --- us tanimi ---------------------------------------------------------

    @app.put("/api/base")
    async def save_base(body: dict[str, Any] = Body(...)) -> JSONResponse:
        try:
            BaseGraph.from_dict(body)
        except BaseModelError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})

        path = base_path(sentinel.data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        temp.replace(path)

        sentinel.reload_base()
        return JSONResponse(content={"ok": True})

    @app.get("/api/base/raw")
    async def base_raw() -> dict[str, Any]:
        """Duzenleyici icin ham tanim. Dosya yoksa ornek sablon doner."""
        path = base_path(sentinel.data_dir)
        if path.exists():
            return {"exists": True, "text": path.read_text(encoding="utf-8")}

        # Sablon pakete gomulu: kaynak agacina gore yol hesaplamak
        # pip/Docker kurulumunda yanlis yeri gosterirdi.
        template = EXAMPLE_BASE.read_text(encoding="utf-8") if EXAMPLE_BASE.exists() else "{}"
        return {"exists": False, "text": template}

    @app.get("/api/base/options")
    async def base_options() -> dict[str, Any]:
        """Editorun kullanacagi gecerli engel turleri.

        Listeler raid maliyet tablosundan uretiliyor; JS'te ikinci bir
        kopya tutmak, tablo degistiginde sessizce eskiyecek bir kaynak
        yaratirdi.
        """
        return {
            "tiers": [
                {"value": tier.value, "label": _TIER_LABELS.get(tier, tier.value),
                 "cost_c4": WALL_COST[tier][WeaponClass.C4]}
                for tier in WALL_COST
            ],
            "deployables": [
                {"value": key, "label": _DEPLOYABLE_LABELS.get(key, key),
                 "cost_c4": costs.get(WeaponClass.C4, 0)}
                for key, costs in DEPLOYABLE_COST.items()
            ],
        }

    @app.get("/api/base")
    async def base() -> dict[str, Any]:
        graph = sentinel.base
        if graph is None:
            return {"loaded": False}
        return {
            "loaded": True,
            "name": graph.name,
            "target": graph.target,
            "zones": sorted(graph.zones),
            "edges": [
                {
                    "from": e.a,
                    "to": e.b,
                    "label": e.label,
                    "cost_c4": e.cost(WeaponClass.C4),
                    "cost_rocket": e.cost(WeaponClass.ROCKET),
                }
                for e in graph.edges
            ],
        }

    return app
