"""Rust+ eslestirme onyuklemesi: FCM kaydi + Steam girisi.

`pip install rustplus` bu adimi icermiyor - kayit mantigi ayri ve PyPI'ye
yayinlanmamis `rustCli` deposunda ya da bir tarayici eklentisinde duruyor.
Kullanicinin baska bir arac indirip JSON yapistirmasi gerekmesin diye akis
buraya tasindi.

Akis:
  1. Sahte bir Android FCM istemcisi kaydedilir (Rust+ uygulamasinin
     Firebase sabitleriyle) -> gcm/fcm anahtarlari.
  2. FCM token'i Expo push token'ina cevrilir.
  3. Tarayicida Facepunch girisi yapilir, donen auth token yakalanir.
  4. Auth token + Expo token Facepunch'a kaydedilir; artik bu makineye
     eslestirme ve alarm bildirimleri gelebilir.

DIKKAT: 3. adim Facepunch'in giris sayfasinin davranisina bagli. Sayfa
kendini React Native WebView icinde saniyor ve sonucu
`ReactNativeWebView.postMessage` ile geri veriyor; bu yuzden acilan pencereye
o nesneyi biz enjekte ediyoruz. Sayfa degisirse bu akis kirilir - o zaman
kirilan yer burasi.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from uuid import uuid4

import httpx
from push_receiver.android_fcm_register import AndroidFCM

from sentinel.rust.credentials import Credentials, load_credentials, save_credentials

log = logging.getLogger(__name__)

# Rust+ mobil uygulamasinin Firebase kimligi.
FCM_API_KEY = "AIzaSyB5y2y-Tzqb4-I4Qnlsh_9naYv_TD8pCvY"
FCM_PROJECT_ID = "rust-companion-app"
FCM_GCM_SENDER_ID = "976529667804"
FCM_GMS_APP_ID = "1:976529667804:android:d6f1ddeb4403b338fea619"
FCM_PACKAGE_NAME = "com.facepunch.rust.companion"
FCM_PACKAGE_CERT = "E28D05345FB78A7A1A63D70F4A302DBF426CA5AD"

EXPO_URL = "https://exp.host/--/api/v2/push/getExpoPushToken"
EXPO_PROJECT_ID = "49451aca-a822-41e6-ad59-955718d0ff9c"

FACEPUNCH_LOGIN_URL = "https://companion-rust.facepunch.com/login"
FACEPUNCH_REGISTER_URL = "https://companion-rust.facepunch.com:443/api/push/register"

DEFAULT_CALLBACK_PORT = 3000
LOGIN_TIMEOUT_SECONDS = 300.0


class PairingError(RuntimeError):
    """Eslestirme akisi tamamlanamadi."""


# Giris penceresini acan ve token'i yakalayan sayfa.
# Popup her kimlik degistirdiginde (rust+ -> steam -> rust+) pencere
# nesnesindeki degisikliklerimiz siliniyor, o yuzden 250 ms'de bir
# yeniden enjekte ediyoruz.
_PAIR_PAGE = """<!doctype html>
<html lang="tr">
<head><meta charset="utf-8"><title>Raid Sentinel - Rust+ Eslestirme</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 34rem; margin: 4rem auto;
         padding: 0 1.5rem; line-height: 1.6; }}
  code {{ background: #eee; padding: .1em .3em; }}
</style>
</head>
<body>
  <h1>Rust+ eslestirmesi</h1>
  <p>Steam giris penceresi acildi. Gorunmuyorsa tarayicinin
     <strong>acilir pencere engelleyicisini</strong> kapatip bu sayfayi yenile.</p>
  <p id="status">Giris bekleniyor...</p>
<script>
  var popup = window.open("{login_url}", "", "width=800,height=800");
  var status = document.getElementById("status");

  if (!popup) {{
    status.textContent = "Acilir pencere engellendi. Engellemeyi kaldirip sayfayi yenile.";
  }}

  var timer = setInterval(function () {{
    try {{
      if (popup && popup.ReactNativeWebView === undefined) {{
        popup.ReactNativeWebView = {{
          postMessage: function (message) {{
            clearInterval(timer);
            var auth = JSON.parse(message);
            status.textContent = "Token alindi, pencere kapaniyor...";
            window.location.href = "/callback?token=" + encodeURIComponent(auth.Token);
            popup.close();
          }}
        }};
      }}
    }} catch (err) {{
      // Popup baska bir kimlikteyken erisim hatasi normal, bekle.
    }}
  }}, 250);
</script>
</body>
</html>
"""


# Token'in geri donebilecegi parametre adlari. Facepunch'in sayfasi
# degistiginde ad da degisebiliyor; birkacini birden deniyoruz.
_TOKEN_PARAMS = ("token", "Token", "authToken", "auth_token")


def _extract_token(query: str) -> str:
    params = parse_qs(query)
    for name in _TOKEN_PARAMS:
        value = (params.get(name) or [""])[0].strip()
        if value:
            return value
    return ""


def _make_handler(
    token_queue: queue.Queue[str], login_url: str, callback_url: str
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib arayuzu
            parsed = urlparse(self.path)

            if parsed.path == "/callback":
                token = _extract_token(parsed.query)
                if token:
                    token_queue.put(token)
                    self._respond(
                        200, "<h1>Tamam</h1><p>Bu sekmeyi kapatabilirsin.</p>"
                    )
                    return

                # Token beklenen adlarin hicbirinde degil - ham sorguyu
                # gosterip logla ki hangi ada dondugunu gorebilelim.
                log.error("Callback token icermiyor. Ham sorgu: %r", parsed.query)
                self._respond(
                    400,
                    "<h1>Token gelmedi</h1><p>Gelen parametreler:</p>"
                    f"<pre>{parsed.query or '(bos)'}</pre>",
                )
                return

            if parsed.path in ("/", "/index.html"):
                # Once dogrudan yonlendirme: Facepunch girisi returnUrl
                # destekliyor ve bu yol popup da cross-origin enjeksiyon da
                # gerektirmiyor. Tarayicilar popup penceresine ozellik
                # yazmayi engelliyor, eski yontem bu yuzden kirildi.
                target = f"{login_url}?returnUrl={quote(callback_url, safe='')}"
                self.send_response(302)
                self.send_header("Location", target)
                self.end_headers()
                return

            if parsed.path == "/legacy":
                # Yonlendirme ise yaramazsa eski popup yontemi.
                self._respond(200, _PAIR_PAGE.format(login_url=login_url))
                return

            self._respond(404, "<h1>404</h1>")

        def _respond(self, status: int, body: str) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            """Stdlib'in stderr'e yazan varsayilan logunu sustur."""

    return Handler


def _steam_login_blocking(port: int, host: str = "127.0.0.1") -> str:
    """Geri donus sunucusunu acar, tarayiciyi yonlendirir ve token'i bekler.

    Headless bir sunucuda tarayici yok; host="0.0.0.0" verilip SSH tuneli
    ile kendi makinenden baglanabilirsin.
    """
    token_queue: queue.Queue[str] = queue.Queue()
    callback_url = f"http://localhost:{port}/callback"
    handler = _make_handler(token_queue, FACEPUNCH_LOGIN_URL, callback_url)

    try:
        server = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        raise PairingError(
            f"Yerel port {port} acilamadi ({exc}). Baska bir uygulama kullaniyor olabilir."
        ) from exc

    thread = threading.Thread(target=server.serve_forever, name="pairing-http", daemon=True)
    thread.start()

    url = f"http://localhost:{port}/"
    log.info("Tarayici aciliyor: %s", url)
    print(f"\n  Tarayicida su adres acilacak: {url}")
    print("  Acilmazsa adresi elle yapistir.\n")
    webbrowser.open(url)

    try:
        return token_queue.get(timeout=LOGIN_TIMEOUT_SECONDS)
    except queue.Empty as exc:
        raise PairingError(
            f"{LOGIN_TIMEOUT_SECONDS / 60:.0f} dakika icinde giris tamamlanmadi"
        ) from exc
    finally:
        server.shutdown()
        server.server_close()


async def _get_expo_push_token(fcm_token: str) -> str:
    payload = {
        "deviceId": str(uuid4()),
        "projectId": EXPO_PROJECT_ID,
        "appId": FCM_PACKAGE_NAME,
        "deviceToken": fcm_token,
        "type": "fcm",
        "development": False,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(EXPO_URL, data=payload)

    if response.status_code >= 400:
        raise PairingError(f"Expo token alinamadi ({response.status_code}): {response.text[:300]}")

    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise PairingError(f"Expo yaniti okunamadi: {response.text[:300]}") from exc

    token = ""
    data = body.get("data")
    if isinstance(data, dict):
        token = str(data.get("expoPushToken") or "")
    if not token:
        token = str(body.get("expoPushToken") or "")

    if not token:
        raise PairingError(f"Expo yanitinda token yok: {body}")
    return token


async def _register_with_facepunch(auth_token: str, expo_token: str) -> None:
    payload = {
        "AuthToken": auth_token,
        "DeviceId": "rustplus.py",
        "PushKind": 3,
        "PushToken": expo_token,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            FACEPUNCH_REGISTER_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
        )

    if response.status_code >= 400:
        raise PairingError(
            f"Facepunch kaydi reddetti ({response.status_code}): {response.text[:300]}"
        )
    log.info("Facepunch push kaydi tamamlandi")


async def register(
    data_dir: Path,
    *,
    port: int = DEFAULT_CALLBACK_PORT,
    host: str = "127.0.0.1",
) -> Credentials:
    """Tam eslestirme onyuklemesini calistirir ve kimlikleri kaydeder."""
    log.info("1/4 FCM istemcisi kaydediliyor...")
    fcm_credentials = await asyncio.to_thread(
        AndroidFCM.register,
        FCM_API_KEY,
        FCM_PROJECT_ID,
        FCM_GCM_SENDER_ID,
        FCM_GMS_APP_ID,
        FCM_PACKAGE_NAME,
        FCM_PACKAGE_CERT,
    )
    fcm_token = (fcm_credentials.get("fcm") or {}).get("token", "")
    if not fcm_token:
        raise PairingError(f"FCM kaydi token dondurmedi: {fcm_credentials}")

    log.info("2/4 Expo push token'i aliniyor...")
    expo_token = await _get_expo_push_token(fcm_token)

    log.info("3/4 Steam girisi bekleniyor...")
    auth_token = await asyncio.to_thread(_steam_login_blocking, port, host)

    log.info("4/4 Facepunch'a kaydediliyor...")
    await _register_with_facepunch(auth_token, expo_token)

    existing = load_credentials(data_dir)
    credentials = Credentials(
        fcm_credentials=fcm_credentials,
        expo_push_token=expo_token,
        rustplus_auth_token=auth_token,
        # Sunucu eslesmesi ayri bir adim; varsa korunur.
        server=existing.server,
    )
    save_credentials(data_dir, credentials)
    return credentials
