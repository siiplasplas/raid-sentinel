"""Bildirim kanallari."""

from sentinel.models import Event, EventKind, Severity
from sentinel.notify.base import Notifier
from sentinel.notify.discord import DiscordNotifier
from sentinel.notify.ntfy import NtfyNotifier
from sentinel.notify.router import NotificationRouter

__all__ = [
    "DiscordNotifier",
    "NotificationRouter",
    "Notifier",
    "NtfyNotifier",
    "sample_raid_event",
]

# Deneme bildirimi, GERCEK bir alarmin tasidigi baglami birebir tasir.
# Aksi halde "Bildirimleri dene" bos bir kabuk gonderir ve kullanici
# gercek bildirimin nasil gorunecegini goremez.
def sample_raid_event() -> Event:
    return Event(
        kind=EventKind.RAID_STARTED,
        severity=Severity.CRITICAL,
        title="Deneme: Garaj saldırı altında",
        body="Bu bir testtir. Gercek bir raid degil.",
        zone="Garaj",
        entity_name="Garaj S3",
        raw={
            "summary": "3 tetikleme · en agir: C4/roket · 79 sn süredir",
            "threat": "HIGH",
            "threat_score": 70,
            "threat_reasons": ["C4/roket kademesinde patlama", "3 tetikleme"],
            "trigger_count": 3,
            "estimated_sulfur": 5400,
            "eta_seconds": 64,
            "eta_low": 45,
            "eta_high": 92,
            "eta_confidence": "orta",
            "remaining_explosives": 2,
            "path": [
                {"to": "Airlock", "cost": 4, "label": "1x metal"},
                {"to": "TC", "cost": 5, "label": "1x metal + 1x sheet_metal_door"},
            ],
        },
    )



def build_router_from_settings(settings) -> NotificationRouter:  # noqa: ANN001
    """Ayarlarda tanimli kanallari kurar. Tanimsiz kanal sessizce atlanir."""
    import logging

    log = logging.getLogger(__name__)
    router = NotificationRouter()

    if settings.discord_webhook_url:
        router.add(
            DiscordNotifier(settings.discord_webhook_url, settings.discord_min_severity)
        )
    else:
        log.warning("DISCORD_WEBHOOK_URL bos - Discord bildirimleri kapali")

    if settings.ntfy_topic:
        router.add(
            NtfyNotifier(
                settings.ntfy_url,
                settings.ntfy_topic,
                settings.ntfy_min_severity,
                token=settings.ntfy_token,
            )
        )
    else:
        log.warning("NTFY_TOPIC bos - ntfy bildirimleri kapali")

    if not router.notifiers:
        log.error("Hicbir bildirim kanali yapilandirilmadi - sistem sessiz calisacak")

    return router
