"""LLM abstraction.

FINGuard treats the language model as an optional *narrator*, never as a source
of facts.  Evidence is always assembled from the database first; the model is
then asked to phrase it.  When no provider is configured the platform degrades
to deterministic templates over the same evidence, so every AI surface keeps
working -- it just stops sounding conversational.

Supported providers: any OpenAI-compatible endpoint (including local servers
such as Ollama or vLLM) and the Anthropic messages API.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Hard guardrail prepended to every system prompt.
GROUNDING_RULES = (
    "You are FINGuard's fraud analysis assistant for a regulated financial "
    "institution. Rules you must follow without exception:\n"
    "1. Use ONLY the evidence provided in the user message. Never invent "
    "transactions, amounts, customers, rules, scores or model outputs.\n"
    "2. If the evidence does not answer the question, say exactly what is "
    "missing instead of guessing.\n"
    "3. Quantify claims with the numbers given, and attribute each claim to the "
    "evidence item it came from.\n"
    "4. Never reveal personally identifiable information beyond what appears in "
    "the evidence, and never output credentials or secrets.\n"
    "5. Recommendations are advisory. State that a human analyst decides."
)


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    latency_ms: float
    tokens_in: int = 0
    tokens_out: int = 0
    generated: bool = True  # False when the deterministic fallback produced it
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class LLMClient:
    """Thin, provider-agnostic client. Never raises into the request path."""

    def __init__(self) -> None:
        self.provider = settings.llm_provider
        self.model = settings.llm_model
        self._available = bool(settings.llm_api_key) and self.provider != "none"

    @property
    def available(self) -> bool:
        return self._available

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.provider if self._available else "deterministic",
            "model": self.model if self._available else "rule-based-templates",
            "available": self._available,
            "grounded": True,
            "note": (
                "Language model narrates evidence retrieved from the database."
                if self._available
                else "No LLM configured; deterministic templates render the same evidence."
            ),
        }

    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        if not self._available:
            return LLMResponse(
                text="",
                provider="deterministic",
                model="none",
                latency_ms=0.0,
                generated=False,
                error="no_provider_configured",
            )
        started = time.perf_counter()
        try:
            if self.provider == "anthropic":
                return self._anthropic(system, user, temperature, max_tokens, started)
            return self._openai_compatible(
                system, user, temperature, max_tokens, json_mode, started
            )
        except Exception as exc:
            logger.warning("llm_call_failed", extra={"provider": self.provider, "error": str(exc)})
            return LLMResponse(
                text="",
                provider=self.provider,
                model=self.model,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                generated=False,
                error=str(exc)[:300],
            )

    def _openai_compatible(
        self,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int | None,
        json_mode: bool,
        started: float,
    ) -> LLMResponse:
        base = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": f"{GROUNDING_RULES}\n\n{system}"},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens or settings.llm_max_output_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = httpx.post(
            f"{base}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=settings.llm_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        usage = data.get("usage", {})
        return LLMResponse(
            text=data["choices"][0]["message"]["content"].strip(),
            provider="openai",
            model=data.get("model", self.model),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
        )

    def _anthropic(
        self, system: str, user: str, temperature: float, max_tokens: int | None, started: float
    ) -> LLMResponse:
        base = (settings.llm_base_url or "https://api.anthropic.com/v1").rstrip("/")
        response = httpx.post(
            f"{base}/messages",
            json={
                "model": self.model,
                "system": f"{GROUNDING_RULES}\n\n{system}",
                "messages": [{"role": "user", "content": user}],
                "temperature": temperature,
                "max_tokens": max_tokens or settings.llm_max_output_tokens,
            },
            headers={
                "x-api-key": settings.llm_api_key or "",
                "anthropic-version": "2023-06-01",
            },
            timeout=settings.llm_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        usage = data.get("usage", {})
        text = "".join(block.get("text", "") for block in data.get("content", []))
        return LLMResponse(
            text=text.strip(),
            provider="anthropic",
            model=data.get("model", self.model),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            tokens_in=int(usage.get("input_tokens", 0)),
            tokens_out=int(usage.get("output_tokens", 0)),
        )

    def complete_json(self, *, system: str, user: str) -> tuple[dict[str, Any] | None, LLMResponse]:
        response = self.complete(system=system, user=user, json_mode=True)
        if not response.text:
            return None, response
        text = response.text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[1] if "\n" in text else text
        try:
            return json.loads(text), response
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1]), response
                except json.JSONDecodeError:
                    pass
        return None, response


def sanitise_prompt(text: str, *, max_length: int = 2000) -> str:
    """Strip prompt-injection scaffolding from analyst input before use.

    Analyst questions are data, not instructions: markers that try to close the
    prompt or re-issue system instructions are neutralised, and the result is
    length-bounded.
    """
    cleaned = " ".join((text or "").split())[:max_length]
    for marker in (
        "```",
        "<|im_start|>",
        "<|im_end|>",
        "[system]",
        "[/system]",
        "<system>",
        "</system>",
        "system:",
        "assistant:",
        "ignore previous instructions",
        "ignore all previous",
        "disregard the above",
    ):
        cleaned = cleaned.replace(marker, " ").replace(marker.upper(), " ")
    return cleaned.strip()


llm_client = LLMClient()
