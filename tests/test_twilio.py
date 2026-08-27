"""Twilio TwiML uretimi ve sonuc yorumlama - ag erisimi olmadan.

Buradaki mantik gercek bir raid aninda calisacak ve o an hata ayiklama
sansi yok. Ozellikle "cevaplandi" yorumlamasi kritik: telesekretere dusen
bir arama cevap sayilirsa zincir durur ve kimse haberdar olmaz.
"""

from __future__ import annotations

import pytest

from sentinel.escalation import CallOutcome
from sentinel.twilio_caller import _TWIML_MAX, TwilioCaller, _to_result


@pytest.fixture
def caller():
    return TwilioCaller("AC123", "token", "+15550000000")


# --- TwiML -----------------------------------------------------------------


def test_twiml_repeats_message_twice(caller):
    """Telefonu yeni acan biri ilk cumleyi kacirir."""
    twiml = caller.build_twiml("Garaj saldiri altinda")
    assert twiml.count("<Say") == 2


def test_twiml_escapes_xml_metacharacters(caller):
    twiml = caller.build_twiml('Garaj & "Airlock" <test>')

    assert "&amp;" in twiml
    assert "<test>" not in twiml
    assert twiml.startswith("<Response>")
    assert twiml.endswith("</Response>")


def test_twiml_respects_twilio_size_limit(caller):
    """Twilio Twiml parametresini 4000 karakterle sinirliyor."""
    twiml = caller.build_twiml("A" * 6000)

    assert len(twiml) <= _TWIML_MAX
    assert "<Say" in twiml


def test_twiml_omits_voice_attribute_when_empty():
    caller = TwilioCaller("AC1", "t", "+1", voice="")
    twiml = caller.build_twiml("test")

    assert "voice=" not in twiml
    assert 'language="tr-TR"' in twiml


def test_twiml_uses_configured_language_and_voice():
    caller = TwilioCaller("AC1", "t", "+1", language="en-US", voice="Polly.Joanna")
    twiml = caller.build_twiml("test")

    assert 'language="en-US"' in twiml
    assert 'voice="Polly.Joanna"' in twiml


def test_missing_credentials_raise():
    with pytest.raises(ValueError):
        TwilioCaller("", "", "")


# --- sonuc yorumlama -------------------------------------------------------


def test_answered_by_human_is_acknowledged():
    result = _to_result(
        "CA1", {"status": "completed", "answered_by": "human", "duration": "14"}
    )

    assert result.outcome is CallOutcome.ANSWERED
    assert result.acknowledged
    assert result.duration_seconds == 14


def test_voicemail_is_not_an_acknowledgement():
    """Telesekreter cevap sayilirsa zincir durur ve kimse haberdar olmaz."""
    result = _to_result(
        "CA1",
        {"status": "completed", "answered_by": "machine_start", "duration": "20"},
    )

    assert result.outcome is CallOutcome.MACHINE
    assert not result.acknowledged


def test_fax_is_not_an_acknowledgement():
    result = _to_result(
        "CA1", {"status": "completed", "answered_by": "fax", "duration": "9"}
    )
    assert not result.acknowledged


def test_very_short_call_counts_as_no_answer():
    """1 saniyelik 'cevap' genelde otomatik kapanmadir."""
    result = _to_result(
        "CA1", {"status": "completed", "answered_by": "human", "duration": "1"}
    )
    assert result.outcome is CallOutcome.NO_ANSWER


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("busy", CallOutcome.BUSY),
        ("no-answer", CallOutcome.NO_ANSWER),
        ("failed", CallOutcome.FAILED),
        ("canceled", CallOutcome.FAILED),
    ],
)
def test_status_mapping(status, expected):
    assert _to_result("CA1", {"status": status}).outcome is expected


def test_price_is_absolute_because_twilio_reports_debits():
    result = _to_result(
        "CA1", {"status": "completed", "answered_by": "human", "duration": "30",
                "price": "-0.2875"}
    )
    assert result.price_usd == pytest.approx(0.2875)


def test_missing_price_is_zero_not_a_crash():
    """price yalnizca arama bittikten sonra doluyor."""
    result = _to_result("CA1", {"status": "completed", "answered_by": "human",
                                "duration": "5", "price": None})
    assert result.price_usd == 0.0


def test_garbage_fields_do_not_crash():
    result = _to_result("CA1", {"status": "completed", "duration": "abc",
                                "price": "not-a-number"})
    assert result.duration_seconds == 0
    assert result.price_usd == 0.0
