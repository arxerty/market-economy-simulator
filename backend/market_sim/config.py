"""Experiment configuration schema.

An experiment is a YAML or JSON file validated against :class:`ExperimentConfig`.
Every field has a sensible default so that small configs stay small.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class Distribution(BaseModel):
    """A value drawn once per agent at setup time from the seeded RNG."""

    dist: Literal["fixed", "uniform", "normal", "choice"] = "fixed"
    value: float = 0.0
    low: float = 0.0
    high: float = 0.0
    mean: float = 0.0
    std: float = 0.0
    values: list[float] = Field(default_factory=list)
    min: Optional[float] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_number(cls, v: Any) -> Any:
        if isinstance(v, (int, float)):
            return {"dist": "fixed", "value": float(v)}
        return v


DistLike = Union[Distribution, float]


class GoodConfig(BaseModel):
    id: str
    name: Optional[str] = None
    tick: float = 0.01
    initial_price: float = 100.0
    # "auto" = competitive equilibrium computed from buyers' valuations and sellers' costs
    fundamental_value: Union[Literal["auto"], float] = "auto"
    # Optional piecewise-constant schedule overriding fundamental_value: [{round: 10, value: 120}]
    fundamental_schedule: list[dict[str, float]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _default_name(self) -> "GoodConfig":
        if not self.name:
            self.name = self.id.capitalize()
        return self


class StrategyConfig(BaseModel):
    name: str = "default"
    prompt: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class AgentGroupConfig(BaseModel):
    role: Literal["buyer", "seller", "speculator", "regulator"]
    count: int = 1
    id_prefix: Optional[str] = None
    # llm | heuristic for traders; schedule | heuristic | llm for regulators
    decision: Literal["llm", "heuristic", "schedule"] = "llm"
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    cash: DistLike = 1000.0
    income_per_round: float = 0.0
    inventory: dict[str, DistLike] = Field(default_factory=dict)
    # Buyers
    valuations: dict[str, DistLike] = Field(default_factory=dict)
    units_per_round: int = 1
    valuation_decay: float = 0.0  # each extra unit within a round is worth (1-decay)x the previous
    # Sellers
    costs: dict[str, DistLike] = Field(default_factory=dict)
    capacity: dict[str, int] = Field(default_factory=dict)
    cost_slope: float = 0.0  # marginal cost increase per unit within a round
    # Observation
    info_lag: int = 0
    memory_size: int = 8
    # Regulator
    objective: str = ""
    decision_interval: int = 1

    @field_validator("count")
    @classmethod
    def _count_positive(cls, v: int) -> int:
        if v < 0:
            raise ValueError("count must be >= 0")
        return v


class CacheConfig(BaseModel):
    # off: never cache. record: call provider, store responses. replay: serve from cache,
    # call provider (and store) on miss. strict_replay: serve from cache, use fallback on miss.
    mode: Literal["off", "record", "replay", "strict_replay"] = "record"
    path: str = "data/llm_cache.sqlite"

    @field_validator("mode", mode="before")
    @classmethod
    def _yaml_off(cls, v: Any) -> Any:
        # YAML 1.1 parses a bare `off` as False
        return "off" if v is False or v is None else v


class LLMConfig(BaseModel):
    provider: str = "mock"
    model: str = "claude-opus-5-5"
    temperature: Optional[float] = None
    effort: Optional[str] = "low"
    max_tokens: int = 1024
    max_concurrency: int = 8
    timeout_s: float = 30.0
    max_retries: int = 2
    retry_backoff_s: float = 0.5
    cache: CacheConfig = Field(default_factory=CacheConfig)
    # Provider-specific options (e.g. mock failure injection, Anthropic fallbacks)
    options: dict[str, Any] = Field(default_factory=dict)


class CommunicationConfig(BaseModel):
    enabled: bool = False
    participants: list[Literal["buyer", "seller", "speculator"]] = Field(default_factory=lambda: ["seller"])
    max_message_chars: int = 280
    history_rounds: int = 3


class MarketConfig(BaseModel):
    order_ttl: int = 1
    max_orders_per_agent: int = 4
    max_quantity_per_order: int = 20
    max_price: float = 100000.0
    communication: CommunicationConfig = Field(default_factory=CommunicationConfig)


class PolicyAction(BaseModel):
    """A regulator action. Scheduled, LLM-produced and user-issued actions share this shape."""

    action: Literal[
        "set_tax", "remove_tax", "set_price_floor", "set_price_ceiling",
        "set_subsidy", "clear_controls", "hold",
    ]
    good: Optional[str] = None
    tax_type: Literal["per_unit", "ad_valorem"] = "per_unit"
    rate: float = 0.0
    payer: Literal["buyer", "seller"] = "seller"
    value: Optional[float] = None
    reason: str = ""


class ScheduledPolicy(PolicyAction):
    round: int


class PolicyBounds(BaseModel):
    max_per_unit_tax: float = 100.0
    max_ad_valorem: float = 0.9
    max_subsidy: float = 100.0


class PolicyConfig(BaseModel):
    schedule: list[ScheduledPolicy] = Field(default_factory=list)
    bounds: PolicyBounds = Field(default_factory=PolicyBounds)
    # Parameters of the built-in heuristic regulator (bubble stabilizer)
    stabilizer_threshold: float = 0.3
    stabilizer_tax: float = 0.05


class ExperimentConfig(BaseModel):
    name: str = "experiment"
    description: str = ""
    experiment_type: Literal["generic", "bubble", "cartel", "taxation"] = "generic"
    seed: int = 42
    rounds: int = 50
    round_delay_ms: int = 300
    goods: list[GoodConfig]
    agents: list[AgentGroupConfig]
    market: MarketConfig = Field(default_factory=MarketConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    runs_dir: str = "data/runs"

    @model_validator(mode="after")
    def _check_goods(self) -> "ExperimentConfig":
        ids = [g.id for g in self.goods]
        if len(set(ids)) != len(ids):
            raise ValueError("good ids must be unique")
        known = set(ids)
        for grp in self.agents:
            for field in ("valuations", "costs", "capacity", "inventory"):
                for gid in getattr(grp, field):
                    if gid not in known:
                        raise ValueError(f"agent group references unknown good '{gid}' in {field}")
        for p in self.policy.schedule:
            if p.good is not None and p.good not in known:
                raise ValueError(f"policy schedule references unknown good '{p.good}'")
        return self


def load_config(path: str | Path, overrides: Optional[dict[str, Any]] = None) -> ExperimentConfig:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    if overrides:
        data = deep_merge(data, overrides)
    return ExperimentConfig.model_validate(data)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out
