"""Claude (Anthropic Messages API) provider.

Config example::

    llm:
      provider: anthropic
      model: claude-opus-5-5
      effort: low            # low | medium | high | xhigh | max
      options:
        fallbacks: default   # server-side refusal fallback; set to null to disable
        structured_output: true

Requires ``ANTHROPIC_API_KEY`` (or any credential the Anthropic SDK resolves).
"""

from __future__ import annotations

from typing import Any

import anthropic

from ..config import LLMConfig
from .base import LLMError, LLMProvider, LLMRequest, LLMResponse, register_provider

# Models that accept the server-side ``fallbacks: "default"`` refusal fallback.
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


@register_provider("anthropic")
class AnthropicProvider(LLMProvider):
    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        # Retries are handled by the decision engine so they interact correctly with
        # the cache and the heuristic fallback.
        self.client = anthropic.AsyncAnthropic(max_retries=0, timeout=config.timeout_s)
        self.fallbacks = config.options.get("fallbacks", "default")
        self.structured = bool(config.options.get("structured_output", True))

    async def complete(self, request: LLMRequest) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
        }
        output_config: dict[str, Any] = {}
        if request.effort:
            output_config["effort"] = request.effort
        if self.structured and request.response_schema:
            output_config["format"] = {"type": "json_schema", "schema": request.response_schema}
        if output_config:
            kwargs["output_config"] = output_config
        if request.temperature is not None:
            # Sampling parameters are rejected by current models; only sent when configured.
            kwargs["extra_body"] = {"temperature": request.temperature}

        try:
            if self.fallbacks and request.model in _FALLBACK_MODELS:
                response = await self.client.beta.messages.create(
                    betas=[_FALLBACK_BETA], fallbacks=self.fallbacks, **kwargs
                )
            else:
                response = await self.client.messages.create(**kwargs)
        except anthropic.RateLimitError as exc:
            raise LLMError(f"rate limited: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"connection error: {exc}") from exc

        if response.stop_reason == "refusal":
            raise LLMError("model declined the request (refusal)")
        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise LLMError(f"empty response (stop_reason={response.stop_reason})")
        usage = {
            "input_tokens": getattr(response.usage, "input_tokens", 0),
            "output_tokens": getattr(response.usage, "output_tokens", 0),
        }
        return LLMResponse(text=text, model=response.model, usage=usage)

    async def aclose(self) -> None:
        await self.client.close()
