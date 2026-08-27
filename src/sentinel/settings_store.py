"""Panelden degistirilebilen ayarlar.

Katman sirasi:  kod varsayilanlari  <  .env  <  data/settings.json

.env dosyasi ilk kurulum icin duruyor; panelden yapilan degisiklikler
`settings.json` icine yaziliyor ve .env'in uzerine biniyor. Boylece
kullanici dosya duzenlemek zorunda kalmiyor ama mevcut kurulumlar da
bozulmuyor.

Sirlar (token, webhook adresi) panele **asla acik gonderilmiyor**;
yalnizca "tanimli mi" ve son birkac karakteri donuyor. Yeni deger
gonderilmezse eskisi korunuyor.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sentinel.config import Settings
from sentinel.models import Severity

log = logging.getLogger(__name__)

SETTINGS_FILENAME = "settings.json"

FieldType = Literal["text", "secret", "int", "float", "severity", "threat", "bool"]


@dataclass(slots=True)
class FieldSpec:
    key: str
    label: str
    group: str
    type: FieldType = "text"
    help: str = ""
    placeholder: str = ""
    choices: tuple[str, ...] = field(default_factory=tuple)


# Panelde gorunecek alanlar. Buraya eklenmeyen hicbir ayar disaridan
# degistirilemez - yanlislikla db_path gibi bir seyin ezilmesini istemiyoruz.
SETTINGS_SPEC: tuple[FieldSpec, ...] = (
    # --- Discord ---
    FieldSpec("discord_webhook_url", "Discord webhook adresi", "Bildirimler",
              "secret", "Kanal ayarlari > Entegrasyonlar > Webhook olustur"),
    FieldSpec("discord_min_severity", "Discord esigi", "Bildirimler", "severity",
              "Bu seviyeden itibaren Discord'a gider"),

    # --- ntfy ---
    FieldSpec("ntfy_url", "ntfy sunucusu", "Bildirimler", "text",
              placeholder="https://ntfy.sh"),
    FieldSpec("ntfy_topic", "ntfy konusu", "Bildirimler", "secret",
              "Tahmin edilemez bir ad sec - konuyu bilen herkes mesaj gonderebilir"),
    FieldSpec("ntfy_token", "ntfy token (opsiyonel)", "Bildirimler", "secret"),
    FieldSpec("ntfy_min_severity", "ntfy esigi", "Bildirimler", "severity",
              "Kritik seviyede telefonun sessiz modunu deler"),

    # --- Telefon ---
    FieldSpec("twilio_account_sid", "Twilio Account SID", "Telefon", "secret"),
    FieldSpec("twilio_auth_token", "Twilio Auth Token", "Telefon", "secret"),
    FieldSpec("twilio_from_number", "Arayan numara", "Telefon", "text",
              placeholder="+15550000000"),
    FieldSpec("escalation_contacts", "Aranacak kisiler", "Telefon", "text",
              "Sirayla aranir, ilk cevap veren zinciri durdurur",
              "Halit:+905000000000,Ahmet:+905000000001"),
    FieldSpec("twilio_language", "Seslendirme dili", "Telefon", "text",
              "Twilio'nun ses tablosunda tr-TR gorunmuyor - calismazsa en-US dene",
              "tr-TR"),
    FieldSpec("twilio_voice", "Ses", "Telefon", "text",
              "Bos birakirsan dilin varsayilan sesi kullanilir", "Polly.Filiz"),

    # --- Esikler ve limitler ---
    FieldSpec("phone_min_threat", "Telefon icin en dusuk tehdit", "Limitler", "threat",
              "YUKSEK = tek bir C4/roket patlamasi"),
    FieldSpec("monthly_call_budget_usd", "Aylik arama tavani (USD)", "Limitler", "float",
              "Turkiye mobiline dakikasi ~0,29 USD. 0 = sinirsiz, onerilmez"),
    FieldSpec("call_cooldown_seconds", "Bolge bekleme suresi (sn)", "Limitler", "int",
              "Ayni bolge icin iki telefon zinciri arasindaki en az sure"),
    FieldSpec("quiet_hours", "Sessiz saatler", "Limitler", "text",
              "Gece yalnizca YUKSEK tehdit telefon caldirir. Bos = kapali",
              "23:00-08:00"),
    FieldSpec("healthcheck_interval", "Saglik kontrolu araligi (sn)", "Limitler", "int",
              "Rust+ baglantisinin gercekten yasadigini dogrulama sikligi"),

    # --- Baglanti ---
    FieldSpec("rust_use_proxy", "Facepunch vekili uzerinden baglan", "Baglanti", "bool",
              "Normalde oyun sunucusuna dogrudan baglaniriz. Sunucunun app portu "
              "disariya kapaliysa baglanti kurulamaz; bunu acmak Facepunch'in "
              "kendi vekili uzerinden gitmeyi dener. 1 veya 0 yaz."),
)

_SPEC_BY_KEY = {spec.key: spec for spec in SETTINGS_SPEC}

_SEVERITY_CHOICES = tuple(s.name.lower() for s in Severity)
_THREAT_CHOICES = ("low", "medium", "high")


class SettingsError(ValueError):
    """Gonderilen ayar gecersiz."""


def settings_path(data_dir: Path) -> Path:
    return data_dir / SETTINGS_FILENAME


def load_overrides(data_dir: Path) -> dict[str, Any]:
    path = settings_path(data_dir)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Ayar dosyasi okunamadi, yok sayiliyor (%s): %s", path, exc)
        return {}

    if not isinstance(data, dict):
        log.error("Ayar dosyasi sozluk degil, yok sayiliyor: %s", path)
        return {}

    # Bilinmeyen anahtarlari sessizce dus - eski surumden kalmis olabilir.
    return {k: v for k, v in data.items() if k in _SPEC_BY_KEY}


def save_overrides(data_dir: Path, overrides: dict[str, Any]) -> Path:
    path = settings_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(overrides, handle, indent=2, ensure_ascii=False, sort_keys=True)
    temp.replace(path)
    return path


def build_settings() -> Settings:
    """Ayarlari .env + panel degisiklikleriyle birlikte kurar.

    Once db_path'i ogrenmek icin taban ayarlar okunur, sonra o dizindeki
    override dosyasi uygulanir. pydantic-settings'te init parametreleri
    ortam degiskenlerinin onunde gelir.
    """
    base = Settings()
    return reapply(base, base.db_path.parent)


def reapply(settings: Settings, data_dir: Path) -> Settings:
    """Mevcut ayarlarin uzerine o dizindeki override dosyasini uygular.

    `build_settings()` gibi .env'den bastan kurmuyor: boylece calisirken
    yeniden yukleme, uygulamaya acikca verilmis db_path gibi degerleri
    kaybetmiyor ve override dosyasini dogru dizinde ariyor.
    """
    overrides = load_overrides(data_dir)
    if not overrides:
        return settings
    try:
        return Settings(**{**settings.model_dump(), **overrides})
    except Exception as exc:  # noqa: BLE001 - bozuk override sistemi durdurmasin
        log.error("Panel ayarlari uygulanamadi, onceki degerler korunuyor: %s", exc)
        return settings


# --- dogrulama -------------------------------------------------------------


def _coerce(spec: FieldSpec, value: Any) -> Any:
    if value is None:
        return None
    text = str(value).strip()

    if spec.type in ("text", "secret"):
        return text
    if spec.type == "int":
        try:
            return int(float(text))
        except ValueError as exc:
            raise SettingsError(f"{spec.label}: sayi olmali") from exc
    if spec.type == "float":
        try:
            return float(text.replace(",", "."))
        except ValueError as exc:
            raise SettingsError(f"{spec.label}: sayi olmali") from exc
    if spec.type == "bool":
        return text.lower() in ("1", "true", "on", "yes", "evet")
    if spec.type == "severity":
        if text.lower() not in _SEVERITY_CHOICES:
            raise SettingsError(
                f"{spec.label}: gecerli degerler {', '.join(_SEVERITY_CHOICES)}"
            )
        return text.lower()
    if spec.type == "threat":
        if text.lower() not in _THREAT_CHOICES:
            raise SettingsError(
                f"{spec.label}: gecerli degerler {', '.join(_THREAT_CHOICES)}"
            )
        return text.lower()
    return text


def _check_ranges(key: str, value: Any) -> None:
    if key == "monthly_call_budget_usd" and value < 0:
        raise SettingsError("Aylik arama tavani negatif olamaz")
    if key == "call_cooldown_seconds" and value < 0:
        raise SettingsError("Bekleme suresi negatif olamaz")
    if key == "healthcheck_interval" and value < 60:
        raise SettingsError("Saglik kontrolu araligi en az 60 saniye olmali")


def _effective(spec: FieldSpec, settings: Settings) -> Any:
    """Ayarin su anki gecerli degeri, gelen veriyle ayni tipe normalize."""
    raw = getattr(settings, spec.key, None)
    if raw is None:
        return "" if spec.type in ("text", "secret") else None
    if spec.type == "severity":
        return raw.name.lower() if isinstance(raw, Severity) else str(raw).lower()
    if spec.type == "threat":
        return str(raw).lower()
    if spec.type == "int":
        return int(raw)
    if spec.type == "float":
        return float(raw)
    if spec.type == "bool":
        return bool(raw)
    return str(raw)


def apply_updates(
    data_dir: Path, updates: dict[str, Any], settings: Settings
) -> tuple[dict[str, Any], list[str]]:
    """Gelen degisiklikleri dogrular, kaydeder ve GERCEKTEN degisenleri doner.

    Panel formu her kaydetmede butun alanlari gonderiyor. Karsilastirma
    override dosyasina degil **su anki gecerli degere** yapiliyor; aksi
    halde .env'den gelen her varsayilan kalici bir override olarak yazilir
    ve sonradan .env'i degistirmek sessizce etkisiz kalirdi.

    Bos gonderilen sir alanlari yok sayilir - panel mevcut degeri
    gostermedigi icin bos kutu "sil" anlamina gelmemeli. Silmek icin "-".
    """
    if not isinstance(updates, dict):
        raise SettingsError("Gecersiz istek govdesi")

    unknown = set(updates) - set(_SPEC_BY_KEY)
    if unknown:
        raise SettingsError(f"Bilinmeyen ayar: {', '.join(sorted(unknown))}")

    current = load_overrides(data_dir)
    changed: list[str] = []
    touched = False

    for key, raw in updates.items():
        spec = _SPEC_BY_KEY[key]
        value = _coerce(spec, raw)

        if spec.type == "secret":
            if value == "":
                continue  # dokunulmadi
            if value == "-":
                value = ""  # acik temizleme istegi

        if isinstance(value, int | float) and not isinstance(value, bool):
            _check_ranges(key, value)

        if value == _effective(spec, settings):
            # Gecerli degerle ayni: gereksiz override'i temizle.
            # Davranis degismedigi icin "degisti" sayilmaz.
            if key in current:
                del current[key]
                touched = True
            continue

        current[key] = value
        changed.append(key)
        touched = True

    if touched:
        save_overrides(data_dir, current)
    return (current, changed)


# --- panele gonderilecek gorunum -------------------------------------------


def _mask(value: str) -> dict[str, Any]:
    if not value:
        return {"set": False, "hint": ""}
    tail = value[-4:] if len(value) > 8 else ""
    return {"set": True, "hint": f"...{tail}" if tail else "tanimli"}


def describe(settings: Settings) -> list[dict[str, Any]]:
    """Alanlarin panele gonderilecek hali. Sirlar maskeli."""
    rows: list[dict[str, Any]] = []
    for spec in SETTINGS_SPEC:
        raw = getattr(settings, spec.key, "")
        entry: dict[str, Any] = {
            "key": spec.key,
            "label": spec.label,
            "group": spec.group,
            "type": spec.type,
            "help": spec.help,
            "placeholder": spec.placeholder,
        }

        if spec.type == "secret":
            entry.update(_mask(str(raw or "")))
        elif spec.type == "severity":
            entry["value"] = raw.name.lower() if isinstance(raw, Severity) else str(raw)
            entry["choices"] = list(_SEVERITY_CHOICES)
        elif spec.type == "threat":
            entry["value"] = str(raw).lower()
            entry["choices"] = list(_THREAT_CHOICES)
        else:
            entry["value"] = "" if raw is None else str(raw)

        rows.append(entry)
    return rows
