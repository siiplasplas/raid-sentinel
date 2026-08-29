"""Komut satiri arayuzu.

    sentinel pair          Rust+ eslestirmesi (bir kez)
    sentinel run           Sistemi calistir
    sentinel doctor        Yapilandirmayi ve baglantiyi kontrol et
    sentinel test-notify   Bildirim kanallarini dene
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
from pathlib import Path

import uvicorn

from sentinel.api import create_app
from sentinel.app import Sentinel
from sentinel.base_model import BaseGraph, BaseModelError, base_path
from sentinel.config import get_settings
from sentinel.escalation import Contact
from sentinel.logging import setup_logging
from sentinel.notify import build_router_from_settings, sample_raid_event
from sentinel.raiddata import WeaponClass
from sentinel.rust.credentials import load_credentials
from sentinel.rust.register import PairingError, register
from sentinel.twilio_caller import TwilioCaller

log = logging.getLogger("sentinel.cli")


# --- komutlar --------------------------------------------------------------


async def cmd_pair(args: argparse.Namespace) -> int:
    settings = get_settings()
    data_dir = settings.db_path.parent
    existing = load_credentials(data_dir)

    if existing.has_fcm and not args.force:
        print("Bu makine zaten kayitli. Yeniden kaydetmek icin: sentinel pair --force")
        _print_pairing_next_steps()
        return 0

    print("\nRust+ eslestirmesi baslatiliyor.")
    print("Tarayicida Steam ile giris yapman istenecek.\n")

    try:
        await register(data_dir, port=args.port, host=args.host)
    except PairingError as exc:
        print(f"\nEslestirme tamamlanamadi: {exc}", file=sys.stderr)
        return 1

    print("\nKayit tamam.")
    _print_pairing_next_steps()
    return 0


def _print_pairing_next_steps() -> None:
    print(
        """
Sonraki adimlar:

  1. 'sentinel run' ile sistemi baslat ve acik birak.
  2. Oyunda sunucuya gir, ESC > Rust+ > 'Pair with Server'.
  3. Her Smart Alarm'a bak ve 'Pair' de.

Cihaz adlandirmasi onemli - sistem bolgeyi ve sismik kademeyi addan okuyor:

     "Garaj S3"     -> Garaj bolgesi, C4/roket kademesi
     "Airlock S2"   -> Airlock bolgesi, satchel kademesi
     "Cati"         -> Cati bolgesi, kademe yok

Eslestirdigin her cihaz otomatik kaydedilir ve izlemeye alinir.
"""
    )


async def cmd_run(args: argparse.Namespace) -> int:
    settings = get_settings()
    sentinel = Sentinel(settings)

    try:
        await sentinel.start()
    except RuntimeError as exc:
        print(f"Baslatilamadi: {exc}", file=sys.stderr)
        return 1

    config = uvicorn.Config(
        create_app(sentinel),
        host=args.host or settings.http_host,
        port=args.port or settings.http_port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    log.info("HTTP arayuzu: http://%s:%s/health", config.host, config.port)

    try:
        await server.serve()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await sentinel.stop()
    return 0


async def cmd_doctor(_args: argparse.Namespace) -> int:
    settings = get_settings()
    data_dir = settings.db_path.parent
    credentials = load_credentials(data_dir)

    checks: list[tuple[bool, str]] = []

    checks.append((credentials.has_fcm, "FCM kimlik bilgileri kayitli"))

    server = credentials.server
    if server is not None:
        checks.append((True, f"Eslesmis sunucu: {server.name or server.server_id}"))
    elif settings.is_paired:
        checks.append((True, f"Sunucu .env'den: {settings.server_id}"))
    else:
        checks.append((False, "Eslesmis sunucu yok - oyunda 'Pair with Server' yap"))

    checks.append((bool(settings.discord_webhook_url), "Discord webhook tanimli"))
    checks.append((bool(settings.ntfy_topic), "ntfy konusu tanimli"))

    twilio_ok = bool(
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_from_number
    )
    contacts = Contact.parse_list(settings.escalation_contacts)
    if twilio_ok and contacts:
        names = ", ".join(c.name for c in contacts)
        checks.append((True, f"Telefon zinciri: {names}"))
        cap = settings.monthly_call_budget_usd
        checks.append(
            (cap > 0, f"Aylik arama tavani: {cap} USD" if cap > 0 else
             "Aylik arama tavani YOK - sinirsiz harcama riski")
        )
    else:
        # Telefon opsiyonel; eksikligi hata degil ama gorunur olmali.
        print("\n  Not: telefon araması kapalı (Twilio ayarları veya kişi listesi eksik).")

    db_ok = settings.db_path.parent.exists() or settings.db_path.parent.parent.exists()
    checks.append((db_ok, f"Veri dizini yazilabilir: {settings.db_path.parent}"))

    print()
    for ok, label in checks:
        print(f"  [{'OK' if ok else '  '}] {label}")
    print()

    failures = [label for ok, label in checks if not ok]
    if failures:
        print(f"{len(failures)} eksik var.\n")
        return 1

    print("Her sey yerinde.\n")
    return 0


async def cmd_test_notify(_args: argparse.Namespace) -> int:
    settings = get_settings()
    router = build_router_from_settings(settings)

    if not router.notifiers:
        print("Yapilandirilmis kanal yok. .env dosyasini kontrol et.", file=sys.stderr)
        return 1

    print(f"Deneme gonderiliyor: {', '.join(n.name for n in router.notifiers)}")

    # CRITICAL secildi: en yuksek esikli kanal da dahil hepsi tetiklensin.
    await router.dispatch(sample_raid_event())
    await router.aclose()
    print("Gonderildi. Kanallari kontrol et.")
    return 0


async def cmd_base(args: argparse.Namespace) -> int:
    """Us tanimini dogrular ve her bolgeden TC'ye yolu gosterir."""
    settings = get_settings()
    path = Path(args.file) if args.file else base_path(settings.db_path.parent)

    if not path.exists():
        print(f"Us tanimi yok: {path}", file=sys.stderr)
        print("Panelin Us sekmesinden olusturabilirsin (ornek sablon hazir gelir).",
              file=sys.stderr)
        return 1

    try:
        graph = BaseGraph.load(path)
    except BaseModelError as exc:
        print(f"\nUs tanimi gecersiz:\n  {exc}\n", file=sys.stderr)
        return 1

    print(f"\n  {graph.name} - hedef: {graph.target}")
    print(f"  {len(graph.zones)} bolge, {len(graph.edges)} baglanti\n")

    weapons = (WeaponClass.C4, WeaponClass.ROCKET)
    header = "  {:<16}".format("BOLGE") + "".join(f"{w.value:>9}" for w in weapons)
    print(header)
    print("  " + "-" * (len(header) - 2))

    unreachable: list[str] = []
    for zone in sorted(graph.zones):
        if zone == graph.target:
            continue
        costs = [graph.remaining_cost(zone, w) for w in weapons]
        if any(c is None for c in costs):
            unreachable.append(zone)
            continue
        row = f"  {zone:<16}" + "".join(f"{c:>9}" for c in costs)
        print(row)

    print()
    for zone in sorted(graph.zones):
        if zone == graph.target:
            continue
        print(f"  {zone}:")
        print(f"    {graph.describe_path(zone)}")

    if unreachable:
        print(f"\n  UYARI: TC'ye yolu olmayan bolgeler: {', '.join(unreachable)}")
        return 1

    print("\n  Tanim gecerli.\n")
    return 0


