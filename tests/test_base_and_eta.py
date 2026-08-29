"""Us graf modeli ve ETA hesabi.

ETA'nin en onemli ozelligi dogru olmasi degil - **bilmedigini soylemesi**.
Uydurulmus bir "3 dakika" gercek bir kayipa yol acar. Testlerin cogu
"hangi durumda None doner" sorusunu koruyor.
"""

from __future__ import annotations

import time

import pytest

from sentinel.base_model import BaseGraph, BaseModelError
from sentinel.eta import Confidence, estimate
from sentinel.raid import RaidSession
from sentinel.raiddata import WeaponClass

BASE = {
    "name": "Test Us",
    "target": "TC",
    "edges": [
        {"from": "Kompound", "to": "Garaj",
         "obstacles": [{"type": "garage_door", "count": 1}]},
        {"from": "Kompound", "to": "Koridor",
         "obstacles": [{"tier": "stone", "count": 1},
                       {"type": "sheet_metal_door", "count": 1}]},
        {"from": "Garaj", "to": "TC", "obstacles": [{"tier": "metal", "count": 1}]},
        {"from": "Koridor", "to": "TC", "obstacles": [{"tier": "metal", "count": 1}]},
    ],
}


@pytest.fixture
def graph():
    return BaseGraph.from_dict(BASE)


def make_session(
    zone: str = "Garaj",
    *,
    level: int | None = 3,
    triggers: int = 6,
    gap: float = 20.0,
) -> RaidSession:
    now = time.time()
    s = RaidSession(zone=zone, started_at=now - gap * triggers, last_trigger_at=now)
    s.trigger_count = triggers
    s.intervals = [gap] * (triggers - 1)
    if level is not None:
        s.levels[level] = triggers
    return s


# --- graf ------------------------------------------------------------------


def test_cheapest_path_is_chosen(graph):
    """Kompound'dan TC'ye iki yol var; ucuz olan secilmeli.

    Garaj uzerinden: 2 (garaj kapisi) + 4 (metal duvar) = 6 C4
    Koridor uzerinden: 2 (tas) + 1 (sac kapi) + 4 = 7 C4
    """
    assert graph.remaining_cost("Kompound", WeaponClass.C4) == 6
    steps = graph.path_to_target("Kompound", WeaponClass.C4)
    assert [s.zone_to for s in steps] == ["Garaj", "TC"]


def test_rocket_costs_more_than_c4(graph):
    c4 = graph.remaining_cost("Garaj", WeaponClass.C4)
    rocket = graph.remaining_cost("Garaj", WeaponClass.ROCKET)
    assert rocket > c4


def test_unknown_zone_returns_none(graph):
    assert graph.path_to_target("Yok Boyle Bir Yer") is None
    assert graph.remaining_cost("Yok Boyle Bir Yer") is None


def test_target_zone_has_empty_path(graph):
    assert graph.path_to_target("TC") == []


def test_disconnected_zone_is_unreachable():
    graph = BaseGraph.from_dict(
        {
            "target": "TC",
            "edges": [
                {"from": "Garaj", "to": "TC", "obstacles": [{"tier": "stone"}]},
                {"from": "Ada", "to": "Ada2", "obstacles": [{"tier": "stone"}]},
            ],
        }
    )
    assert graph.remaining_cost("Ada") is None


# --- tanim dogrulama -------------------------------------------------------


def test_target_must_exist_in_edges():
    with pytest.raises(BaseModelError, match="baglantilarda gecmiyor"):
        BaseGraph.from_dict(
            {"target": "Yok", "edges": [{"from": "A", "to": "B", "obstacles": []}]}
        )


def test_unknown_tier_is_rejected_with_helpful_message():
    with pytest.raises(BaseModelError, match="Bilinmeyen duvar kademesi"):
        BaseGraph.from_dict(
            {
                "target": "B",
                "edges": [{"from": "A", "to": "B",
                           "obstacles": [{"tier": "betonarme"}]}],
            }
        )


def test_unknown_deployable_is_rejected():
    with pytest.raises(BaseModelError, match="Bilinmeyen yapi turu"):
        BaseGraph.from_dict(
            {
                "target": "B",
                "edges": [{"from": "A", "to": "B",
                           "obstacles": [{"type": "celik_kapi"}]}],
            }
        )


def test_obstacle_needs_exactly_one_of_tier_or_type():
    with pytest.raises(BaseModelError):
        BaseGraph.from_dict(
            {"target": "B", "edges": [{"from": "A", "to": "B", "obstacles": [{}]}]}
        )
    with pytest.raises(BaseModelError):
        BaseGraph.from_dict(
            {
                "target": "B",
                "edges": [{"from": "A", "to": "B",
                           "obstacles": [{"tier": "stone", "type": "garage_door"}]}],
            }
        )


def test_empty_edges_rejected():
    with pytest.raises(BaseModelError, match="en az bir baglanti"):
        BaseGraph.from_dict({"target": "TC", "edges": []})


# --- ETA: ne zaman bilmedigini soyler --------------------------------------


def test_no_seismic_tier_means_no_eta(graph):
    """Patlayici tipi bilinmiyorsa hiz patlayiciya cevrilemez."""
    assert estimate(make_session(level=None), graph) is None


def test_unknown_zone_means_no_eta(graph):
    assert estimate(make_session("Cati"), graph) is None


def test_single_trigger_means_no_eta(graph):
    """Tek olcumle hiz tahmini olmaz."""
    session = make_session(triggers=1)
    session.intervals = []
    assert estimate(session, graph) is None


def test_already_at_target_means_no_eta(graph):
    assert estimate(make_session("TC"), graph) is None


# --- ETA: hesap ------------------------------------------------------------


def test_estimate_has_ordered_confidence_band(graph):
    eta = estimate(make_session(), graph)

    assert eta is not None
    assert eta.low_seconds <= eta.seconds <= eta.high_seconds
    assert eta.remaining_explosives >= 0


def test_faster_pace_gives_shorter_eta(graph):
    slow = estimate(make_session(gap=40.0), graph)
    fast = estimate(make_session(gap=10.0), graph)

    assert slow is not None and fast is not None
    assert fast.seconds < slow.seconds


def test_more_measurements_raise_confidence(graph):
    few = estimate(make_session(triggers=3), graph)
    many = estimate(make_session(triggers=10), graph)

    assert few is not None and many is not None
    assert few.confidence is Confidence.MEDIUM
    assert many.confidence is Confidence.GOOD


def test_explosives_already_spent_reduce_remaining(graph):
    """Garaj->TC 4 C4; ikisini yemislerse ikisi kalmali."""
    early = estimate(make_session(triggers=2, gap=20.0), graph)
    late = estimate(make_session(triggers=4, gap=20.0), graph)

    assert early is not None and late is not None
    assert late.remaining_explosives < early.remaining_explosives


def test_duplicate_sensor_triggers_do_not_inflate_speed(graph):
    """Ayni patlama iki sensoru tetiklerse aralik ~0 olur; sayilmamali."""
    session = make_session(triggers=6, gap=20.0)
    session.intervals = [0.1, 20.0, 0.2, 20.0, 20.0]

    eta = estimate(session, graph)
    assert eta is not None
    assert eta.seconds_per_explosive == pytest.approx(20.0, abs=0.1)


def test_format_is_human_readable(graph):
    eta = estimate(make_session(), graph)
    assert eta is not None

    text = eta.format()
    assert "TC'ye tahmini" in text
    assert "patlayıcı kaldı" in text
