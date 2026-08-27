"""Panelden yapilan ayar degisiklikleri.

Iki sey burada korunuyor:
  - Sirlar panele acik gonderilmemeli.
  - Panel formu her kaydetmede butun alanlari gonderiyor; degismeyenler
    override olarak yazilmamali, yoksa .env'i sonradan degistirmek sessizce
    etkisiz kalir.
"""

from __future__ import annotations

import pytest

from sentinel.config import Settings
from sentinel.settings_store import (
    SettingsError,
    apply_updates,
    describe,
    load_overrides,
    reapply,
)


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=tmp_path / "s.db")


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path


# --- dogrulama -------------------------------------------------------------


def test_unknown_key_is_rejected(data_dir, settings):
    with pytest.raises(SettingsError, match="Bilinmeyen ayar"):
        apply_updates(data_dir, {"db_path": "/etc/passwd"}, settings)


def test_non_numeric_budget_is_rejected(data_dir, settings):
    with pytest.raises(SettingsError, match="sayi olmali"):
        apply_updates(data_dir, {"monthly_call_budget_usd": "bes dolar"}, settings)


def test_invalid_threat_level_is_rejected(data_dir, settings):
    with pytest.raises(SettingsError, match="gecerli degerler"):
        apply_updates(data_dir, {"phone_min_threat": "cok-yuksek"}, settings)


def test_negative_budget_is_rejected(data_dir, settings):
    with pytest.raises(SettingsError, match="negatif"):
        apply_updates(data_dir, {"monthly_call_budget_usd": "-5"}, settings)


def test_too_short_healthcheck_is_rejected(data_dir, settings):
    with pytest.raises(SettingsError, match="en az 60"):
        apply_updates(data_dir, {"healthcheck_interval": "10"}, settings)


# --- degisiklik tespiti ----------------------------------------------------


def test_unchanged_fields_are_not_written(data_dir, settings):
    """Form her seyi gonderir; degismeyenler override dosyasina girmemeli."""
    form = {
        "ntfy_url": settings.ntfy_url,
        "monthly_call_budget_usd": str(settings.monthly_call_budget_usd),
        "discord_min_severity": settings.discord_min_severity.name.lower(),
        "ntfy_topic": "yeni-konu",
    }
    _, changed = apply_updates(data_dir, form, settings)

    assert changed == ["ntfy_topic"]
    assert load_overrides(data_dir) == {"ntfy_topic": "yeni-konu"}


def test_reverting_to_default_removes_the_override(data_dir, settings):
    apply_updates(data_dir, {"monthly_call_budget_usd": "12.5"}, settings)
    assert "monthly_call_budget_usd" in load_overrides(data_dir)

    _, changed = apply_updates(
        data_dir, {"monthly_call_budget_usd": str(settings.monthly_call_budget_usd)},
        settings,
    )

    assert changed == [], "Davranis degismedigi icin degisiklik sayilmamali"
    assert "monthly_call_budget_usd" not in load_overrides(data_dir)


# --- sirlar ----------------------------------------------------------------


def test_secrets_are_never_exposed(tmp_path):
    settings = Settings(
        db_path=tmp_path / "s.db",
        discord_webhook_url="https://discord.com/api/webhooks/1/supersecrettoken",
    )
    field = next(f for f in describe(settings) if f["key"] == "discord_webhook_url")

    assert "value" not in field
    assert field["set"] is True
    assert "supersecrettoken" not in str(field)


def test_blank_secret_keeps_existing_value(data_dir, settings):
    apply_updates(data_dir, {"ntfy_token": "gizli-token"}, settings)
    # Uygulamada oldugu gibi: kaydettikten sonra ayarlar yeniden yuklenir
    live = reapply(settings, data_dir)

    _, changed = apply_updates(data_dir, {"ntfy_token": ""}, live)

    assert changed == []
    assert load_overrides(data_dir)["ntfy_token"] == "gizli-token"


def test_dash_clears_a_secret(data_dir, settings):
    apply_updates(data_dir, {"ntfy_token": "gizli-token"}, settings)
    live = reapply(settings, data_dir)
    assert live.ntfy_token == "gizli-token"

    _, changed = apply_updates(data_dir, {"ntfy_token": "-"}, live)

    assert changed == ["ntfy_token"]
    assert reapply(settings, data_dir).ntfy_token == ""


# --- uygulama --------------------------------------------------------------


def test_reapply_layers_overrides_on_current_settings(data_dir, settings):
    """Yeniden yukleme .env'den bastan kurmamali - db_path gibi degerler
    acikca verilmis olabilir ve kaybolmamali."""
    apply_updates(data_dir, {"ntfy_topic": "panelden"}, settings)

    updated = reapply(settings, data_dir)

    assert updated.ntfy_topic == "panelden"
    assert updated.db_path == settings.db_path


def test_reapply_without_overrides_returns_same_settings(data_dir, settings):
    assert reapply(settings, data_dir) is settings


def test_corrupt_override_file_is_ignored(data_dir, settings):
    (data_dir / "settings.json").write_text("{bu json degil", encoding="utf-8")

    assert load_overrides(data_dir) == {}
    assert reapply(settings, data_dir) is settings


def test_stale_keys_in_file_are_dropped(data_dir, settings):
    (data_dir / "settings.json").write_text(
        '{"ntfy_topic": "gecerli", "eski_ayar": "x"}', encoding="utf-8"
    )

    assert load_overrides(data_dir) == {"ntfy_topic": "gecerli"}
