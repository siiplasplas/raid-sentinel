"""Discord webhook surucusu.

Raid olaylari zengin bir gomulu mesaj olarak gonderiliyor: tehdit olcegi,
tool cupboard'a geri sayim, kalan yol merdiveni ve alarmin neden verildigi.
Amac telefonda tek bakista karar verilebilmesi - "kalkip savunayim mi".

Gorsel icin dis kaynak kullanmiyoruz. Barindirilan bir resim, sunucu
kapaliyken ya da internet yokken kirik gorunurdu; unicode blok olcekler
her yerde ayni gorunuyor ve hicbir seye bagimli degil.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

import httpx

from sentinel.models import Event, EventKind, Severity
from sentinel.notify.base import Notifier

log = logging.getLogger(__name__)

# Cubuk renkleri siddet seviyesini bir bakista okutur.
_COLORS: dict[Severity, int] = {
    Severity.DEBUG: 0x6B7280,
    Severity.INFO: 0x33646A,
    Severity.WARN: 0xD7A81C,
    Severity.CRITICAL: 0x9E3A24,
}

_THREAT_LABELS = {
    "HIGH": "YÜKSEK",
    "MEDIUM": "ORTA",
    "LOW": "DÜŞÜK",
    "NONE": "belirsiz",
}

# Olay turune gore baslik isareti. Bildirim listesinde ne oldugunu
# okumadan ayirt edebilmek icin.
_KIND_MARKS = {
    EventKind.RAID_STARTED: "🔴",
    EventKind.RAID_PROGRESS: "🟠",
    EventKind.RAID_ENDED: "🟢",
    EventKind.ESCALATION_STARTED: "📞",
    EventKind.ESCALATION_ACKNOWLEDGED: "✅",
    EventKind.ESCALATION_EXHAUSTED: "❗",
    EventKind.CALL_BUDGET_EXCEEDED: "💸",
    EventKind.CONNECTION_DOWN: "⚠️",
    EventKind.CONNECTION_UP: "🔌",
    EventKind.SERVER_PAIRED: "🔗",
    EventKind.ENTITY_PAIRED: "📟",
    EventKind.SENTINEL_STARTED: "🛡️",
    EventKind.SENTINEL_STOPPING: "⚠️",
}

_MAX_RETRIES = 3
_METER_WIDTH = 12


def _meter(value: float, maximum: float = 100.0) -> str:
    """Unicode blok olcek. Puani sayidan once goze gosteriyor."""
    ratio = 0.0 if maximum <= 0 else max(0.0, min(1.0, value / maximum))
    filled = round(ratio * _METER_WIDTH)
    return "█" * filled + "░" * (_METER_WIDTH - filled)


def _mmss(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60}:{total % 60:02d}"


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
        payload: dict[str, Any] = {"embeds": [self._embed(event)]}

        # Etiketler gomulu mesajin icinde ping uretmiyor; icerik alanina
        # koymak gerekiyor.
        mentions = (event.raw or {}).get("mentions")
        if mentions:
            payload["content"] = str(mentions)[:2000]

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

    # --- gomulu mesaj -------------------------------------------------------

    def _embed(self, event: Event) -> dict[str, Any]:
        raw = event.raw or {}
        mark = _KIND_MARKS.get(event.kind, "•")

        title = f"{mark}  {event.title}"[:256]

        embed: dict[str, Any] = {
            "title": title,
            "color": _COLORS.get(event.severity, _COLORS[Severity.INFO]),
            "timestamp": dt.datetime.fromtimestamp(event.ts, dt.UTC).isoformat(),
            "author": {"name": "Raid Sentinel"},
            "footer": {"text": f"{event.kind} · {event.severity.name.lower()}"},
        }

        description = self._description(event, raw)
        if description:
            embed["description"] = description[:4096]

        fields = self._fields(event, raw)
        if fields:
            embed["fields"] = fields
        return embed

    def _description(self, event: Event, raw: dict[str, Any]) -> str:
        parts: list[str] = []

        summary = raw.get("summary")
        if summary:
            parts.append(f"**{summary}**")
        elif event.body:
            parts.append(event.body)

        threat = raw.get("threat")
        if threat:
            score = int(raw.get("threat_score") or 0)
            label = _THREAT_LABELS.get(str(threat), str(threat))
            parts.append(f"`{_meter(score)}`  **{label}** · {score} puan")

        # Ozet zaten govdeyi iceriyorsa tekrar etme
        if summary and event.body and event.body not in summary:
            extra = "\n".join(
                line for line in event.body.splitlines() if line and line not in summary
            )
            if extra:
                parts.append(extra)

        return "\n\n".join(parts)

    def _fields(self, event: Event, raw: dict[str, Any]) -> list[dict[str, Any]]:
        fields: list[dict[str, Any]] = []

        if event.zone:
            fields.append({"name": "Bölge", "value": event.zone, "inline": True})

        if raw.get("trigger_count"):
            fields.append(
                {"name": "Tetikleme", "value": str(raw["trigger_count"]), "inline": True}
            )

        if raw.get("estimated_sulfur"):
            sulfur = f"{int(raw['estimated_sulfur']):,}".replace(",", ".")
            fields.append(
                {"name": "Harcadıkları", "value": f"~{sulfur} sülfür", "inline": True}
            )

        eta = raw.get("eta_seconds")
        if eta is not None:
            value = f"**{_mmss(eta)}**"
            low, high = raw.get("eta_low"), raw.get("eta_high")
            if low is not None and high is not None:
                value += f"  ({_mmss(low)}–{_mmss(high)})"
            remaining = raw.get("remaining_explosives")
            if remaining is not None:
                value += f"\n{remaining} patlayıcı kaldı"
            confidence = raw.get("eta_confidence")
            if confidence:
                value += f" · güven: {confidence}"
            fields.append(
                {"name": "Tool cupboard'a", "value": value, "inline": False}
            )

        ladder = _path_ladder(raw.get("path"))
        if ladder:
            fields.append({"name": "Kalan yol", "value": ladder, "inline": False})

        reasons = raw.get("threat_reasons")
        if reasons:
            joined = "; ".join(str(r) for r in reasons)
            fields.append({"name": "Neden", "value": joined[:1024], "inline": False})

        if event.entity_name:
            fields.append({"name": "Cihaz", "value": event.entity_name, "inline": True})

        return fields

    async def aclose(self) -> None:
        await self._client.aclose()


def _path_ladder(path: Any) -> str:
    """Kalan bolgeleri kod blogunda merdiven olarak gosterir."""
    if not isinstance(path, list) or not path:
        return ""

    lines = []
    for index, step in enumerate(path):
        if not isinstance(step, dict):
            continue
        marker = "▶" if index == 0 else " "
        zone = str(step.get("to", "?"))
        cost = step.get("cost", "?")
        label = str(step.get("label", ""))
        lines.append(f"{marker} {zone:<14} {cost:>2} C4  {label}")

    if not lines:
        return ""
    return "```\n" + "\n".join(lines)[:1000] + "\n```"


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
