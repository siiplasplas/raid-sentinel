"""Ussun graf modeli: bolgeler, aralarindaki engeller ve TC'ye giden yol.

ETA hesaplanabilmesi icin sistemin ussun seklini bilmesi gerekiyor.
Model bilerek kaba tutuldu - metre metre bir plan degil, "hangi bolgeden
hangi bolgeye gecmek kac patlayici eder" sorusunun cevabi.

    Dis Duvar --[1 yuksek tas duvar]-- Kompound --[1 garaj kapisi]-- Garaj
                                                                       |
                                                          [2 tas duvar]|
                                                                       |
                                        TC --[1 sac duvar + 1 sac kapi]-- Airlock

Raider'in izleyecegi yol, saldirdigi bolgeden TC'ye giden **en ucuz** yol.
Kalan maliyet her an hesaplanabilir bir sayi olunca ETA da hesaplanabilir
hale geliyor.
"""

from __future__ import annotations

import heapq
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sentinel.raiddata import DEPLOYABLE_COST, WALL_COST, Tier, WeaponClass

log = logging.getLogger(__name__)

BASE_FILENAME = "base.json"

# Tabloda karsiligi olmayan patlayici icin referans olarak C4 kullanilir.
_FALLBACK_WEAPON = WeaponClass.C4


class BaseModelError(ValueError):
    """Us tanimi okunamadi veya tutarsiz."""


@dataclass(slots=True)
class Obstacle:
    """Iki bolgeyi ayiran tek bir engel turu.

    Ya bir duvar kademesi (`tier`) ya da bir yapi (`kind`) olur.
    """

    count: int = 1
    tier: Tier | None = None
    kind: str | None = None

    def __post_init__(self) -> None:
        if (self.tier is None) == (self.kind is None):
            raise BaseModelError(
                "Engel ya 'tier' ya da 'type' icermeli (ikisi birden veya hicbiri olmaz)"
            )
        if self.count < 1:
            raise BaseModelError(f"Engel adedi en az 1 olmali, {self.count} verildi")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Obstacle:
        count = int(data.get("count", 1))
        raw_tier = data.get("tier")
        raw_kind = data.get("type")

        tier = None
        if raw_tier is not None:
            try:
                tier = Tier(str(raw_tier).lower())
            except ValueError as exc:
                valid = ", ".join(t.value for t in Tier)
                raise BaseModelError(
                    f"Bilinmeyen duvar kademesi: {raw_tier!r}. Gecerli olanlar: {valid}"
                ) from exc

        if raw_kind is not None and str(raw_kind) not in DEPLOYABLE_COST:
            valid = ", ".join(sorted(DEPLOYABLE_COST))
            raise BaseModelError(
                f"Bilinmeyen yapi turu: {raw_kind!r}. Gecerli olanlar: {valid}"
            )

        return cls(count=count, tier=tier, kind=str(raw_kind) if raw_kind else None)

    @property
    def label(self) -> str:
        what = self.tier.value if self.tier else (self.kind or "?")
        return f"{self.count}x {what}"

    def cost(self, weapon: WeaponClass) -> int:
        """Bu engeli acmak icin gereken patlayici adedi."""
        table = WALL_COST.get(self.tier) if self.tier else DEPLOYABLE_COST.get(self.kind or "")
        if not table:
            return 0

        per_unit = table.get(weapon)
        if per_unit is None:
            # Bu patlayici icin veri yok - C4 cinsinden yaklas.
            per_unit = table.get(_FALLBACK_WEAPON, 0)
        return per_unit * self.count


@dataclass(slots=True)
class Edge:
    a: str
    b: str
    obstacles: list[Obstacle] = field(default_factory=list)

    def cost(self, weapon: WeaponClass) -> int:
        return sum(o.cost(weapon) for o in self.obstacles)

    @property
    def label(self) -> str:
        return " + ".join(o.label for o in self.obstacles) or "engelsiz"

    def other(self, zone: str) -> str:
        return self.b if zone == self.a else self.a


@dataclass(slots=True)
class PathStep:
    zone_from: str
    zone_to: str
    cost: int
    label: str


