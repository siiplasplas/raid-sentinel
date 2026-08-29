"""Tehdit degerlendirmesi: bu gercek bir raid mi, yoksa yanindan gecen biri mi?

Bu katman iki isi birden yapiyor ve ikincisi kolay gozden kaciyor:

1. Sahte alarmi eler. Tek bir HBHF tetiklemesi gece 4'te telefon caldirmaz.
2. Fatura kontrolu. Telefon aramasi Turkiye mobiline dakikasi ~0,29 dolar.
   Her tetiklemede arama yapan bir sistem ayda bin lirayi bulur. Aramayi
   yalnizca YUKSEK tehditte acmak, maliyet tavaninin ilk savunma hatti.

Puanlama bilerek acik ve okunabilir tutuldu: her kural bir gerekce cumlesi
uretiyor ve bu gerekceler bildirimin icine giriyor. "Neden uyandirildim"
sorusunun cevabi her zaman elimizde olsun istiyoruz.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum

from sentinel.i18n import t
from sentinel.models import SensorKind, Severity
from sentinel.raid import RaidSession
from sentinel.raiddata import SeismicLevel


class ThreatLevel(IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3


# Esikler. Tek bir 3. kademe patlama (C4/roket) tek basina YUKSEK'e ulasir -
# ussune C4 yapistiran biri tanim geregi raid'dir, ikinci kanita gerek yok.
THRESHOLD_LOW = 15
THRESHOLD_MEDIUM = 35
THRESHOLD_HIGH = 60

# Sismik kademe puanlari
# Gerekce metinleri i18n anahtari; dil ayardan geliyor.
_LEVEL_SCORE: dict[SeismicLevel, tuple[int, str]] = {
    SeismicLevel.HEAVY: (60, "score.heavy"),
    SeismicLevel.MEDIUM: (40, "score.medium"),
    SeismicLevel.LIGHT: (15, "score.light"),
}

_BASELINE_SCORE = 10
_SUSTAINED_SECONDS = 90.0
_TEAMMATE_PENALTY = 30


@dataclass(slots=True)
class Assessment:
    level: ThreatLevel
    score: int
    reasons: list[str] = field(default_factory=list)

    @property
    def explanation(self) -> str:
        return "; ".join(self.reasons) if self.reasons else t("score.none")


def assess(
    session: RaidSession,
    *,
    now: float | None = None,
    teammate_nearby: bool = False,
) -> Assessment:
    """Bir raid oturumunu puanlar."""
    moment = now if now is not None else time.time()
    score = 0
    reasons: list[str] = []

    if session.trigger_count > 0:
        score += _BASELINE_SCORE

    level = session.heaviest_level
    if level is not None:
        points, reason_key = _LEVEL_SCORE[level]
        score += points
        # Gerekce, GORULEN seyi anlatmali. Hareket sensorune elle kademe
        # atanmis olabilir; o zaman "C4 patladi" demek yanlis olur.
        if session.only_presence:
            reasons.append(t("score.presence"))
        elif str(SensorKind.EXPLOSION) not in session.kinds:
            reasons.append(t("score.unverified", reason=t(reason_key)))
        else:
            reasons.append(t(reason_key))

    distinct = len(session.entities)
    if distinct >= 2:
        score += 25
        reasons.append(t("score.distinct", n=distinct))

    if session.trigger_count >= 3:
        score += 15
        reasons.append(t("desc.triggers", n=session.trigger_count))
    if session.trigger_count >= 10:
        score += 10

    elapsed = max(0.0, moment - session.started_at)
    if elapsed >= _SUSTAINED_SECONDS and session.trigger_count >= 2:
        score += 15
        reasons.append(t("score.sustained", n=f"{elapsed / 60:.0f}"))

    # Takim arkadasi cezasi YALNIZCA patlayici kaniti yokken uygulanir.
    # Ceza, HBHF/hareket sensorlerinin urettigi sahte alarmi elemek icin var
    # - eve gelen arkadas raid degildir. Ama sismik sensor patlama gormusse
    # o kanit tartisilmaz: kimse kendi ussunu C4'lemez. Cezayi orada da
    # uygulamak, gercek bir raidi ORTA'ya dusurup telefonu susturur.
    if teammate_nearby and level is None:
        score -= _TEAMMATE_PENALTY
        reasons.append(t("score.teammate"))

    score = max(0, score)
    return Assessment(level=_to_level(score), score=score, reasons=reasons)


# Tehdit seviyesi -> bildirim siddeti. Bu esleme, puanlamanin bildirim
# kanallarina baglandigi yer: NONE olay olarak kaydedilir ama kimseyi
# uyandirmaz, HIGH her kanali tetikler.
THREAT_SEVERITY: dict[ThreatLevel, Severity] = {
    ThreatLevel.NONE: Severity.DEBUG,
    ThreatLevel.LOW: Severity.INFO,
    ThreatLevel.MEDIUM: Severity.WARN,
    ThreatLevel.HIGH: Severity.CRITICAL,
}


def severity_for(session: RaidSession) -> Severity:
    """Bir oturumun su anki tehdit seviyesine karsilik gelen siddet."""
    return THREAT_SEVERITY[assess(session).level]


def _to_level(score: int) -> ThreatLevel:
    if score >= THRESHOLD_HIGH:
        return ThreatLevel.HIGH
    if score >= THRESHOLD_MEDIUM:
        return ThreatLevel.MEDIUM
    if score >= THRESHOLD_LOW:
        return ThreatLevel.LOW
    return ThreatLevel.NONE
