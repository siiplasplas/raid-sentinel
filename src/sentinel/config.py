"""Ayarlar. Tek kaynak: .env dosyasi ve ortam degiskenleri.

Sirlar koda gomulmez; .env dosyasi .gitignore icinde.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from sentinel.models import Severity


class Settings(BaseSettings):
    """Ayarlar calisma dizinine gore cozulur.

    Paket site-packages icine kuruluyor; kaynak agacina gore yol hesaplamak
    Docker'da ya da pip ile kurulmus bir surumde yanlis yeri gosterirdi.
    Normal bir servis gibi davraniyoruz: `.env` ve `data/` calisma dizininde.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Rust+ eslesmesi ---------------------------------------------------
    rust_server_ip: str = ""
    rust_server_port: int = 28082
    rust_steam_id: str = ""
    rust_player_token: str = ""
    rust_server_label: str = "Ana Sunucu"

    # Normalde oyun sunucusunun app portuna dogrudan baglaniyoruz. Bazi
    # barindiricilar o portu disariya kapatiyor; bu durumda Facepunch'in
    # kendi vekili uzerinden gitmek tek yol.
    rust_use_proxy: bool = False

    # --- Bildirim kanallari ------------------------------------------------
    discord_webhook_url: str = ""
    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_token: str = ""

    discord_min_severity: Severity = Severity.INFO
    ntfy_min_severity: Severity = Severity.WARN

    # --- Telefon aramasi (Twilio) ------------------------------------------
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # DIKKAT: Twilio'nun kendi desteklenen ses tablosunda Turkce (tr-TR)
    # gorunmuyor. Polly'de Filiz ve Burcu var ama Twilio bunlari
    # yayinliyor mu dogrulanamadi. Ilk gercek aramada test et; ses gelmezse
    # TWILIO_LANGUAGE=en-US ve TWILIO_VOICE=Polly.Joanna yap.
    twilio_language: str = "tr-TR"
    twilio_voice: str = "Polly.Filiz"

    # "Halit:+905xxxxxxxxx,Ahmet:+905xxxxxxxxx" - sirayla aranir
    escalation_contacts: str = ""

    # Aylik arama tavani (USD). 0 = sinirsiz, onerilmez.
    monthly_call_budget_usd: float = 5.0
    # Ayni bolge icin iki zincir arasinda beklenecek sure
    call_cooldown_seconds: int = 900
    # Telefonun calmasi icin gereken en dusuk tehdit: low|medium|high
    phone_min_threat: str = "high"
    # "23:00-08:00" - bos birakilirsa kapali
    quiet_hours: str = ""

    # Panel kimlik dogrulama. Bos = kapali (yalnizca 127.0.0.1 dinlerken
    # kabul edilebilir). Panelden Twilio token'i degistirilebildigi icin
    # disariya acacaksan bunu mutlaka doldur.
    panel_token: str = ""

    # --- Sistem ------------------------------------------------------------
    db_path: Path = Path("data/sentinel.db")
    log_level: str = "INFO"
    http_host: str = "127.0.0.1"
    http_port: int = 8787
    healthcheck_interval: int = Field(default=1800, ge=60)

    @field_validator("discord_min_severity", "ntfy_min_severity", mode="before")
    @classmethod
    def _parse_severity(cls, value: object) -> Severity:
        if isinstance(value, str | int):
            return Severity.parse(value)
        return value  # type: ignore[return-value]

    @field_validator("db_path", mode="after")
    @classmethod
    def _absolute_db_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else Path.cwd() / value

    # --- Turetilmis ---------------------------------------------------------
    @property
    def server_id(self) -> str:
        """Sunucuyu tekil tanimlar. Wipe sonrasi ip:port degisebilir."""
        return f"{self.rust_server_ip}:{self.rust_server_port}"

    @property
    def is_paired(self) -> bool:
        return bool(self.rust_server_ip and self.rust_steam_id and self.rust_player_token)

    def missing_pairing_fields(self) -> list[str]:
        missing = []
        if not self.rust_server_ip:
            missing.append("RUST_SERVER_IP")
        if not self.rust_steam_id:
            missing.append("RUST_STEAM_ID")
        if not self.rust_player_token:
            missing.append("RUST_PLAYER_TOKEN")
        return missing


_settings: Settings | None = None


def get_settings(*, reload: bool = False) -> Settings:
    global _settings
    if _settings is None or reload:
        _settings = Settings()
    return _settings
