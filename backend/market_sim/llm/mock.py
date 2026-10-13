"""Deterministic mock LLM.

It answers by running the agent's heuristic strategy (handed over through
``request.metadata["synthesize"]``) with an RNG seeded from the prompt, and
serialises the result as the same JSON a real model would return. Optional fault
injection makes the retry / fallback path testable::

    llm:
      provider: mock
      options: {failure_rate: 0.1, malformed_rate: 0.05, latency_ms: 20, hang_rate: 0.0}
"""

from __future__ import annotations

import asyncio
import json
import random

from .base import LLMError, LLMProvider, LLMRequest, LLMResponse, register_provider
from ..rng import derive_seed


@register_provider("mock")
class MockProvider(LLMProvider):
    def __init__(self, config) -> None:  # type: ignore[no-untyped-def]
        super().__init__(config)
        opts = config.options
        self.failure_rate = float(opts.get("failure_rate", 0.0))
        self.malformed_rate = float(opts.get("malformed_rate", 0.0))
        self.hang_rate = float(opts.get("hang_rate", 0.0))
        self.latency_ms = float(opts.get("latency_ms", 0.0))
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        fault = random.Random(derive_seed(0, "fault", request.system, request.user, request.attempt)).random()
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000.0)
        if fault < self.hang_rate:
            await asyncio.sleep(self.config.timeout_s * 10)
        if fault < self.hang_rate + self.failure_rate:
            raise LLMError("mock: injected failure")
        if fault < self.hang_rate + self.failure_rate + self.malformed_rate:
            return LLMResponse(text="Sure! I think I'll buy some... {not json", model="mock")

        synthesize = request.metadata.get("synthesize")
        rng = random.Random(derive_seed(0, "answer", request.user))
        if synthesize is None:
            payload = {"reasoning": "No strategy available; holding.", "actions": [{"type": "hold"}]}
        else:
            payload = synthesize(rng)
        return LLMResponse(text=json.dumps(payload, sort_keys=True), model="mock", usage={"input_tokens": 0, "output_tokens": 0})
