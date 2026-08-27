"""Rust+ kimlik bilgileri: FCM anahtarlari ve eslesmis sunucu.

Dosya `data/` altinda tutulur ve .gitignore'da - icinde Steam oturumundan
turetilmis token var, repoya girmemeli.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CREDENTIALS_FILENAME = "rustplus.config.json"


@dataclass(slots=True)
class PairedServer:
    """FCM eslestirme bildiriminden gelen sunucu bilgisi."""

    ip: str
    port: int
    player_id: str
    player_token: str
    name: str = ""
    paired_at: float = 0.0

    @property
    def server_id(self) -> str:
        return f"{self.ip}:{self.port}"


@dataclass(slots=True)
class Credentials:
    fcm_credentials: dict[str, Any] = field(default_factory=dict)
    expo_push_token: str = ""
    rustplus_auth_token: str = ""
    server: PairedServer | None = None

    @property
    def has_fcm(self) -> bool:
        return bool(self.fcm_credentials)

    @property
    def has_server(self) -> bool:
        return self.server is not None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "fcm_credentials": self.fcm_credentials,
            "expo_push_token": self.expo_push_token,
            "rustplus_auth_token": self.rustplus_auth_token,
        }
        if self.server is not None:
            data["server"] = asdict(self.server)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Credentials:
        server_data = data.get("server")
        server = None
        if isinstance(server_data, dict):
            try:
                server = PairedServer(
                    ip=str(server_data["ip"]),
                    port=int(server_data["port"]),
                    player_id=str(server_data["player_id"]),
                    player_token=str(server_data["player_token"]),
                    name=str(server_data.get("name", "")),
                    paired_at=float(server_data.get("paired_at", 0.0)),
                )
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("Kayitli sunucu bilgisi okunamadi, yok sayiliyor: %s", exc)

        return cls(
            fcm_credentials=data.get("fcm_credentials") or {},
            expo_push_token=str(data.get("expo_push_token", "")),
            rustplus_auth_token=str(data.get("rustplus_auth_token", "")),
            server=server,
        )


def credentials_path(data_dir: Path) -> Path:
    return data_dir / CREDENTIALS_FILENAME


def load_credentials(data_dir: Path) -> Credentials:
    path = credentials_path(data_dir)
    if not path.exists():
        return Credentials()
    try:
        with path.open("r", encoding="utf-8") as handle:
            return Credentials.from_dict(json.load(handle))
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Kimlik dosyasi bozuk (%s): %s", path, exc)
        return Credentials()


def save_credentials(data_dir: Path, credentials: Credentials) -> Path:
    path = credentials_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Once gecici dosyaya yaz, sonra yer degistir: yazma sirasinda cokme
    # olursa eski dosya saglam kalir.
    temp_path = path.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(credentials.to_dict(), handle, indent=2, ensure_ascii=False)
    temp_path.replace(path)

    log.info("Kimlik bilgileri kaydedildi: %s", path)
    return path
