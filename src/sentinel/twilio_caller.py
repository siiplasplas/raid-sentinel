"""Twilio Voice ile arama - SDK'siz, dogrudan REST.

Iki tasarim karari:

1. **Gelen webhook yok.** Arama olustururken TwiML'i `Twiml` parametresiyle
   satir ici gonderiyoruz (4000 karakter siniri var). Bu sayede makinenin
   internetten erisilebilir olmasi gerekmiyor - evdeki ya da NAT arkasindaki
   bir VPS'te sorunsuz calisir. Sonucu webhook yerine arama kaynagini
   yoklayarak ogreniyoruz.

2. **Cevaplandi = onaylandi.** DTMF ile "1'e bas" onayi icin genel erisime
   acik bir URL gerekiyor; onu F5'e biraktik. Simdilik telefonu acmis olmak
   zinciri durdurmaya yetiyor. Telesekreter ayrimini `MachineDetection` ile
   yapiyoruz, yoksa telefonun sesli mesaja dusmesi "cevaplandi" sayilirdi.

Turkce uyarisi: Twilio'nun desteklenen ses tablosunda tr-TR gorunmuyor.
Ses/dil yapilandirilabilir birakildi; ilk gercek aramada dogrulanmali.
"""

from __future__ import annotations

import asyncio
import logging
from xml.sax.saxutils import escape

import httpx

from sentinel.escalation import CallOutcome, CallResult

log = logging.getLogger(__name__)

API_ROOT = "https://api.twilio.com/2010-04-01"

# Twilio'nun belgelendirdigi Twiml parametre siniri
_TWIML_MAX = 4000

_TERMINAL_STATUSES = frozenset(
    {"completed", "busy", "failed", "no-answer", "canceled"}
)
_STATUS_TO_OUTCOME = {
    "completed": CallOutcome.ANSWERED,
    "busy": CallOutcome.BUSY,
    "no-answer": CallOutcome.NO_ANSWER,
    "failed": CallOutcome.FAILED,
    "canceled": CallOutcome.FAILED,
}

# Trial hesapta dogrulanmamis numara / gecersiz numara
_ERROR_HINTS = {
    21211: "Numara gecersiz - E.164 bicimi gerekiyor (+90...)",
    21219: "Trial hesapta bu numara dogrulanmamis. Twilio konsolundan dogrula.",
    10004: "Es zamanli arama siniri asildi.",
}

_POLL_INTERVAL = 3.0
_MAX_RETRIES = 3


