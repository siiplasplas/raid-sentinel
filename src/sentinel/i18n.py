"""Kullanicinin gordugu metinlerin dili.

Kapsam: panel, Discord/ntfy bildirimleri ve telefon seslendirmesi. Loglar,
kod yorumlari ve API alan adlari her zaman oldugu gibi kaliyor - onlar
gelistirici yuzeyi, cevrilmeleri kimseye fayda saglamaz.

Dil tek bir ayardan geliyor (`language`) cunku metin iki yoldan cikiyor:
panele ve bildirim kanallarina. Bunlarin farkli dillerde olmasi, ayni
saldiriyi Discord'da Turkce panelde Ingilizce okumak demek olurdu.

Seslendirme icin ozellikle onemli: Twilio "bolgesi" ile "bölgesi"yi farkli
okur, yani burasi kozmetik degil.
"""

from __future__ import annotations

import logging
from enum import StrEnum

log = logging.getLogger(__name__)


class Lang(StrEnum):
    TR = "tr"
    EN = "en"


DEFAULT = Lang.TR

# Anahtar -> {dil: sablon}. Sablonlar str.format ile dolduruluyor.
CATALOG: dict[str, dict[str, str]] = {
    # --- olay basliklari ---
    "raid.started":   {"tr": "{zone}: saldırı başladı",  "en": "{zone}: attack started"},
    "raid.progress":  {"tr": "{zone}: saldırı sürüyor",  "en": "{zone}: attack ongoing"},
    "raid.ended":     {"tr": "{zone}: saldırı durdu",    "en": "{zone}: attack stopped"},
    "raid.alarm":     {"tr": "Alarm tetiklendi",         "en": "Alarm triggered"},
    "zone.unknown":   {"tr": "Bilinmeyen bölge",         "en": "Unknown zone"},

    # --- oturum ozeti ---
    "desc.triggers":  {"tr": "{n} tetikleme",            "en": "{n} triggers"},
    "desc.presence":  {"tr": "hareket algılandı",        "en": "movement detected"},
    "desc.heaviest":  {"tr": "en ağır: {label}",         "en": "heaviest: {label}"},
    "desc.manual":    {"tr": " (kademe elle atanmış)",
                       "en": " (tier assigned by hand)"},
    "desc.minutes":   {"tr": "{n} dk süredir",           "en": "{n} min so far"},
    "desc.seconds":   {"tr": "{n} sn süredir",           "en": "{n} s so far"},

    # --- patlayici siniflari ---
    "weapon.light":   {"tr": "el bombası/beancan",       "en": "grenade/beancan"},
    "weapon.medium":  {"tr": "satchel/patlayıcı mermi",  "en": "satchel/explosive ammo"},
    "weapon.heavy":   {"tr": "C4/roket",                 "en": "C4/rocket"},

    # --- puanlama gerekceleri ---
    "score.heavy":    {"tr": "C4/roket kademesinde patlama",
                       "en": "explosion at C4/rocket tier"},
    "score.medium":   {"tr": "satchel/patlayıcı mermi kademesinde patlama",
                       "en": "explosion at satchel/explosive-ammo tier"},
    "score.light":    {"tr": "hafif patlayıcı (el bombası/beancan)",
                       "en": "light explosive (grenade/beancan)"},
    "score.presence": {"tr": "hareket sensörü tetiklendi (kademe elle atanmış)",
                       "en": "motion sensor triggered (tier assigned by hand)"},
    "score.unverified": {"tr": "{reason} (kademe elle atanmış, doğrulanmadı)",
                         "en": "{reason} (tier assigned by hand, unverified)"},
    "score.distinct": {"tr": "{n} ayrı sensör tetiklendi",
                       "en": "{n} separate sensors triggered"},
    "score.sustained": {"tr": "{n} dakikadır sürüyor",
                        "en": "ongoing for {n} min"},
    "score.teammate": {"tr": "takım arkadaşı bölgeye yakın (puan düşürüldü)",
                       "en": "teammate near the zone (score reduced)"},
    "score.none":     {"tr": "belirgin bir kanıt yok",   "en": "no clear evidence"},

    # --- ETA ---
    "conf.low":       {"tr": "düşük",                    "en": "low"},
    "conf.medium":    {"tr": "orta",                     "en": "medium"},
    "conf.good":      {"tr": "iyi",                      "en": "good"},
    "eta.line":       {"tr": "TC'ye tahmini {eta} (±{band}, güven: {conf}) · "
                             "{n} patlayıcı kaldı",
                       "en": "ETA to TC {eta} (±{band}, confidence: {conf}) · "
                             "{n} explosives left"},

    # --- telefon zinciri ---
    "esc.started":    {"tr": "{zone}: telefon zinciri başladı",
                       "en": "{zone}: call chain started"},
    "esc.answered":   {"tr": "{zone}: {name} cevap verdi",
                       "en": "{zone}: {name} answered"},
    "esc.answered_body": {"tr": "{n} sn konuşuldu. Zincir durduruldu.",
                          "en": "Talked for {n}s. Chain stopped."},
    "esc.nobody":     {"tr": "{zone}: kimse cevap vermedi",
                       "en": "{zone}: nobody answered"},
    "esc.nobody_body": {"tr": "{n} kişi arandı, cevap alınamadı.",
                        "en": "Called {n} people, nobody answered."},
    "esc.all_zones":  {"tr": "tüm bölgeler",             "en": "all zones"},

    # --- seslendirme ---
    "tts.alert":      {"tr": "Dikkat. {zone} bölgesi saldırı altında. {detail}.",
                       "en": "Attention. Zone {zone} is under attack. {detail}."},

    # --- panelden tetiklenen eylemler ---
    "ack.done":       {"tr": "{zone}: üstlenildi",       "en": "{zone}: acknowledged"},
    "ack.all":        {"tr": "Tüm bölgeler",             "en": "All zones"},
}

_current: Lang = DEFAULT


def set_language(value: str | Lang | None) -> Lang:
    """Etkin dili degistirir. Taninmayan deger varsayilana duser."""
    global _current
    try:
        _current = Lang(str(value).lower())
    except ValueError:
        log.warning("Bilinmeyen dil %r, %s kullaniliyor", value, DEFAULT)
        _current = DEFAULT
    return _current


def language() -> Lang:
    return _current


def t(key: str, **params: object) -> str:
    """Anahtari etkin dilde metne cevirir.

    Eksik anahtar bir programlama hatasi ama calisan bir alarm sistemini
    KeyError ile dusurmeye degmez - anahtarin kendisi donuyor ki hem metin
    gorunsun hem eksik oldugu belli olsun.
    """
    entry = CATALOG.get(key)
    if entry is None:
        log.warning("Ceviri anahtari yok: %s", key)
        return key
    template = entry.get(_current) or entry.get(DEFAULT) or key
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        log.warning("Ceviri parametresi eksik: %s", key)
        return template
