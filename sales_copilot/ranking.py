"""Deterministic read-only ranking of recorded lead facts, never authorization."""

from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from .schemas import StrictModel

PRIORITY_ORDER = {"low": 0, "medium": 1, "high": 2}


class RankingError(ValueError):
    """Fixed public code, without raw config/data contents."""


class RankingFactor(StrictModel):
    # New signals require a reviewed extractor and verified source, not a name in JSON.
    field: Literal["score", "priority"]
    weight: Decimal = Field(default=Decimal("1"), ge=0, le=10000, max_digits=12, decimal_places=6)


class RankingConfig(StrictModel):
    factors: tuple[RankingFactor, ...] = (RankingFactor(field="score"),)
    priority_tiebreak: bool = True

    @model_validator(mode="after")
    def valid_factors(self):
        fields = [factor.field for factor in self.factors]
        if len(set(fields)) != len(fields) or not any(f.weight > 0 for f in self.factors):
            raise ValueError("Ranking needs unique factors and at least one positive weight")
        return self


def load_ranking_config(path: str | Path) -> RankingConfig:
    try:
        return RankingConfig.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        raise RankingError("ranking_configuration_invalid") from None


def recorded_score(lead):
    value = lead.get("score")
    if type(value) is not int or not 0 <= value <= 100:
        raise RankingError("ranking_recorded_score_required")
    return Decimal(value)


def recorded_priority(lead):
    value = lead.get("priority")
    if not isinstance(value, str) or value not in PRIORITY_ORDER:
        raise RankingError("ranking_recorded_priority_required")
    return Decimal(PRIORITY_ORDER[value])


# Only these recorded attributes have defined ranking semantics today.
EXTRACTORS = {"score": recorded_score, "priority": recorded_priority}


class LeadRanker:
    def __init__(self, config: RankingConfig | None = None):
        self.config = RankingConfig.model_validate((config or RankingConfig()).model_dump())

    def _factors(self, sort_by):
        # An explicit score request always uses score, regardless of business weights.
        return (RankingFactor(field="score"),) if sort_by == "score" else self.config.factors

    def order(self, leads: list[dict], *, sort_by="id", sort_order=None) -> list[dict]:
        if sort_by not in {"id", "score", "business"} or sort_order not in {None, "asc", "desc"}:
            raise RankingError("ranking_order_invalid")
        ids = [r.get("id") for r in leads]
        if any(type(i) is not int or i <= 0 for i in ids) or len(set(ids)) != len(ids):
            raise RankingError("ranking_unique_lead_ids_required")
        direction = sort_order or ("asc" if sort_by == "id" else "desc")
        if sort_by == "id":
            return sorted(leads, key=lambda r: r["id"], reverse=direction == "desc")
        factors = self._factors(sort_by)
        def key(lead):
            # Decimal weights and bounded integer inputs avoid floating-point tie drift.
            total = sum((f.weight * EXTRACTORS[f.field](lead) for f in factors if f.weight > 0), Decimal(0))
            priority = recorded_priority(lead) if self.config.priority_tiebreak else Decimal(0)
            # Ascending reverses only the primary total, never the stable tie rules.
            return (-total if direction == "desc" else total, -priority, lead["id"])
        return sorted(leads, key=key)

    def description(self, sort_by, sort_order=None):
        if sort_by == "id":
            return None
        return {"strategy": sort_by, "direction": sort_order or "desc",
                "factors": [{"field": f.field, "weight": str(f.weight)} for f in self._factors(sort_by) if f.weight > 0],
                "tie_breakers": (["priority_desc"] if self.config.priority_tiebreak else []) + ["id_asc"]}
