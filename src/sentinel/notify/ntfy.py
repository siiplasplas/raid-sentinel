"""ntfy surucusu - telefonda sessiz modu delen ucretsiz push.

Onemli: baslik ve mesaji HTTP basliklarina koymuyoruz. ntfy'nin baslik
tabanli API'si ASCII bekliyor ve Turkce karakterler bozuluyor. Bunun yerine
JSON yayinlama ucunu kullaniyoruz - UTF-8 sorunsuz gecer.
"""

from __future__ import annotations

import logging

import httpx

from sentinel.models import Event, Severity
from sentinel.notify.base import Notifier

log = logging.getLogger(__name__)

# ntfy oncelikleri: 1 min, 3 default, 4 high, 5 urgent (sessiz modu deler).
_PRIORITY: dict[Severity, int] = {
    Severity.DEBUG: 2,
    Severity.INFO: 3,
    Severity.WARN: 4,
    Severity.CRITICAL: 5,
}

_TAGS: dict[Severity, list[str]] = {
    Severity.DEBUG: ["mag"],
    Severity.INFO: ["information_source"],
    Severity.WARN: ["warning"],
    Severity.CRITICAL: ["rotating_light"],
}


class NtfyNotifier(Notifier):
    name = "ntfy"

    def __init__(
        self,
        base_url: str,
        topic: str,
        min_severity: Severity = Severity.WARN,
        *,
        token: str = "",
        timeout: float = 10.0,
    ) -> None:
        super().__init__(min_severity)
        if not topic:
            raise ValueError("ntfy konusu bos olamaz")
        self._base_url = base_url.rstrip("/")
        self._topic = topic

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers)

    async def send(self, event: Event) -> None:
        payload = {
            "topic": self._topic,
            "title": event.title[:200],
            "message": event.body or event.title,
            "priority": _PRIORITY.get(event.severity, 3),
            "tags": _TAGS.get(event.severity, []),
        }
        response = await self._client.post(self._base_url, json=payload)
        response.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()
