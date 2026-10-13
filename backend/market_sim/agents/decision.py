"""Concurrent LLM decision runner with caching, retries, timeouts and heuristic fallback.

Results are returned in task order regardless of completion order, and fallback
heuristics use RNGs derived from (seed, round, agent), so concurrency never affects
the outcome of a run.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from ..config import LLMConfig
from ..llm.base import LLMProvider, LLMRequest
from ..llm.cache import ResponseCache, cache_key
from .actions import ParseError, extract_json, parse_decision

Kind = Literal["trade", "message", "policy"]
Source = Literal["llm", "cache", "heuristic", "fallback"]

MESSAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"message": {"type": "string"}},
    "required": ["message"],
    "additionalProperties": False,
}


@dataclass
class DecisionTask:
    agent_id: str
    kind: Kind
    use_llm: bool
    system: str
    user: str
    heuristic: Callable[[random.Random], dict[str, Any]]
    rng_seed: int
    schema: Optional[dict[str, Any]] = None


@dataclass
class DecisionOutcome:
    agent_id: str
    kind: Kind
    payload: dict[str, Any]
    source: Source
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    raw: Optional[str] = None


@dataclass
class DecisionStats:
    llm_calls: int = 0
    llm_failures: int = 0
    fallbacks: int = 0
    cache_hits: int = 0
    heuristic: int = 0
    total_latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["avg_latency_ms"] = round(1000 * self.total_latency_s / self.llm_calls, 1) if self.llm_calls else 0.0
        d.pop("total_latency_s")
        return d


def parse_payload(kind: Kind, text: str) -> dict[str, Any]:
    if kind == "message":
        data = extract_json(text)
        if not isinstance(data, dict) or not isinstance(data.get("message", ""), str):
            raise ParseError("expected {\"message\": string}")
        return {"message": data.get("message", ""), "reasoning": ""}
    d = parse_decision(text)
    return {"reasoning": d.reasoning, "actions": d.actions, "message": d.message}


class DecisionEngine:
    def __init__(self, config: LLMConfig, provider: LLMProvider, cache: Optional[ResponseCache] = None) -> None:
        self.config = config
        self.provider = provider
        self.cache = cache if config.cache.mode != "off" else None
        self._sem = asyncio.Semaphore(max(1, config.max_concurrency))
        self.stats = DecisionStats()

    async def run(self, tasks: list[DecisionTask]) -> list[DecisionOutcome]:
        # A fresh semaphore per batch keeps the engine usable across event loops.
        self._sem = asyncio.Semaphore(max(1, self.config.max_concurrency))
        return list(await asyncio.gather(*(self._run_one(t) for t in tasks)))

    def _heuristic(self, task: DecisionTask, source: Source, errors: list[str], attempts: int) -> DecisionOutcome:
        payload = task.heuristic(random.Random(task.rng_seed))
        if source == "fallback":
            self.stats.fallbacks += 1
        else:
            self.stats.heuristic += 1
        return DecisionOutcome(task.agent_id, task.kind, payload, source, attempts, errors)

    async def _run_one(self, task: DecisionTask) -> DecisionOutcome:
        if not task.use_llm:
            return self._heuristic(task, "heuristic", [], 0)

        cfg = self.config
        request = LLMRequest(
            system=task.system, user=task.user, model=cfg.model, max_tokens=cfg.max_tokens,
            temperature=cfg.temperature, effort=cfg.effort, response_schema=task.schema,
            metadata={"synthesize": task.heuristic, "agent_id": task.agent_id, "kind": task.kind},
        )
        key = cache_key(self.provider.name, request) if self.cache is not None else None
        errors: list[str] = []

        if self.cache is not None and key and cfg.cache.mode in ("replay", "strict_replay"):
            cached = self.cache.get(key)
            if cached is not None:
                try:
                    payload = parse_payload(task.kind, cached)
                    self.stats.cache_hits += 1
                    return DecisionOutcome(task.agent_id, task.kind, payload, "cache", 0, [], cached)
                except ParseError as exc:
                    errors.append(f"cached response unparseable: {exc}")
            if cfg.cache.mode == "strict_replay":
                errors.append("cache miss in strict replay mode")
                return self._heuristic(task, "fallback", errors, 0)

        attempts = 0
        for attempt in range(cfg.max_retries + 1):
            attempts = attempt + 1
            request.attempt = attempt
            try:
                async with self._sem:
                    t0 = time.perf_counter()
                    self.stats.llm_calls += 1
                    try:
                        response = await asyncio.wait_for(self.provider.complete(request), timeout=cfg.timeout_s)
                    finally:
                        self.stats.total_latency_s += time.perf_counter() - t0
                self.stats.input_tokens += int(response.usage.get("input_tokens", 0) or 0)
                self.stats.output_tokens += int(response.usage.get("output_tokens", 0) or 0)
                payload = parse_payload(task.kind, response.text)
                if self.cache is not None and key and cfg.cache.mode in ("record", "replay"):
                    self.cache.put(key, response.text, response.model)
                return DecisionOutcome(task.agent_id, task.kind, payload, "llm", attempts, errors, response.text)
            except asyncio.TimeoutError:
                errors.append(f"attempt {attempts}: timeout after {cfg.timeout_s}s")
            except ParseError as exc:
                errors.append(f"attempt {attempts}: unparseable response ({exc})")
            except Exception as exc:  # noqa: BLE001 - any provider failure triggers retry/fallback
                errors.append(f"attempt {attempts}: {type(exc).__name__}: {exc}")
            self.stats.llm_failures += 1
            if attempt < cfg.max_retries and cfg.retry_backoff_s > 0:
                await asyncio.sleep(cfg.retry_backoff_s * (2 ** attempt))
        return self._heuristic(task, "fallback", errors, attempts)
