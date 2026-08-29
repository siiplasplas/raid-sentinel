"""README icin panelin gercek ekran goruntulerini uretir.

Chrome'u headless calistirip DevTools Protocol uzerinden surer: her sekmeye
tiklar, gorunumu icerigin gercek yuksekligine ayarlar ve PNG kaydeder.

Yukseklik icin kucuk bir numara var: body'de min-height:100vh oldugu icin
scrollHeight hep gorunum kadar cikar. Once gorunumu kucultup sonra olcuyoruz.

Kullanim - once demo paneli baslat:

    python scripts/demo_panel.py
    python scripts/screenshots.py

Chrome baska bir yerdeyse CHROME degiskeniyle yolu ver.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import urllib.request

import websockets

CHROME = (
    os.environ.get("CHROME")
    or shutil.which("chrome")
    or shutil.which("google-chrome")
    or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
)
PORT = 9333
URL = os.environ.get("PANEL_URL", "http://127.0.0.1:8788/")
SHOTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "docs" / "screenshots"
PROFILE = pathlib.Path(tempfile.gettempdir()) / "sentinel-shot-profile"

WIDTH = 1360
SHOTS = [
    ("events", "01-live.png"),
    ("base", "02-base-map.png"),
    ("devices", "03-devices.png"),
    ("history", "04-archive.png"),
    ("team", "05-team.png"),
    ("system", "06-system.png"),
]


class CDP:
    """Tek sayfaya bagli minimal DevTools Protocol istemcisi."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.n = 0

    async def send(self, method: str, **params):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def viewport(self, height: int, *, scale: int = 2, mobile: bool = False) -> None:
        await self.send(
            "Emulation.setDeviceMetricsOverride",
            width=WIDTH if not mobile else 414,
            height=height,
            deviceScaleFactor=scale,
            mobile=mobile,
        )

    async def png(self, path: pathlib.Path) -> None:
        res = await self.send(
            "Page.captureScreenshot", format="png", captureBeyondViewport=False
        )
        path.write_bytes(base64.b64decode(res["data"]))
        print(f"  {path.name}  {path.stat().st_size // 1024} KB")


async def _attach(proc) -> str:
    """Chrome acilana kadar DevTools ucunu yoklar."""
    for _ in range(40):
        await asyncio.sleep(0.4)
        if proc.poll() is not None:
            raise SystemExit("Chrome beklenmedik sekilde kapandi")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json") as r:
                tabs = json.load(r)
        except Exception:
            continue
        page = next((t for t in tabs if t["type"] == "page"), None)
        if page:
            return page["webSocketDebuggerUrl"]
    raise SystemExit("Chrome DevTools'a baglanilamadi")


def panel_language() -> str:
    """Panelin dilini kendisine soruyoruz - goruntuler o dilin klasorune gider."""
    try:
        with urllib.request.urlopen(URL.rstrip("/") + "/api/state", timeout=5) as r:
            return json.load(r).get("language") or "tr"
    except Exception:
        return "tr"


async def main() -> None:
    if not pathlib.Path(CHROME).exists():
        raise SystemExit(f"Chrome bulunamadi: {CHROME}\nCHROME degiskeniyle yolu ver.")

    out = SHOTS_DIR / panel_language()
    out.mkdir(parents=True, exist_ok=True)
    print(f"  -> {out}")
    shutil.rmtree(PROFILE, ignore_errors=True)

    proc = subprocess.Popen(
        [
            CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
            f"--user-data-dir={PROFILE}", "--no-first-run",
            "--no-default-browser-check", "--hide-scrollbars", "--disable-gpu",
            "--force-color-profile=srgb", f"--window-size={WIDTH},1000", "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        ws_url = await _attach(proc)
        async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
            c = CDP(ws)
            await c.send("Page.enable")
            await c.send("Runtime.enable")
            await c.viewport(1000)
            await c.send("Page.navigate", url=URL)
            await asyncio.sleep(5)  # SSE ve ilk fetch'ler otursun

            for tab, name in SHOTS:
                await c.send(
                    "Runtime.evaluate",
                    expression=f"document.querySelector('[data-tab=\"{tab}\"]').click()",
                )
                await asyncio.sleep(2.0)
                await c.send("Runtime.evaluate", expression="window.scrollTo(0,0)")

                # Once kucult, sonra gercek icerik yuksekligini olc.
                await c.viewport(300, scale=1)
                await asyncio.sleep(0.5)
                h = await c.send(
                    "Runtime.evaluate",
                    expression=(
                        "Math.max(620, Math.min(1900, "
                        "Math.ceil(document.documentElement.scrollHeight) + 24))"
                    ),
                )
                await c.viewport(int(h["result"]["value"]))
                await asyncio.sleep(0.6)
                await c.png(out / name)

            await c.viewport(896, mobile=True)
            await c.send(
                "Runtime.evaluate",
                expression="document.querySelector('[data-tab=\"events\"]').click()",
            )
            await asyncio.sleep(1.5)
            await c.png(out / "07-mobile.png")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(PROFILE, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
