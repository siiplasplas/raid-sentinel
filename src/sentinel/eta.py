"""TC'ye ne kadar kaldi?

Uc girdiden cikiyor:
    tip  - sismik termometrenin verdigi patlayici kademesi
    hiz  - son tetiklemeler arasindaki sure
    yol  - us grafindan TC'ye en ucuz yolun kalan maliyeti

    ETA = kalan_patlayici x tetikleme_araligi + bolge_gecis_suresi

Dogruluk konusunda durust olmak gerekirse: "yetkin bir raid ekibi duvar
basina kac saniye harcar" sorusunun guvenilir bir topluluk konsensusu yok.
Bu yuzden hiz sabit bir katsayi degil, o anki raidden **olculuyor**. Ilk
birkac patlamada tahmin kaba olur ve guven "dusuk" isaretlenir; olcum
biriktikce daralir.

Belirsizlik gizlenmiyor, bant olarak veriliyor: 3. kademe hem C4 hem roket
olabilir ve ikisinin duvar basina adedi farkli, o fark bandin icinde.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from enum import StrEnum

from sentinel.base_model import BaseGraph, PathStep
from sentinel.i18n import t
from sentinel.raid import RaidSession
from sentinel.raiddata import SEISMIC_TO_WEAPONS, SeismicLevel, WeaponClass

log = logging.getLogger(__name__)

# Bir bolgeden digerine gecis: yikilan duvardan girme, yeniden konumlanma.
# Kalibre edilecek bir varsayim - gercek olculerle degistirilmeli.
DEFAULT_TRANSIT_SECONDS = 25.0

# Hiz tahmini icin kullanilacak son olcum sayisi
_RECENT_WINDOW = 10

# Cok kisa araliklar (ayni patlamanin coklu tetiklemesi) hizi sisirir
_MIN_GAP_SECONDS = 2.0


class Confidence(StrEnum):
    """Deger dilden bagimsiz bir tanimlayici; gosterilirken cevriliyor."""

    LOW = "low"
    MEDIUM = "medium"
    GOOD = "good"

    @property
    def label(self) -> str:
        return t(f"conf.{self.value}")


@dataclass(slots=True)
class EtaEstimate:
    seconds: float
    low_seconds: float
    high_seconds: float
    remaining_explosives: int
    weapons: tuple[WeaponClass, ...]
    seconds_per_explosive: float
    confidence: Confidence
    path: list[PathStep] = field(default_factory=list)

    @property
    def target_zone(self) -> str:
        return self.path[-1].zone_to if self.path else ""

    def format(self) -> str:
        """Bildirimde gorunecek tek satir."""
        return t(
            "eta.line",
            eta=_mmss(self.seconds),
            band=_mmss(max(0.0, self.high_seconds - self.seconds)),
            conf=self.confidence.label,
            n=self.remaining_explosives,
        )


def _mmss(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60}:{total % 60:02d}"


def _measure_pace(session: RaidSession) -> tuple[float, float, Confidence] | None:
    """Tetikleme basina saniye, sapma ve guven.

    Ayni patlamanin birden fazla sensoru tetiklemesi cok kisa araliklar
    uretir ve hizi yapay olarak sisirir; onlari eliyoruz.
    """
    gaps = [g for g in session.intervals[-_RECENT_WINDOW:] if g >= _MIN_GAP_SECONDS]

    if len(gaps) >= 2:
        mean_gap = statistics.mean(gaps)
        deviation = statistics.stdev(gaps)
        confidence = Confidence.GOOD if len(gaps) >= 5 else Confidence.MEDIUM
        return (mean_gap, deviation, confidence)

    # Yeterli olcum yok: oturum ortalamasina dus, genis bant ver.
    if session.trigger_count >= 2 and session.duration > 0:
        mean_gap = session.duration / (session.trigger_count - 1)
        if mean_gap < _MIN_GAP_SECONDS:
            return None
        return (mean_gap, mean_gap * 0.6, Confidence.LOW)

    return None


def estimate(
    session: RaidSession,
    graph: BaseGraph,
    *,
    transit_seconds: float = DEFAULT_TRANSIT_SECONDS,
) -> EtaEstimate | None:
    """Oturum ve us grafindan ETA uretir.

    None doner: bolge tanimda yok, patlayici kademesi bilinmiyor ya da
    hiz olcecek kadar veri yok. Uydurulmus bir sayi vermektense
    "bilmiyorum" demeyi tercih ediyoruz.
    """
    level = session.heaviest_level
    if level is None:
        return None

    steps = graph.path_to_target(session.zone)
    if steps is None:
        log.debug("Bölge üs tanımında yok, ETA hesaplanmadı: %s", session.zone)
        return None
    if not steps:
        return None  # zaten TC'de

    pace = _measure_pace(session)
    if pace is None:
        return None
    mean_gap, deviation, confidence = pace

    weapons = SEISMIC_TO_WEAPONS[SeismicLevel(level)]
    costs = [
        cost
        for cost in (graph.remaining_cost(session.zone, weapon) for weapon in weapons)
        if cost is not None
    ]
    if not costs:
        return None

    spent = _spent_on_current_edge(session, graph)
    low_cost = max(0, min(costs) - spent)
    high_cost = max(0, max(costs) - spent)
    mid_cost = (low_cost + high_cost) / 2

    transit_total = transit_seconds * len(steps)
    fast_gap = max(_MIN_GAP_SECONDS, mean_gap - deviation)
    slow_gap = mean_gap + deviation

    seconds = mid_cost * mean_gap + transit_total
    low = low_cost * fast_gap + transit_total
    high = high_cost * slow_gap + transit_total

    return EtaEstimate(
        seconds=seconds,
        low_seconds=min(low, seconds),
        high_seconds=max(high, seconds),
        remaining_explosives=int(round(mid_cost)),
        weapons=weapons,
        seconds_per_explosive=mean_gap,
        confidence=confidence,
        path=steps,
    )


def _spent_on_current_edge(session: RaidSession, graph: BaseGraph) -> int:
    """Su anki engele kac patlayici yedirdiler.

    Sensor bolgede patlama gordugu icin, harcanan patlayicilarin o bolgeden
    cikan engele gittigini varsayiyoruz. Ilk adimin maliyetiyle sinirli -
    fazlasi bir sonraki engele sayilmaz, cunku gectiklerinde yeni bolgenin
    sensoru zaten tetiklenecek.
    """
    steps = graph.path_to_target(session.zone)
    if not steps:
        return 0
    return min(session.explosive_triggers, steps[0].cost)