class TwilioCaller:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        *,
        language: str = "tr-TR",
        voice: str = "Polly.Filiz",
        ring_timeout: int = 30,
        max_wait_seconds: float = 120.0,
        timeout: float = 20.0,
    ) -> None:
        if not (account_sid and auth_token and from_number):
            raise ValueError("Twilio ayarlari eksik (sid, token, from numarasi)")

        self._sid = account_sid
        self._from = from_number
        self._language = language
        self._voice = voice
        self._ring_timeout = ring_timeout
        self._max_wait = max_wait_seconds

        self._client = httpx.AsyncClient(
            base_url=f"{API_ROOT}/Accounts/{account_sid}",
            auth=(account_sid, auth_token),
            timeout=timeout,
        )

    # --- TwiML -------------------------------------------------------------

    def build_twiml(self, message: str) -> str:
        """Mesaji iki kez okur - telefonu yeni acmis biri ilkini kacirir."""
        safe = escape(message)
        attrs = f'language="{escape(self._language)}"'
        if self._voice:
            attrs += f' voice="{escape(self._voice)}"'

        twiml = (
            "<Response>"
            f"<Say {attrs}>{safe}</Say>"
            "<Pause length=\"1\"/>"
            f"<Say {attrs}>{safe}</Say>"
            "</Response>"
        )

        if len(twiml) > _TWIML_MAX:
            # Mesaji kisaltip tek okumaya dus
            budget = _TWIML_MAX - (len(twiml) - len(safe) * 2) - 64
            trimmed = escape(message[: max(64, budget)])
            twiml = f"<Response><Say {attrs}>{trimmed}</Say></Response>"
        return twiml

    # --- arama -------------------------------------------------------------

    async def place_call(self, to: str, message: str) -> CallResult:
        payload = {
            "To": to,
            "From": self._from,
            "Twiml": self.build_twiml(message),
            "Timeout": str(self._ring_timeout),
            # Telesekretere dusen arama "cevaplandi" sayilmamali
            "MachineDetection": "Enable",
        }

        try:
            created = await self._post_with_retry("/Calls.json", payload)
        except httpx.HTTPError as exc:
            return CallResult(outcome=CallOutcome.FAILED, error=str(exc))

        if isinstance(created, CallResult):
            return created

        sid = str(created.get("sid") or "")
        if not sid:
            return CallResult(outcome=CallOutcome.FAILED, error=f"sid yok: {created}")

        return await self._await_completion(sid)

    async def _post_with_retry(self, path: str, data: dict[str, str]):
        delay = 2.0
        for _attempt in range(_MAX_RETRIES):
            response = await self._client.post(path, data=data)

            if response.status_code == 429:
                # Twilio acikca ustel geri cekilme oneriyor
                log.warning("Twilio hiz siniri, %.0f sn bekleniyor", delay)
                await asyncio.sleep(delay)
                delay *= 2
                continue

            if response.status_code >= 400:
                return self._error_result(response)

            return response.json()

        return CallResult(
            outcome=CallOutcome.FAILED, error="Twilio hiz siniri asilamadi"
        )

    def _error_result(self, response: httpx.Response) -> CallResult:
        code = 0
        detail = response.text[:300]
        try:
            body = response.json()
            code = int(body.get("code") or 0)
            detail = str(body.get("message") or detail)
        except (ValueError, TypeError):
            pass

        hint = _ERROR_HINTS.get(code, "")
        message = f"Twilio {response.status_code}/{code}: {detail}"
        if hint:
            message = f"{message} — {hint}"
        log.error(message)
        return CallResult(outcome=CallOutcome.FAILED, error=message)

    async def _await_completion(self, sid: str) -> CallResult:
        """Arama bitene kadar yoklar.

        `price` yalnizca arama bittikten sonra doluyor, o yuzden erken
        cikmiyoruz - maliyet takibi buna bagli.
        """
        deadline = asyncio.get_running_loop().time() + self._max_wait

        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(_POLL_INTERVAL)
            try:
                response = await self._client.get(f"/Calls/{sid}.json")
                response.raise_for_status()
                body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("Arama durumu okunamadi (%s): %s", sid, exc)
                continue

            status = str(body.get("status") or "")
            if status not in _TERMINAL_STATUSES:
                continue

            return _to_result(sid, body)

        log.warning("Arama zaman asimina ugradi, sonuc bilinmiyor: %s", sid)
        return CallResult(outcome=CallOutcome.NO_ANSWER, sid=sid, error="zaman asimi")

    async def aclose(self) -> None:
        await self._client.aclose()


def _to_result(sid: str, body: dict[str, object]) -> CallResult:
    status = str(body.get("status") or "")
    answered_by = str(body.get("answered_by") or "")
    outcome = _STATUS_TO_OUTCOME.get(status, CallOutcome.FAILED)

    # Telesekreter/faks cevap sayilmaz - zincir devam etmeli.
    if outcome is CallOutcome.ANSWERED and answered_by.startswith(("machine", "fax")):
        outcome = CallOutcome.MACHINE

    duration = 0
    try:
        duration = int(str(body.get("duration") or "0"))
    except ValueError:
        pass

    # Cok kisa "cevap" genelde otomatik kapanmadir.
    if outcome is CallOutcome.ANSWERED and duration < 2:
        outcome = CallOutcome.NO_ANSWER

    # price arama bitince doluyor ve borc oldugu icin negatif gelebilir.
    price = 0.0
    try:
        raw_price = body.get("price")
        if raw_price not in (None, ""):
            price = abs(float(str(raw_price)))
    except (TypeError, ValueError):
        pass

    return CallResult(
        outcome=outcome,
        sid=sid,
        duration_seconds=duration,
        price_usd=price,
    )
