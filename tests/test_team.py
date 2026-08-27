"""Takim: tek baglanti, cok kisi.

Rust+ eslestirmesi tek hesaba ait ve oyle kaliyor; sistem cikisi N kisiye
dagitiyor. Buradaki testler o dagitimin dogru kuruldugunu koruyor.
"""

from __future__ import annotations

import pytest

from sentinel.team import Member, Team, TeamError, load_team, save_team


def test_order_is_the_call_order():
    team = Team.from_dict({"members": [
        {"name": "Halit", "phone": "+905001"},
        {"name": "Ahmet", "phone": "+905002"},
    ]})

    assert [m.name for m in team.callable_members] == ["Halit", "Ahmet"]


def test_inactive_member_is_skipped_everywhere():
    """Tatildeki uyeyi silmeden susturabilmeli."""
    team = Team.from_dict({"members": [
        {"name": "Halit", "phone": "+905001", "active": False},
        {"name": "Ahmet", "phone": "+905002"},
    ]})

    assert [m.name for m in team.callable_members] == ["Ahmet"]
    assert [m.name for m in team.active_members] == ["Ahmet"]


def test_member_without_phone_is_not_called_but_still_notified():
    team = Team.from_dict({"members": [
        {"name": "Halit", "phone": "+905001"},
        {"name": "Ahmet", "discord_id": "123"},
    ]})

    assert [m.name for m in team.callable_members] == ["Halit"]
    assert len(team.active_members) == 2


def test_mentions_only_include_members_with_discord_id():
    team = Team.from_dict({"members": [
        {"name": "Halit", "discord_id": "111"},
        {"name": "Ahmet"},
        {"name": "Mehmet", "discord_id": "333", "active": False},
    ]})

    assert team.mentions() == "<@111>"


def test_phone_must_be_international():
    with pytest.raises(TeamError, match="uluslararasi"):
        Member(name="Halit", phone="05001234567")


def test_empty_name_is_rejected():
    with pytest.raises(TeamError, match="bos olamaz"):
        Member(name="   ")


def test_duplicate_names_are_rejected():
    with pytest.raises(TeamError, match="iki kez"):
        Team.from_dict({"members": [{"name": "Halit"}, {"name": "halit"}]})


def test_round_trip(tmp_path):
    team = Team.from_dict({"members": [
        {"name": "Halit", "phone": "+905001", "discord_id": "111"},
    ]})
    save_team(tmp_path, team)

    loaded = load_team(tmp_path)
    assert loaded.members[0].name == "Halit"
    assert loaded.members[0].discord_id == "111"


def test_missing_file_is_an_empty_team(tmp_path):
    assert load_team(tmp_path).members == []


def test_corrupt_file_does_not_crash_the_system(tmp_path):
    """Bozuk takim dosyasi alarmlari durdurmamali - sadece kimse aranmaz."""
    (tmp_path / "team.json").write_text("{bozuk", encoding="utf-8")

    assert load_team(tmp_path).members == []
