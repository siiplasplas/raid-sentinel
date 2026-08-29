"""Cihaz adindan bolge ve sismik kademe cikarimi.

Rust+ cihazlarina oyun icinde verdigin ad, sistemin tek yapilandirma
arayuzu. Ayri bir panel doldurmak yerine alarmi "Garaj S3" diye
adlandiriyorsun; sistem bolgeyi ve kademeyi buradan anliyor.

Kural:
    "<bolge> S<kademe>"   ->  bolge + sismik kademe (1-3)
    "<bolge>"             ->  sadece bolge

Ornekler:
    "Garaj S3"      -> ("Garaj", 3)      C4/roket kademesi
    "Airlock S1"    -> ("Airlock", 1)    el bombası/beancan kademesi
    "Cati HBHF"     -> ("Cati HBHF", None)
"""

from __future__ import annotations

import re

_SEISMIC_SUFFIX = re.compile(r"^(?P<zone>.+?)[\s_-]+[Ss](?P<level>[1-3])$")


def parse_entity_name(name: str) -> tuple[str, int | None]:
    """Cihaz adini (bolge, sismik kademe) ikilisine ayirir."""
    cleaned = (name or "").strip()
    if not cleaned:
        return ("", None)

    match = _SEISMIC_SUFFIX.match(cleaned)
    if match is None:
        return (cleaned, None)

    return (match.group("zone").strip(), int(match.group("level")))
