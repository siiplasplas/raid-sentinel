"""Dil katmani.

Onemli olan cevirinin guzelligi degil, **eksik cevirinin sistemi durdurmamasi**.
Bir alarm sistemi, ceviri anahtari eksik diye KeyError ile olmemeli.
"""

from __future__ import annotations

import time
from collections import Counter

import pytest

from sentinel import i18n
from sentinel.eta import Confidence
from sentinel.raid import RaidSession
from sentinel.raiddata import SeismicLevel
from sentinel.scoring import assess


@pytest.fixture(autouse=True)
def _restore_language():
    before = i18n.language()
    yield
    i18n.set_language(before)


def _session(now: float) -> RaidSession:
    return RaidSession(
        zone="Garage", started_at=now - 200, last_trigger_at=now,
        trigger_count=6, levels=Counter({3: 6}),
        entities={"Garage S3", "Airlock S2"}, kinds={"explosion"},
    )


def test_unknown_language_falls_back_instead_of_raising():
    assert i18n.set_language("klingon") is i18n.DEFAULT


def test_missing_key_returns_the_key_not_an_exception():
    # Sistem calismaya devam etmeli; eksik anahtar gorunur olsun yeter.
    assert i18n.t("boyle.bir.anahtar.yok") == "boyle.bir.anahtar.yok"


def test_missing_parameter_does_not_crash():
    # Sablon parametresiz donsun - bildirim gitmemesindense eksik gitsin.
    assert "{zone}" in i18n.t("raid.started")


def test_every_key_exists_in_both_languages():
    for key, entry in i18n.CATALOG.items():
        assert set(entry) == {"tr", "en"}, f"{key} iki dilde de olmali"
        assert entry["tr"] and entry["en"], f"{key} bos ceviri"


def test_placeholders_match_across_languages():
    """Ayni anahtarin iki dili ayni parametreleri kullanmali.

    Yoksa dil degistirince metin sessizce yarim kalir.
    """
    import re
    for key, entry in i18n.CATALOG.items():
        fields = {lang: set(re.findall(r"\{(\w+)\}", text))
                  for lang, text in entry.items()}
        assert fields["tr"] == fields["en"], f"{key}: {fields}"


def test_session_summary_switches_language():
    now = time.time()
    session = _session(now)

    i18n.set_language("tr")
    turkish = session.describe()
    i18n.set_language("en")
    english = session.describe()

    assert "tetikleme" in turkish
    assert "triggers" in english
    assert turkish != english


def test_thousands_separator_follows_the_language():
    now = time.time()
    session = _session(now)

    i18n.set_language("tr")
    assert "10.800" in session.describe()
    i18n.set_language("en")
    assert "10,800" in session.describe()


def test_scoring_reasons_switch_language_without_changing_the_score():
    now = time.time()
    session = _session(now)

    i18n.set_language("tr")
    tr = assess(session, now=now)
    i18n.set_language("en")
    en = assess(session, now=now)

    # Dil bir sunum meselesi - puan ve tehdit seviyesi degismemeli.
    assert tr.score == en.score
    assert tr.level is en.level
    assert tr.explanation != en.explanation


def test_confidence_value_is_language_independent():
    """Deger API'de ve veritabaninda duruyor; sabit kalmali."""
    i18n.set_language("en")
    assert Confidence.LOW.value == "low"
    assert Confidence.LOW.label == "low"

    i18n.set_language("tr")
    assert Confidence.LOW.value == "low"
    assert Confidence.LOW.label == "düşük"


def test_seismic_level_names_translate():
    now = time.time()
    session = RaidSession(zone="Roof", started_at=now - 10, last_trigger_at=now,
                          trigger_count=1, levels=Counter({SeismicLevel.LIGHT: 1}),
                          entities={"Roof S1"}, kinds={"explosion"})
    i18n.set_language("en")
    assert "grenade/beancan" in session.describe()
