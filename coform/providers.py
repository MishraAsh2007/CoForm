"""Small, opt-in JSON adapters for the three first-party model APIs.

No network activity occurs on import or construction. Model names must be configured
explicitly, so a run records the actual choice rather than a moving default.

Contracts checked against official documentation on 2026-10-30:
https://developers.openai.com/api/docs/guides/structured-outputs
https://platform.claude.com/docs/en/api/messages/create
https://ai.google.dev/api/generate-content
"""
from dataclasses import dataclass, field
import json
import os
import re
import threading
import time
from typing import Any

import httpx


class ProviderError(RuntimeError):
    """Safe-to-log error: excludes credentials, request payloads and response bodies."""

    def __init__(self, message: str, *, input_tokens: int = 0, output_tokens: int = 0, excerpt: str = "",
                 truncated: bool = False):
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        # Start of the model's own reply text (never request data or keys), for diagnosing bad JSON.
        self.excerpt = excerpt[:300]
        # The reply ran into the output-token cap: a shorter answer can succeed where a retry of the same won't.
        self.truncated = truncated


class _CutOff(ValueError):
    """The provider stopped because the reply reached max_output_tokens."""


@dataclass(frozen=True)
class ProviderResponse:
    data: dict
    input_tokens: int
    output_tokens: int


