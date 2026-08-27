"""Aylik arama butcesi.

Bu opsiyonel bir konfor ozelligi degil, zorunlu bir emniyet supabi.
Turkiye mobiline dakikasi ~0,29 dolar; bozuk bir sensor ya da gevsemis bir
esik yuzunden donguye giren bir sistem, farkina varmadan aylik faturayi
bin liraya tasiyabilir.

Defter olay deposunun kv tablosunda tutuluyor: surec yeniden baslasa da
ayin harcamasi kaybolmaz.
"""

from __future__ import annotations

import datetime as dt
import logging

from sentinel.store import Store

log = logging.getLogger(__name__)

_KEY_PREFIX = "spend"


def _current_period(now: dt.datetime | None = None) -> str:
    moment = now or dt.datetime.now(dt.UTC)
    return f"{moment.year:04d}-{moment.month:02d}"


class MonthlySpend:
    def __init__(self, store: Store, cap_usd: float) -> None:
        self._store = store
        self.cap_usd = cap_usd

    def _key(self, period: str | None = None) -> str:
        return f"{_KEY_PREFIX}:{period or _current_period()}"

    async def total(self, period: str | None = None) -> float:
        value = await self._store.get_state(self._key(period), 0.0)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    async def remaining(self) -> float:
        if self.cap_usd <= 0:
            return float("inf")
        return max(0.0, self.cap_usd - await self.total())

    async def can_spend(self, estimate_usd: float) -> bool:
        """Tavan yoksa (0 veya negatif) sinirsiz kabul edilir."""
        if self.cap_usd <= 0:
            return True
        return (await self.total()) + estimate_usd <= self.cap_usd

    async def record(self, amount_usd: float) -> float:
        if amount_usd <= 0:
            return await self.total()

        new_total = await self.total() + amount_usd
        await self._store.set_state(self._key(), round(new_total, 4))

        if self.cap_usd > 0 and new_total >= self.cap_usd:
            log.warning(
                "Aylik arama butcesi doldu: %.2f / %.2f USD", new_total, self.cap_usd
            )
        return new_total

    async def summary(self) -> dict[str, float | str]:
        total = await self.total()
        return {
            "period": _current_period(),
            "spent_usd": round(total, 4),
            "cap_usd": self.cap_usd,
            "remaining_usd": round(await self.remaining(), 4)
            if self.cap_usd > 0
            else float("inf"),
        }