@dataclass(slots=True)
class BaseGraph:
    name: str
    target: str
    edges: list[Edge] = field(default_factory=list)

    # --- yukleme -----------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BaseGraph:
        target = str(data.get("target") or "").strip()
        if not target:
            raise BaseModelError("Us tanimi 'target' (TC bolgesi) icermeli")

        edges: list[Edge] = []
        for index, raw in enumerate(data.get("edges") or [], start=1):
            try:
                a = str(raw["from"]).strip()
                b = str(raw["to"]).strip()
            except (KeyError, TypeError) as exc:
                raise BaseModelError(f"{index}. baglantida 'from'/'to' eksik") from exc
            if not a or not b:
                raise BaseModelError(f"{index}. baglantida bolge adi bos")

            obstacles = [Obstacle.from_dict(o) for o in (raw.get("obstacles") or [])]
            edges.append(Edge(a=a, b=b, obstacles=obstacles))

        if not edges:
            raise BaseModelError("Us tanimi en az bir baglanti icermeli")

        graph = cls(name=str(data.get("name") or "Us"), target=target, edges=edges)

        if target not in graph.zones:
            known = ", ".join(sorted(graph.zones))
            raise BaseModelError(
                f"Hedef bolge '{target}' baglantilarda gecmiyor. Taninanlar: {known}"
            )
        return graph

    @classmethod
    def load(cls, path: Path) -> BaseGraph:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return cls.from_dict(json.load(handle))
        except FileNotFoundError as exc:
            raise BaseModelError(f"Us tanimi bulunamadi: {path}") from exc
        except json.JSONDecodeError as exc:
            raise BaseModelError(f"Us tanimi bozuk JSON ({path}): {exc}") from exc

    # --- sorgular ----------------------------------------------------------

    @property
    def zones(self) -> set[str]:
        return {z for e in self.edges for z in (e.a, e.b)}

    def _neighbours(self, zone: str) -> list[Edge]:
        return [e for e in self.edges if zone in (e.a, e.b)]

    def path_to_target(
        self, zone: str, weapon: WeaponClass = WeaponClass.C4
    ) -> list[PathStep] | None:
        """Bolgeden TC'ye en ucuz yol. Bolge taninmiyorsa None.

        Dijkstra: kenar agirligi o engeli acmanin patlayici adedi.
        """
        if zone not in self.zones:
            return None
        if zone == self.target:
            return []

        distances: dict[str, int] = {zone: 0}
        previous: dict[str, tuple[str, Edge]] = {}
        visited: set[str] = set()
        queue: list[tuple[int, str]] = [(0, zone)]

        while queue:
            cost, current = heapq.heappop(queue)
            if current in visited:
                continue
            visited.add(current)

            if current == self.target:
                break

            for edge in self._neighbours(current):
                neighbour = edge.other(current)
                if neighbour in visited:
                    continue
                new_cost = cost + edge.cost(weapon)
                if new_cost < distances.get(neighbour, 1 << 30):
                    distances[neighbour] = new_cost
                    previous[neighbour] = (current, edge)
                    heapq.heappush(queue, (new_cost, neighbour))

        if self.target not in distances:
            log.warning("'%s' bolgesinden TC'ye yol yok", zone)
            return None

        steps: list[PathStep] = []
        cursor = self.target
        while cursor != zone:
            parent, edge = previous[cursor]
            steps.append(
                PathStep(
                    zone_from=parent,
                    zone_to=cursor,
                    cost=edge.cost(weapon),
                    label=edge.label,
                )
            )
            cursor = parent
        steps.reverse()
        return steps

    def remaining_cost(
        self, zone: str, weapon: WeaponClass = WeaponClass.C4
    ) -> int | None:
        """TC'ye kalan toplam patlayici adedi. Bolge taninmiyorsa None."""
        steps = self.path_to_target(zone, weapon)
        if steps is None:
            return None
        return sum(s.cost for s in steps)

    def describe_path(self, zone: str, weapon: WeaponClass = WeaponClass.C4) -> str:
        steps = self.path_to_target(zone, weapon)
        if steps is None:
            return f"'{zone}' bolgesi us tanimda yok"
        if not steps:
            return "Zaten TC'de"
        parts = [f"{s.zone_from} -> {s.zone_to} ({s.cost}x, {s.label})" for s in steps]
        return " | ".join(parts)


def base_path(data_dir: Path) -> Path:
    return data_dir / BASE_FILENAME


def load_base(data_dir: Path) -> BaseGraph | None:
    """Us tanimi varsa yukler. Yoksa None - ETA kapali kalir, sistem calisir."""
    path = base_path(data_dir)
    if not path.exists():
        return None
    try:
        graph = BaseGraph.load(path)
    except BaseModelError as exc:
        log.error("Us tanimi yuklenemedi, ETA kapali: %s", exc)
        return None
    log.info("Us tanimi yuklendi: %s (%d bolge)", graph.name, len(graph.zones))
    return graph
