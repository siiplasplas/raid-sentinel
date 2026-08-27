"""Takim: kimin haberi olacak, kim aranacak, hangi sirayla.

Tasarim karari: **tek baglanti, cok kisi.** Rust+ eslestirmesi tek bir
hesaba ait (senin hesabin) ve oyle kalmali - her takim uyesinin ayri
eslestirme yapmasi hem gereksiz hem de her wipe'ta N kat is demek.
Sistem tek bir baglantidan besleniyor, cikisi N kisiye dagitiyor.

Kanal basina nasil calisir:

  Discord  tek kanal herkese gider; kritik olayda nobetteki kisiler
           ayrica etiketlenir (Discord kullanici id'si girilmisse)
  ntfy     herkes ayni konuya abone olur, ekstra ayar gerekmez
  Telefon  sirayla aranir, ilk cevap veren zinciri durdurur
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

TEAM_FILENAME = "team.json"


class TeamError(ValueError):
    """Takim tanimi gecersiz."""


@dataclass(slots=True)
class Member:
    name: str
    phone: str = ""
    discord_id: str = ""
    # Kapali uye hicbir kanalda gorunmez; silmeden gecici olarak susturmak
    # icin (tatilde, uyuyor, telefonu bozuk).
    active: bool = True

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        self.phone = self.phone.strip()
        self.discord_id = self.discord_id.strip()
        if not self.name:
            raise TeamError("Uye adi bos olamaz")
        if self.phone and not self.phone.startswith("+"):
            raise TeamError(
                f"{self.name}: telefon uluslararasi bicimde olmali (+90...)"
            )

    @property
    def mention(self) -> str:
        return f"<@{self.discord_id}>" if self.discord_id else self.name


@dataclass(slots=True)
class Team:
    """Sirali uye listesi. Sira = telefon zincirinin sirasi."""

    members: list[Member] = field(default_factory=list)

    @property
    def active_members(self) -> list[Member]:
        return [m for m in self.members if m.active]

    @property
    def callable_members(self) -> list[Member]:
        return [m for m in self.active_members if m.phone]

    def mentions(self) -> str:
        """Kritik bildirimde etiketlenecek kisiler."""
        tags = [m.mention for m in self.active_members if m.discord_id]
        return " ".join(tags)

    def to_dict(self) -> dict[str, Any]:
        return {"members": [asdict(m) for m in self.members]}

    @classmethod
    def from_dict(cls, data: Any) -> Team:
        if not isinstance(data, dict):
            raise TeamError("Takim tanimi sozluk olmali")

        raw_members = data.get("members")
        if not isinstance(raw_members, list):
            raise TeamError("'members' bir liste olmali")

        members: list[Member] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_members, start=1):
            if not isinstance(raw, dict):
                raise TeamError(f"{index}. uye sozluk olmali")
            member = Member(
                name=str(raw.get("name", "")),
                phone=str(raw.get("phone", "")),
                discord_id=str(raw.get("discord_id", "")),
                active=bool(raw.get("active", True)),
            )
            key = member.name.casefold()
            if key in seen:
                raise TeamError(f"Ayni ad iki kez: {member.name}")
            seen.add(key)
            members.append(member)

        return cls(members=members)


def team_path(data_dir: Path) -> Path:
    return data_dir / TEAM_FILENAME


def load_team(data_dir: Path) -> Team:
    """Takimi okur. Dosya yoksa veya bozuksa bos takim - sistem calismaya
    devam eder, sadece kimse aranmaz."""
    path = team_path(data_dir)
    if not path.exists():
        return Team()
    try:
        with path.open("r", encoding="utf-8") as handle:
            return Team.from_dict(json.load(handle))
    except (json.JSONDecodeError, OSError, TeamError) as exc:
        log.error("Takim tanimi okunamadi, bos kabul ediliyor (%s): %s", path, exc)
        return Team()


def save_team(data_dir: Path, team: Team) -> Path:
    path = team_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(team.to_dict(), handle, indent=2, ensure_ascii=False)
    temp.replace(path)
    return path
