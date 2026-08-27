"""Discord webhook surucusu."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

import httpx

from sentinel.models import Event, Severity
from sentinel.notify.base import Notifier

log = logging.getLogger(__name__)

# Cubuk renkleri siddet seviyesini bir bakista okutur.
_COLORS: dict[Severity, int] = {
    Severity.DEBUG: 0x6B7280,
    Severity.INFO: 0x33646A,
    Severity.WARN: 0xD7A81C,
    Severity.CRITICAL: 0x9E3A24,
}

_MAX_RETRIES = 3


class DiscordNotifier(Notifier):
    name = "discord"

    def __init__(
        self,
        webhook_url: str,
        min_severity: Severity = Severity.INFO,
        *,
        timeout: float = 10.0,
    ) -> None:
        super().__init__(min_severity)
        if not webhook_url:
            raise ValueError("Discord webhook adresi bos olamaz")
        self._url = webhook_url
        self._client = httpx.AsyncClient(timeout=timeout)

    async def send(self, event: Event) -> None:
        payload = {"embeds": [self._embed(event)]}

        for attempt in range(_MAX_RETRIES):
            response = await self._client.post(self._url, json=payload)

            if response.status_code == 429:
                # Discord kendi bekleme suresini soyler; ona uy.
                retry_after = _retry_after(response, default=1.0 * (attempt + 1))
                log.warning("Discord hiz siniri, %.1f sn bekleniyor", retry_after)
                await asyncio.sleep(retry_after)
                continue

            response.raise_for_status()
            return

        raise RuntimeError(f"Discord {_MAX_RETRIES} denemede hiz siniri asilamadi")

    def _embed(self, event: Event) -> dict[str, object]:
        fields = []
        if event.zone:
            fields.append({"name": "Bolge", "value": event.zone, "inline": True})
        if event.entity_name:
            fields.append({"name": "Cihaz", "value": event.entity_name, "inline": True})

        embed: dict[str, object] = {
            "title": event.title[:256],
            "color": _COLORS.get(event.severity, _COLORS[Severity.INFO]),
            "timestamp": dt.datetime.fromtimestamp(event.ts, dt.UTC).isoformat(),
            "footer": {"text": f"{event.kind} · {event.severity.name.lower()}"},
        }
        if event.body:
            embed["description"] = event.body[:4096]
        if fields:
            embed["fields"] = fields
        return embed

    async def aclose(self) -> None:
        await self._client.aclose()


def _retry_after(response: httpx.Response, *, default: float) -> float:
    try:
        data = response.json()
        value = data.get("retry_after")
        if value is not None:
            # Discord bazen saniye, bazen milisaniye doner; buyuk deger ms demektir.
            seconds = float(value)
            return seconds / 1000 if seconds > 100 else seconds
    except (ValueError, AttributeError, TypeError):
        pass

    header = response.headers.get("Retry-After")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    return default