async def cmd_test_call(args: argparse.Namespace) -> int:
    """Gercek bir arama yapar.

    Twilio'nun Turkce seslendirmeyi destekleyip desteklemedigi
    dogrulanamadi - bunu raid sirasinda ogrenmek istemiyoruz.
    """
    settings = get_settings()

    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        print("Twilio ayarlari eksik. .env dosyasini kontrol et.", file=sys.stderr)
        return 1

    contacts = Contact.parse_list(settings.escalation_contacts)
    target = args.to or (contacts[0].phone if contacts else "")
    if not target:
        print("Aranacak numara yok. --to ile ver veya ESCALATION_CONTACTS doldur.",
              file=sys.stderr)
        return 1

    caller = TwilioCaller(
        settings.twilio_account_sid,
        settings.twilio_auth_token,
        settings.twilio_from_number,
        language=settings.twilio_language,
        voice=settings.twilio_voice,
    )

    message = (
        "Dikkat. Garaj bölgesi saldırı altında. "
        "Bu bir testtir, gercek bir raid degil."
    )

    print(f"\nAranan: {target}")
    print(f"Dil/ses: {settings.twilio_language} / {settings.twilio_voice or '(varsayilan)'}")
    print(f"\nGonderilen TwiML:\n  {caller.build_twiml(message)}\n")
    print("Aranıyor, arama bitene kadar bekleniyor...\n")

    try:
        result = await caller.place_call(target, message)
    finally:
        await caller.aclose()

    print(f"  Sonuc     : {result.outcome}")
    print(f"  Sure      : {result.duration_seconds} sn")
    print(f"  Maliyet   : {result.price_usd:.4f} USD")
    if result.sid:
        print(f"  Arama SID : {result.sid}")
    if result.error:
        print(f"  Hata      : {result.error}")

    print(
        "\nSesi duyduysan ve Turkce okuduysa ayar dogru.\n"
        "Ses gelmediyse .env icinde TWILIO_LANGUAGE=en-US ve\n"
        "TWILIO_VOICE=Polly.Joanna yapip tekrar dene.\n"
    )
    return 0 if not result.error else 1


# --- giris noktasi ---------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentinel", description="Raid Sentinel - Rust ussu erken uyari sistemi"
    )
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR")
    sub = parser.add_subparsers(dest="command", required=True)

    p_pair = sub.add_parser("pair", help="Rust+ eslestirmesi")
    p_pair.add_argument("--port", type=int, default=3000, help="Yerel geri donus portu")
    p_pair.add_argument(
        "--host",
        default="127.0.0.1",
        help="Geri donus sunucusunun dinleyecegi arayuz."
        " Headless sunucuda 0.0.0.0 + SSH tuneli kullan.",
    )
    p_pair.add_argument("--force", action="store_true", help="Kayitli kimlikleri sifirla")
    p_pair.set_defaults(func=cmd_pair)

    p_run = sub.add_parser("run", help="Sistemi calistir")
    p_run.add_argument("--host", default=None)
    p_run.add_argument("--port", type=int, default=None)
    p_run.set_defaults(func=cmd_run)

    p_doctor = sub.add_parser("doctor", help="Yapilandirmayi kontrol et")
    p_doctor.set_defaults(func=cmd_doctor)

    p_test = sub.add_parser("test-notify", help="Bildirim kanallarini dene")
    p_test.set_defaults(func=cmd_test_notify)

    p_call = sub.add_parser("test-call", help="Gercek bir telefon aramasi dene")
    p_call.add_argument("--to", default=None, help="Aranacak numara (+90...)")
    p_call.set_defaults(func=cmd_test_call)

    p_base = sub.add_parser("base", help="Us tanimini dogrula ve yollari goster")
    p_base.add_argument("--file", default=None, help="Alternatif tanim dosyasi")
    p_base.set_defaults(func=cmd_base)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(args.log_level or settings.log_level)

    try:
        return asyncio.run(args.func(args))
    except KeyboardInterrupt:
        print("\nDurduruldu.")
        return 130


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(main())
