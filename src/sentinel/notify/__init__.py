"""Bildirim kanallari."""

from sentinel.notify.base import Notifier
from sentinel.notify.discord import DiscordNotifier
from sentinel.notify.ntfy import NtfyNotifier
from sentinel.notify.router import NotificationRouter

__all__ = ["DiscordNotifier", "NotificationRouter", "Notifier", "NtfyNotifier"]


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
