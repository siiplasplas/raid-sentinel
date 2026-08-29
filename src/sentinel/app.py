"""Uygulamanin baglanti noktasi: butun parcalar burada birlesir.

Akis:
    FCM bildirimi ------\\
                         >-- Sentinel --> olay veri yolu --> depo
    entity abonelik ----/                                \\-> bildirim kanallari
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path
from typing import Any

from sentinel.base_model import BaseGraph, load_base
from sentinel.bus import EventBus
from sentinel.config import Settings
from sentinel.escalation import Contact, EscalationEngine, QuietHours
from sentinel.eta import estimate as estimate_eta
from sentinel.models import Entity, EntityType, Event, EventKind, SensorKind, Severity
from sentinel.naming import parse_entity_name
from sentinel.notify import build_router_from_settings
from sentinel.raid import RaidAggregator, RaidSession
from sentinel.rust.credentials import Credentials, PairedServer, load_credentials, save_credentials
from sentinel.rust.fcm import FcmSupervisor
from sentinel.rust.notifications import NotificationKind, RustNotification
from sentinel.rust.socket import RustSocketSupervisor
from sentinel.scoring import ThreatLevel, assess, severity_for
from sentinel.settings_store import apply_updates, reapply
from sentinel.spend import MonthlySpend
from sentinel.store import Store
from sentinel.team import Team, load_team
from sentinel.twilio_caller import TwilioCaller

log = logging.getLogger(__name__)


class Sentinel:
    def __init__(self, settings: Settings | None = None) -> None:
        # Taban = kod varsayilanlari + .env (override'siz). Override'lar
        # her yeniden yuklemede bunun UZERINE bininyor; boylece bir
        # override kaldirildiginda deger gercekten tabana geri doner.
        self._base_settings = settings or Settings()
        self.data_dir: Path = self._base_settings.db_path.parent
        self.settings = reapply(self._base_settings, self.data_dir)

        self.store = Store(self.settings.db_path)
        self.bus = EventBus()
        self.router = build_router_from_settings(self.settings)
        self.raid = RaidAggregator(
            self._publish,
            on_session_update=self._on_session_update,
            # Bildirim siddeti tehdit puanindan gelir: tek bir sahte HBHF
            # tetiklemesi kaydedilir ama kimseyi uyandirmaz.
            severity_for=severity_for,
            detail_for=self._detail_for,
            context_for=self._context_for,
        )
        self.base: BaseGraph | None = None
        self.team: Team = Team()

        self.credentials: Credentials = Credentials()
        self.fcm: FcmSupervisor | None = None
        self.socket: RustSocketSupervisor | None = None

        self.spend: MonthlySpend | None = None
        self.escalation: EscalationEngine | None = None
        self._quiet_hours: QuietHours | None = QuietHours.parse(self.settings.quiet_hours)
        self._phone_threshold = _parse_threat(self.settings.phone_min_threat)

        self.started_at: float = 0.0
        self._running = asyncio.Event()

    # --- yasam dongusu -----------------------------------------------------

    async def start(self) -> None:
        self.store.connect()
        self.bus.subscribe(self._store_event)
        # Dogrudan router.dispatch degil: router ayar degisikliginde
        # yeniden kuruluyor, abonelik eski nesneye takili kalmasin.
        self.bus.subscribe(self._dispatch_notifications)

        self.credentials = load_credentials(self.data_dir)
        if not self.credentials.has_fcm:
            raise RuntimeError(
                "FCM kimlik bilgileri yok. Once 'sentinel pair' calistir."
            )

        self.team = load_team(self.data_dir)
        self.base = load_base(self.data_dir)
        if self.base is None:
            log.info(
                "Us tanimi yok (data/base.json) - ETA kapali, alarmlar normal calisir"
            )

        self._build_escalation()
        await self.raid.start()

        self.fcm = FcmSupervisor(
            self.credentials.fcm_credentials,
            self._on_notification,
            expo_push_token=self.credentials.expo_push_token,
        )
        await self.fcm.start()

        server = self._resolve_server()
        if server is not None:
            await self._connect_socket(server)
        else:
            log.warning(
                "Eslesmis sunucu yok. Oyunda 'Pair with Server' yapinca otomatik baglanacak."
            )

        self.started_at = time.time()
        self._running.set()

        await self._publish(
            Event(
                kind=EventKind.SENTINEL_STARTED,
                severity=Severity.INFO,
                title="Raid Sentinel calisiyor",
                body=self._startup_summary(),
            )
        )

    async def stop(self) -> None:
        self._running.clear()
        with contextlib.suppress(Exception):
            await self._publish(
                Event(
                    kind=EventKind.SENTINEL_STOPPING,
                    severity=Severity.WARN,
                    title="Raid Sentinel duruyor",
                    body="Alarm sistemi kapaniyor - bu beklenen bir sey degilse kontrol et.",
                )
            )

        for closer in (
            self.raid.stop,
            lambda: self.fcm.stop() if self.fcm else _noop(),
            lambda: self.socket.stop() if self.socket else _noop(),
            lambda: self.escalation.aclose() if self.escalation else _noop(),
            self.router.aclose,
        ):
            with contextlib.suppress(Exception):
                await closer()

        self.store.close()
        log.info("Kapandi")

    async def run_forever(self) -> None:
        await self.start()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise
        finally:
            await self.stop()

    # --- olay yayini -------------------------------------------------------

    async def _publish(self, event: Event) -> None:
        if event.server_id is None:
            event.server_id = self._server_id()
        await self.bus.publish(event)

    async def publish_event(self, event: Event) -> None:
        """Disaridan (panel eylemleri) olay yayinlamak icin."""
        await self._publish(event)

    async def _store_event(self, event: Event) -> None:
        await self.store.add_event(event)

    async def _dispatch_notifications(self, event: Event) -> None:
        # Kritik olayda takimi etiketle - Discord'da bildirim sesi
        # cikmasi icin metnin icinde gecmesi gerekiyor.
        if event.severity >= Severity.CRITICAL and not event.raw.get("mentions"):
            mentions = self.team.mentions()
            if mentions:
                event.raw["mentions"] = mentions
        await self.router.dispatch(event)

    # --- calisirken yeniden yapilandirma -----------------------------------

    async def reload_settings(self) -> None:
        """Panelden ayar degistiginde yeniden kurar - surec yeniden baslamaz."""
        self.settings = reapply(self._base_settings, self.data_dir)

        old_router = self.router
        self.router = build_router_from_settings(self.settings)
        with contextlib.suppress(Exception):
            await old_router.aclose()

        old_escalation = self.escalation
        self._quiet_hours = QuietHours.parse(self.settings.quiet_hours)
        self._phone_threshold = _parse_threat(self.settings.phone_min_threat)
        self._build_escalation()
        if old_escalation is not None:
            with contextlib.suppress(Exception):
                await old_escalation.aclose()

        log.info("Ayarlar yeniden yuklendi")
        await self._publish(
            Event(
                kind=EventKind.SETTINGS_CHANGED,
                severity=Severity.INFO,
                title="Ayarlar guncellendi",
                body=self._startup_summary(),
            )
        )

    async def update_settings(self, updates: dict[str, Any]) -> list[str]:
        """Panelden gelen ayarlari uygular ve devreye alir.

        Karsilastirma tabana yapiliyor - bkz. settings_store.apply_updates.
        """
        _, changed = apply_updates(self.data_dir, updates, self._base_settings)
        if changed:
            await self.reload_settings()
        return changed

    def reload_base(self) -> None:
        """Us tanimi degistiginde yeniden okur."""
        self.team = load_team(self.data_dir)
        self.base = load_base(self.data_dir)

    # --- FCM bildirimleri --------------------------------------------------

    async def _on_notification(self, notification: RustNotification) -> None:
        handlers = {
            NotificationKind.SERVER_PAIRING: self._handle_server_pairing,
            NotificationKind.ENTITY_PAIRING: self._handle_entity_pairing,
            NotificationKind.ALARM: self._handle_alarm,
            NotificationKind.PLAYER_DIED: self._handle_player_died,
            NotificationKind.PLAYER_LOGGED_IN: self._handle_player_login,
        }
        handler = handlers.get(notification.kind, self._handle_unknown)
        await handler(notification)

    async def _handle_server_pairing(self, notification: RustNotification) -> None:
        data = notification.data
        try:
            server = PairedServer(
                ip=str(data["ip"]),
                port=int(data["port"]),
                player_id=str(data["playerId"]),
                player_token=str(data["playerToken"]),
                name=notification.server_name,
                paired_at=time.time(),
            )
        except (KeyError, TypeError, ValueError) as exc:
            log.error("Sunucu eslestirme bildirimi eksik: %s (%s)", exc, data)
            return

        self.credentials.server = server
        save_credentials(self.data_dir, self.credentials)

        await self._publish(
            Event(
                kind=EventKind.SERVER_PAIRED,
                severity=Severity.INFO,
                title=f"Sunucu eslestirildi: {server.name or server.server_id}",
                body=server.server_id,
                server_id=server.server_id,
                raw=data,
            )
        )
        await self._connect_socket(server)

    async def _handle_entity_pairing(self, notification: RustNotification) -> None:
        entity_id = notification.entity_id
        if entity_id is None:
            log.error("Cihaz eslestirmesinde entityId yok: %s", notification.data)
            return

        raw_name = notification.entity_name or f"Cihaz {entity_id}"
        zone, level = parse_entity_name(raw_name)

        try:
            entity_type = EntityType.from_proto(notification.entity_type or 2)
        except ValueError:
            entity_type = EntityType.ALARM

        entity = Entity(
            entity_id=entity_id,
            entity_type=entity_type,
            name=raw_name,
            server_id=self._server_id() or "",
            zone=zone or None,
        )
        await self.store.upsert_entity(entity)

        level_note = f" · sismik kademe {level}" if level else ""
        await self._publish(
            Event(
                kind=EventKind.ENTITY_PAIRED,
                severity=Severity.INFO,
                title=f"Cihaz eslestirildi: {raw_name}",
                body=f"{entity_type}{level_note}",
                entity_id=entity_id,
                entity_name=raw_name,
                zone=zone or None,
                raw=notification.data,
            )
        )

        if self.socket is not None:
            await self.socket.track_entity(entity_id)

    async def _handle_alarm(self, notification: RustNotification) -> None:
        entity_id = notification.entity_id
        name = notification.entity_name or notification.title or "Alarm"
        zone, level, kind = await self._resolve_zone(entity_id, name)

        # Ham tetikleme kaydedilir ama bildirilmez - gurultuyu toplayici yonetir.
        await self._publish(
            Event(
                kind=EventKind.ALARM_TRIGGERED,
                severity=Severity.DEBUG,
                title=f"Alarm: {name}",
                body=notification.message,
                entity_id=entity_id,
                entity_name=name,
                zone=zone,
                raw=notification.data,
            )
        )

        await self.raid.feed(
            zone=zone,
            entity_name=name,
            entity_id=entity_id,
            seismic_level=level,
            sensor_kind=kind,
        )

    async def _handle_player_died(self, notification: RustNotification) -> None:
        await self._publish(
            Event(
                kind=EventKind.PLAYER_DIED,
                severity=Severity.WARN,
                title="Oyuncu oldu",
                body=notification.message or notification.title,
                raw=notification.data,
            )
        )

    async def _handle_player_login(self, notification: RustNotification) -> None:
        await self._publish(
            Event(
                kind=EventKind.PLAYER_LOGGED_IN,
                severity=Severity.DEBUG,
                title="Takim uyesi giris yapti",
                body=notification.message or notification.title,
                raw=notification.data,
            )
        )

    async def _handle_unknown(self, notification: RustNotification) -> None:
        await self._publish(
            Event(
                kind=EventKind.ALARM_TRIGGERED,
                severity=Severity.DEBUG,
                title="Taninmayan bildirim",
                body=notification.message or notification.title,
                raw=notification.raw,
            )
        )

    # --- entity abonelik olaylari -----------------------------------------

    async def _on_entity_change(self, entity_id: int, payload: Any) -> None:
        """Cihaz durumu degistiginde.

        Yalnizca yukselen kenar ilgilendiriyor: alarm sonduginde raid
        bitmis olmuyor, sadece sensor sifirlaniyor.
        """
        value = bool(getattr(payload, "value", False))
        server_id = self._server_id() or ""
        await self.store.update_entity_value(entity_id, server_id, value)

        if not value:
            return

        zone, level, kind = await self._resolve_zone(entity_id, "")
        name = await self._entity_name(entity_id)

        await self._publish(
            Event(
                kind=EventKind.ENTITY_CHANGED,
                severity=Severity.DEBUG,
                title=f"Cihaz tetiklendi: {name or entity_id}",
                entity_id=entity_id,
                entity_name=name,
                zone=zone,
                raw={"value": value},
            )
        )

        await self.raid.feed(
            zone=zone, entity_name=name, entity_id=entity_id,
            seismic_level=level, sensor_kind=kind,
        )

    async def _on_socket_status(self, up: bool, reason: str) -> None:
        await self._publish(
            Event(
                kind=EventKind.CONNECTION_UP if up else EventKind.CONNECTION_DOWN,
                severity=Severity.INFO if up else Severity.WARN,
                title="Rust+ baglantisi kuruldu" if up else "Rust+ baglantisi koptu",
                body=reason,
            )
        )

    # --- tehdit degerlendirmesi ve telefon zinciri ------------------------

    def _build_escalation(self) -> None:
        settings = self.settings
        self.spend = MonthlySpend(self.store, settings.monthly_call_budget_usd)

        caller = None
        if settings.twilio_account_sid and settings.twilio_auth_token:
            try:
                caller = TwilioCaller(
                    settings.twilio_account_sid,
                    settings.twilio_auth_token,
                    settings.twilio_from_number,
                    language=settings.twilio_language,
                    voice=settings.twilio_voice,
                )
            except ValueError as exc:
                log.error("Twilio yapilandirmasi gecersiz, arama kapali: %s", exc)

        # Takim listesi varsa o gecerli; yoksa eski .env dizesine dus.
        # Boylece mevcut kurulumlar bozulmadan yeni yapiya geciliyor.
        if self.team.callable_members:
            contacts = [
                Contact(name=m.name, phone=m.phone) for m in self.team.callable_members
            ]
        else:
            contacts = Contact.parse_list(settings.escalation_contacts)
        self.escalation = EscalationEngine(
            caller,
            contacts,
            self._publish,
            self.spend,
            cooldown_seconds=float(settings.call_cooldown_seconds),
        )

        if self.escalation.enabled:
            log.info(
                "Telefon zinciri hazir: %s (esik: %s, tavan: %s USD)",
                ", ".join(c.name for c in contacts),
                self._phone_threshold.name,
                settings.monthly_call_budget_usd,
            )
        else:
            log.warning("Telefon araması kapalı - Twilio ayarları veya kişi listesi eksik")

    def _detail_for(self, session: RaidSession) -> str:
        """Bildirim govdesine eklenecek ETA satiri.

        Us tanimi yoksa ya da olcum yetersizse bos doner - uydurma bir
        sayi vermektense hic vermemeyi tercih ediyoruz.
        """
        if self.base is None:
            return ""
        eta = estimate_eta(session, self.base)
        return eta.format() if eta is not None else ""

    def _context_for(self, session: RaidSession) -> dict[str, Any]:
        """Bildirim kanallarinin zengin gosterim icin kullandigi baglam."""
        assessment = assess(session)
        context: dict[str, Any] = {
            "threat": assessment.level.name,
            "threat_score": assessment.score,
            "threat_reasons": assessment.reasons,
            "estimated_sulfur": session.estimated_sulfur,
            "trigger_count": session.trigger_count,
            "summary": session.describe(),
        }
        if self.base is not None:
            eta = estimate_eta(session, self.base)
            if eta is not None:
                context["eta_seconds"] = round(eta.seconds)
                context["eta_low"] = round(eta.low_seconds)
                context["eta_high"] = round(eta.high_seconds)
                context["eta_confidence"] = str(eta.confidence)
                context["remaining_explosives"] = eta.remaining_explosives
                context["path"] = [
                    {"to": step.zone_to, "cost": step.cost, "label": step.label}
                    for step in eta.path
                ]
        return context

    async def _on_session_update(self, session: RaidSession) -> None:
        """Her tetiklemeden sonra: bu telefon caldirmayi hak ediyor mu?"""
        if self.escalation is None or not self.escalation.enabled:
            return

        assessment = assess(session)
        if assessment.level < self._phone_threshold:
            return

        # Sessiz saatlerde cita yukselir: gece yalnizca YUKSEK tehdit
        # telefon caldirir, ORTA sabahi bekler.
        if (
            self._quiet_hours is not None
            and self._quiet_hours.contains()
            and assessment.level < ThreatLevel.HIGH
        ):
            log.info(
                "Sessiz saat: %s tehdidi telefon caldirmiyor (%s)",
                assessment.level.name,
                session.zone,
            )
            return

        message = (
            f"Dikkat. {session.zone} bölgesi saldırı altında. {session.describe()}."
        )
        if self.base is not None:
            eta = estimate_eta(session, self.base)
            if eta is not None:
                minutes = max(1, int(round(eta.seconds / 60)))
                message += f" Tool cupboard'a tahminen {minutes} dakika."
        await self.escalation.escalate(
            session.zone, message, reason=assessment.explanation
        )

    # --- yardimcilar -------------------------------------------------------

    async def _connect_socket(self, server: PairedServer) -> None:
        if self.socket is not None:
            with contextlib.suppress(Exception):
                await self.socket.stop()

        self.socket = RustSocketSupervisor(
            server,
            on_entity_change=self._on_entity_change,
            on_status=self._on_socket_status,
            healthcheck_interval=float(self.settings.healthcheck_interval),
            use_proxy=self.settings.rust_use_proxy,
        )
        await self.socket.start()

        for entity in await self.store.entities(server.server_id):
            await self.socket.track_entity(entity.entity_id)

    def _resolve_server(self) -> PairedServer | None:
        if self.credentials.server is not None:
            return self.credentials.server

        # .env ile elle girilmis olabilir
        if self.settings.is_paired:
            return PairedServer(
                ip=self.settings.rust_server_ip,
                port=self.settings.rust_server_port,
                player_id=self.settings.rust_steam_id,
                player_token=self.settings.rust_player_token,
                name=self.settings.rust_server_label,
            )
        return None

    def server_id(self) -> str | None:
        """Eslesmis sunucu anahtari (yoksa None)."""
        return self._server_id()

    async def resolve_zone_for(
        self, entity: Entity
    ) -> tuple[str | None, int | None]:
        """Bir cihazin bolgesi ve sismik kademesi.

        Elle atanan kademe, cihaz adindan cikarilanin onune gecer.
        """
        zone_from_name, level_from_name = parse_entity_name(entity.name)
        zone = entity.zone or zone_from_name or None
        level = (
            entity.seismic_level
            if entity.seismic_level is not None
            else level_from_name
        )
        return (zone, level)

    def _server_id(self) -> str | None:
        if self.credentials.server is not None:
            return self.credentials.server.server_id
        return self.settings.server_id if self.settings.is_paired else None

    async def _resolve_zone(
        self, entity_id: int | None, fallback_name: str
    ) -> tuple[str | None, int | None, str]:
        """Cihaz kaydindan bolgeyi ve sismik kademeyi bulur.

        Panelden elle atanmis kademe, cihaz adindan cikarilanin onune gecer -
        adlandirma kuralina uymayan cihazlar icin kacis yolu.
        """
        entities = await self.store.entities(self._server_id())

        match = None
        if entity_id is not None:
            match = next((e for e in entities if e.entity_id == entity_id), None)

        if match is None and fallback_name:
            # Alarm bildirimi her zaman entityId tasimiyor; adla da esle.
            # Eslesemezsek panelden atanan bolge ve kademe sessizce
            # kaybolur ve ETA hesaplanamaz.
            wanted = fallback_name.strip().casefold()
            match = next((e for e in entities if e.name.strip().casefold() == wanted), None)

        if match is None and len(entities) == 1:
            # Tek eslesmis cihaz varsa alarm ondan gelmis olmak zorunda.
            # Alarm bildirimleri her zaman entityId tasimiyor ve tasidiklari
            # ad cihazin adi degil, oyunda girilen alarm mesaji olabiliyor -
            # bu durumda panelden atanan bolge ve kademe bosa giderdi.
            match = entities[0]
            log.debug("Alarm tek kayitli cihaza atfedildi: %s", match.name)

        if match is not None:
            level = match.seismic_level or parse_entity_name(match.name)[1]
            return (match.zone, level, str(match.sensor_kind))

        if fallback_name:
            zone, level = parse_entity_name(fallback_name)
            return (zone or None, level, str(SensorKind.UNKNOWN))
        return (None, None, str(SensorKind.UNKNOWN))

    async def _entity_name(self, entity_id: int) -> str:
        for entity in await self.store.entities(self._server_id()):
            if entity.entity_id == entity_id:
                return entity.name
        return ""

    def _startup_summary(self) -> str:
        channels = ", ".join(n.name for n in self.router.notifiers) or "yok"
        server = self._server_id() or "eslesmemis"
        return f"Sunucu: {server} · Kanallar: {channels}"

    # --- saglik ------------------------------------------------------------

    def raid_snapshot(self) -> list[dict[str, Any]]:
        """Aktif raidler, tehdit ve ETA ile birlikte - panelin ana verisi."""
        rows: list[dict[str, Any]] = []
        for session in self.raid.active_sessions:
            assessment = assess(session)
            row: dict[str, Any] = {
                "zone": session.zone,
                "started_at": session.started_at,
                "last_trigger_at": session.last_trigger_at,
                "trigger_count": session.trigger_count,
                "explosive_triggers": session.explosive_triggers,
                "levels": dict(session.levels),
                "estimated_sulfur": session.estimated_sulfur,
                "summary": session.describe(),
                "entities": sorted(session.entities),
                "threat": assessment.level.name,
                "threat_score": assessment.score,
                "threat_reasons": assessment.reasons,
                "eta": None,
            }

            if self.base is not None:
                eta = estimate_eta(session, self.base)
                if eta is not None:
                    row["eta"] = {
                        # Panel geri sayimi kendisi isletsin diye mutlak an
                        "due_at": time.time() + eta.seconds,
                        "seconds": round(eta.seconds),
                        "low_seconds": round(eta.low_seconds),
                        "high_seconds": round(eta.high_seconds),
                        "remaining_explosives": eta.remaining_explosives,
                        "confidence": str(eta.confidence),
                        "path": [
                            {"from": s.zone_from, "to": s.zone_to,
                             "cost": s.cost, "label": s.label}
                            for s in eta.path
                        ],
                    }
            rows.append(row)
        return rows

    async def health(self) -> dict[str, Any]:
        return {
            "running": self._running.is_set(),
            "uptime_seconds": round(time.time() - self.started_at, 1)
            if self.started_at
            else None,
            "server": self._server_id(),
            "fcm": self.fcm.health() if self.fcm else None,
            "socket": self.socket.health() if self.socket else None,
            "active_raids": self.raid_snapshot(),
            "channels": [n.name for n in self.router.notifiers],
            "escalation": {
                "enabled": bool(self.escalation and self.escalation.enabled),
                "phone_min_threat": self._phone_threshold.name,
                "quiet_hours_active": bool(
                    self._quiet_hours and self._quiet_hours.contains()
                ),
            },
            "spend": await self.spend.summary() if self.spend else None,
        }


def _parse_threat(value: str) -> ThreatLevel:
    try:
        return ThreatLevel[value.strip().upper()]
    except KeyError:
        log.error(
            "PHONE_MIN_THREAT gecersiz: %r - HIGH varsayildi (low|medium|high)", value
        )
        return ThreatLevel.HIGH


async def _noop() -> None:
    return None
