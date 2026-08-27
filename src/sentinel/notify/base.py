"""Bildirim surucusu sozlesmesi.

Yeni kanal eklemek (F2'de Twilio araması) bu arayuzu uygulamak demek.
"""

from __future__ import annotations

import abc

from sentinel.models import Event, Severity


class Notifier(abc.ABC):
    """Tek bir bildirim kanali.

    `min_severity` esigi yonlendirici tarafindan uygulanir; surucu kendi
    esigini kontrol etmek zorunda degil.
    """

    name: str = "notifier"

    def __init__(self, min_severity: Severity = Severity.INFO) -> None:
        self.min_severity = min_severity

    @abc.abstractmethod
    async def send(self, event: Event) -> None:
        """Olayi gonderir. Hata firlatabilir - yonlendirici yakalar ve loglar."""

    async def aclose(self) -> None:
        """Acik baglantilari kapatir. Varsayilan: yapacak bir sey yok."""
        return None

    def accepts(self, event: Event) -> bool:
        return event.severity >= self.min_severity

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name} min={self.min_severity.name}>"