def encode(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def input_token_upper_bound(system: str, payload: dict) -> int:
    """Conservative text-byte reservation; includes a message-framing allowance.

This is deliberately more restrictive than tokens ~= characters/4. Actual usage
from the provider replaces the reservation. It is a local call admission guard,
not a provider-side billing guarantee for arbitrary future tokenizers.
"""
    return len(system.encode("utf-8")) + len(encode(payload).encode("utf-8")) + 256


_NVIDIA_PACE = {"lock": threading.Lock(), "last": 0.0}
# A reply cannot arrive before it is written: allow for the slowest generation seen. NVIDIA's shared
# endpoint dropped from ~80 to 29 tokens/s one night, so 2,900-token drafts timed out at a flat 90 s.
SLOWEST_TOKENS_PER_S = 20


def reply_timeout(base_s: float, max_output_tokens: int) -> float:
    """The configured timeout, raised to cover writing the longest reply allowed at the slowest speed."""
    return max(base_s, 30 + max_output_tokens / SLOWEST_TOKENS_PER_S)


def _json_text(text: str) -> str:
    """Strip reasoning traces and code fences some hosted models wrap around JSON."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    start = text.find("{")
    return text[start:] if start >= 0 else text


def _usage_int(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("Missing or invalid token usage")
    return value


@dataclass
class JSONProvider:
    name: str
    model: str
    api_key: str = field(repr=False)
    timeout_s: float = 45.0
    client: httpx.Client | None = field(default=None, repr=False)

    def __post_init__(self):
        if self.name not in {"openai", "anthropic", "gemini", "nvidia"}:
            raise ValueError("Provider must be openai, anthropic, gemini or nvidia")
        if not self.model or not re.fullmatch(r"[A-Za-z0-9._:/-]+", self.model):
            raise ValueError("Configure an explicit valid model identifier")
        if not self.api_key:
            raise ValueError(f"Missing {self.name} API key")

    def complete_json(self, system: str, payload: dict, max_output_tokens: int = 800) -> ProviderResponse:
        if not 64 <= max_output_tokens <= 4096:
            raise ValueError("Output token cap must be between 64 and 4096")
        system = system + "\nReturn one JSON object only."
        timeout = reply_timeout(self.timeout_s, max_output_tokens)
        body_text = encode(payload)
        headers = {"content-type": "application/json"}
        if self.name == "openai":
            url = "https://api.openai.com/v1/responses"
            headers["Authorization"] = f"Bearer {self.api_key}"
            body = {"model": self.model, "instructions": system, "input": body_text,
                    "max_output_tokens": max_output_tokens, "store": False,
                    "text": {"format": {"type": "json_object"}}}
            if self.model == "gpt-6-luna":
                body["reasoning"] = {"effort": "none"}
        elif self.name == "anthropic":
            url = "https://api.anthropic.com/v1/messages"
            headers.update({"Authorization": f"Bearer {self.api_key}", "anthropic-version": "2026-10-21"})
            workspace = os.getenv("ANTHROPIC_WORKSPACE_ID")
            if workspace:
                headers["anthropic-workspace-id"] = workspace
            body = {"model": self.model, "system": system, "max_tokens": max_output_tokens,
                    "messages": [{"role": "user", "content": body_text}]}
            if self.model == "claude-haiku-5-5":
                body["thinking"] = {"type": "disabled"}
        elif self.name == "nvidia":
            url = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/") + "/chat/completions"
            headers["Authorization"] = f"Bearer {self.api_key}"
            body = {"model": self.model, "max_tokens": max_output_tokens, "temperature": 0.2,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": body_text}]}
            if os.getenv("NVIDIA_NO_THINK", "1") == "1":
                # Verified 2026-10-31 on nemotron-3.5-lightning: 225 -> 16 output tokens for the same JSON.
                body["chat_template_kwargs"] = {"enable_thinking": False}
            # Free-tier rate limits: space calls instead of retrying failures.
            with _NVIDIA_PACE["lock"]:
                wait = float(os.getenv("NVIDIA_MIN_INTERVAL_S", "1.6")) - (time.monotonic() - _NVIDIA_PACE["last"])
                if wait > 0 and self.client is None:
                    time.sleep(wait)
                _NVIDIA_PACE["last"] = time.monotonic()
        else:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            headers["x-goog-api-key"] = self.api_key
            body = {"systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": body_text}]}],
                    "generationConfig": {"maxOutputTokens": max_output_tokens,
                                         "responseMimeType": "application/json"}}
            if self.model == "gemini-2.5-flash-lite":
                body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
        try:
            if self.client is None:
                with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                    response = client.post(url, headers=headers, json=body)
            else:
                response = self.client.post(url, headers=headers, json=body, timeout=timeout)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # Nothing reached the provider (a rate limit would be an HTTP 429 reply instead).
            raise ProviderError(f"could not connect to {self.name} ({type(exc).__name__}): the network or the "
                                "provider's server is unreachable; no retry was made") from None
        except httpx.ReadTimeout:
            raise ProviderError(f"{self.name} did not answer within {timeout:g} s (ReadTimeout); no retry was made") from None
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} request failed ({type(exc).__name__}); no retry was made") from None
        if not 200 <= response.status_code < 300:
            raise ProviderError(f"{self.name} returned HTTP {response.status_code}; check credentials, model and account limits")
        input_tokens = output_tokens = 0
        text = ""
        try:
            raw = response.json()
            if self.name == "openai":
                input_tokens = _usage_int(raw["usage"]["input_tokens"])
                output_tokens = _usage_int(raw["usage"]["output_tokens"])
                if (raw.get("incomplete_details") or {}).get("reason") == "max_output_tokens":
                    raise _CutOff
                if raw.get("status") != "completed":
                    raise ValueError("Incomplete response")
                blocks = [block for item in raw.get("output", []) if item.get("type") == "message"
                          for block in item.get("content", [])]
                if any(block.get("type") == "refusal" for block in blocks):
                    raise ValueError("Refused response")
                text = "".join(block.get("text", "") for block in blocks if block.get("type") == "output_text")
            elif self.name == "nvidia":
                input_tokens = _usage_int(raw["usage"]["prompt_tokens"])
                output_tokens = _usage_int(raw["usage"]["completion_tokens"])
                choice = raw["choices"][0]
                text = _json_text(choice["message"].get("content") or "")  # kept for the excerpt even if incomplete
                if choice.get("finish_reason") == "length":
                    raise _CutOff
                if choice.get("finish_reason") != "stop":
                    raise ValueError("Incomplete response")
            elif self.name == "anthropic":
                usage = raw["usage"]
                input_tokens = _usage_int(usage["input_tokens"]) + _usage_int(usage.get("cache_creation_input_tokens", 0)) + _usage_int(usage.get("cache_read_input_tokens", 0))
                output_tokens = _usage_int(usage["output_tokens"])
                if raw.get("stop_reason") == "max_tokens":
                    raise _CutOff
                if raw.get("stop_reason") != "end_turn":
                    raise ValueError("Incomplete response")
                text = "".join(block.get("text", "") for block in raw.get("content", []) if block.get("type") == "text")
            else:
                usage = raw["usageMetadata"]
                input_tokens = _usage_int(usage["promptTokenCount"])
                total = _usage_int(usage["totalTokenCount"])
                # total includes internal thinking, unlike candidatesTokenCount alone.
                output_tokens = max(0, total - input_tokens)
                candidate = raw["candidates"][0]
                if candidate.get("finishReason") == "MAX_TOKENS":
                    raise _CutOff
                if candidate.get("finishReason") != "STOP":
                    raise ValueError("Incomplete response")
                text = "".join(part.get("text", "") for part in candidate["content"]["parts"] if not part.get("thought", False))
            if self.name == "nvidia":
                # Hosted open models sometimes append text or a stray brace after a complete
                # object; take the first complete JSON value and ignore the remainder.
                value = json.JSONDecoder().raw_decode(text)[0]
            else:
                value = json.loads(text)
            if not isinstance(value, dict):
                raise ValueError("JSON object required")
        except _CutOff:
            raise ProviderError(f"{self.name} reply was cut off at the {max_output_tokens}-token output limit before the JSON ended",
                                input_tokens=input_tokens, output_tokens=output_tokens, excerpt=text, truncated=True) from None
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderError(f"{self.name} returned an incomplete, refused or invalid JSON response",
                                input_tokens=input_tokens, output_tokens=output_tokens, excerpt=text) from None
        return ProviderResponse(value, input_tokens, output_tokens)


def from_environment(provider_name: str, model: str | None = None) -> JSONProvider:
    """Construct a provider; caller loads .env if desired. Never reads or prints keys."""
    name = {"claude": "anthropic", "google": "gemini", "chatgpt": "openai", "nemotron": "nvidia"}.get(provider_name.lower(), provider_name.lower())
    if name not in {"openai", "anthropic", "gemini", "nvidia"}:
        raise ValueError("Provider must be openai, anthropic, gemini or nvidia")
    key = os.getenv(f"{name.upper()}_API_KEY", "")
    if name == "gemini" and not key:
        key = os.getenv("GOOGLE_API_KEY", "")
    selected_model = model or os.getenv(f"{name.upper()}_MODEL", "")
    if not selected_model:
        raise ValueError(f"Set {name.upper()}_MODEL to the exact model available to your API account")
    # Large hosted models answering three concurrent reviews can exceed 45 s.
    provider = JSONProvider(name=name, model=selected_model, api_key=key,
                            timeout_s=float(os.getenv("PROVIDER_TIMEOUT_S", "90")))
    from .budget import BudgetedProvider, guard_from_environment
    guard = guard_from_environment()
    return BudgetedProvider(provider, guard) if guard is not None else provider
