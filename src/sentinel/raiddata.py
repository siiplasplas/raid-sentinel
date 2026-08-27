"""Raid matematiginin sabitleri.

Bu degerler oyun surumune bagli ve Facepunch periyodik olarak dengeliyor.
Bu yuzden koda gomulu "gercek" degil, dogrulama tarihiyle birlikte tutulan
en iyi bilgi. F3'te uzerine olculen gercek breach surelerinden gelen
kalibrasyon katmani binecek.

Dogrulama: 27 Agustos 2026, kaynak wiki.facepunch.com ve wikirust.com.
DIKKAT: rustlabs.com artik yok (skin pazarina donustu), oradan gelen
hicbir rakam kullanilmadi.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

GAME_DATA_VERIFIED_AT = "2026-08-27"


class WeaponClass(StrEnum):
    C4 = "c4"
    ROCKET = "rocket"
    SATCHEL = "satchel"
    BEANCAN = "beancan"
    GRENADE = "grenade"
    EXPLOSIVE_AMMO = "explosive_ammo"
    UNKNOWN = "unknown"


class SeismicLevel(IntEnum):
    """Sismik sensorun patlama tipine gore urettigi guc.

    Kaynak: Facepunch "Seismic Shift" devblog / Rustafied 6 Haziran 2024.
    Sensor gucu 3 saniye tutuyor, menzil 1-30 m ayarlanabilir.
    """

    LIGHT = 1  # F1 Grenade, Beancan
    MEDIUM = 2  # Explosive ammo, Satchel
    HEAVY = 3  # C4, Rocket


# Termometre devresi bize guc kademesini verir, tam patlayici tipini degil.
# 3. kademe C4 mi roket mi ayirt edilemiyor - ikisi de ciddi raid demek.
SEISMIC_TO_WEAPONS: dict[SeismicLevel, tuple[WeaponClass, ...]] = {
    SeismicLevel.LIGHT: (WeaponClass.GRENADE, WeaponClass.BEANCAN),
    SeismicLevel.MEDIUM: (WeaponClass.EXPLOSIVE_AMMO, WeaponClass.SATCHEL),
    SeismicLevel.HEAVY: (WeaponClass.C4, WeaponClass.ROCKET),
}

# Sulfur maliyetleri tarif agacindan tabandan yukari hesaplandi ve
# toplulugun kullandigi yuvarlak rakamlarla birebir tuttu.
# HV ve incendiary roket kaynaklari celisiyor - bilerek disarida.
SULFUR_COST: dict[WeaponClass, int] = {
    WeaponClass.C4: 2200,
    WeaponClass.ROCKET: 1400,
    WeaponClass.SATCHEL: 480,
    WeaponClass.BEANCAN: 120,
    WeaponClass.EXPLOSIVE_AMMO: 25,
}

# Bir kademedeki patlamanin ortalama sulfur maliyeti. Kademe icindeki
# belirsizligi (C4 mi roket mi) ortalayarak "ne kadar harcadilar"
# tahmininde kullanilir.
SEISMIC_AVG_SULFUR: dict[SeismicLevel, int] = {
    level: round(sum(SULFUR_COST.get(w, 0) for w in weapons) / len(weapons))
    for level, weapons in SEISMIC_TO_WEAPONS.items()
}


class Tier(StrEnum):
    TWIG = "twig"
    WOOD = "wood"
    STONE = "stone"
    METAL = "metal"
    ARMORED = "armored"


# Duvar segmenti basina, sert taraf. Ahsap duvarin roket sayisi iki kaynak
# arasinda celisiyor (1 vs 2) - surume bagli, 2 alindi (guvenli taraf).
WALL_COST: dict[Tier, dict[WeaponClass, int]] = {
    Tier.WOOD: {
        WeaponClass.C4: 1,
        WeaponClass.ROCKET: 2,
        WeaponClass.SATCHEL: 3,
        WeaponClass.EXPLOSIVE_AMMO: 48,
    },
    Tier.STONE: {
        WeaponClass.C4: 2,
        WeaponClass.ROCKET: 4,
        WeaponClass.SATCHEL: 10,
        WeaponClass.EXPLOSIVE_AMMO: 211,
    },
    Tier.METAL: {
        WeaponClass.C4: 4,
        WeaponClass.ROCKET: 8,
        WeaponClass.SATCHEL: 23,
        WeaponClass.EXPLOSIVE_AMMO: 406,
    },
    Tier.ARMORED: {
        WeaponClass.C4: 8,
        WeaponClass.ROCKET: 15,
        WeaponClass.SATCHEL: 46,
        WeaponClass.EXPLOSIVE_AMMO: 806,
    },
}

# Kapilar ve dis engeller. Bazi degerler hedefi cok dusuk HP'de birakir ve
# ucuz bir vurusla bitirilir; sayilar yukari yuvarlanmis.
DEPLOYABLE_COST: dict[str, dict[WeaponClass, int]] = {
    "wooden_door": {WeaponClass.C4: 1, WeaponClass.ROCKET: 1, WeaponClass.SATCHEL: 2},
    "sheet_metal_door": {WeaponClass.C4: 1, WeaponClass.ROCKET: 1, WeaponClass.SATCHEL: 4},
    "garage_door": {WeaponClass.C4: 2, WeaponClass.ROCKET: 3, WeaponClass.SATCHEL: 9},
    "armored_door": {WeaponClass.C4: 3, WeaponClass.ROCKET: 5, WeaponClass.SATCHEL: 15},
    "ladder_hatch": {WeaponClass.C4: 1, WeaponClass.ROCKET: 1, WeaponClass.SATCHEL: 4},
    "high_stone_wall": {WeaponClass.C4: 2, WeaponClass.ROCKET: 4, WeaponClass.SATCHEL: 10},
    "high_wood_wall": {WeaponClass.C4: 1, WeaponClass.ROCKET: 2, WeaponClass.SATCHEL: 6},
    "metal_embrasure": {WeaponClass.C4: 2, WeaponClass.ROCKET: 4, WeaponClass.SATCHEL: 13},
    "window_bars": {WeaponClass.C4: 2, WeaponClass.ROCKET: 4, WeaponClass.SATCHEL: 12},
    "auto_turret": {WeaponClass.C4: 1, WeaponClass.ROCKET: 4, WeaponClass.SATCHEL: 2},
}
