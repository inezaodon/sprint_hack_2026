"""Claude client factory shared by every AI feature.

Every feature calls `ai_available()` first and uses its deterministic fallback when it is False, so the app works
with no ANTHROPIC_API_KEY. Tests inject a fake with `set_client(fake)`; nothing here touches the network on import.

Env:
  ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN   enables the Claude path
  GOODWILL_AI=off                            forces the fallback path even when a key exists
  GOODWILL_AI_TIMEOUT                        per-request timeout in seconds (default 30)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger("goodwill_pulse.ai")

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"   # `fallbacks: "default"`: refused requests re-run server-side
DEFAULT_TIMEOUT = float(os.environ.get("GOODWILL_AI_TIMEOUT", "30"))
MAX_RETRIES = 1                                      # one retry (SDK retries 408/409/429/5xx + connection errors)

_override: Any = None
_client: Any = None


class AIUnavailable(RuntimeError):
    """Raised when the Claude path can't produce an answer; callers then use their fallback."""


def set_client(client: Any) -> None:
    """Inject a client (tests use a fake). `None` restores the real lazy client."""
    global _override, _client
    _override = client
    _client = None


def ai_available() -> bool:
    if _override is not None:
        return True
    if os.environ.get("GOODWILL_AI", "").lower() in ("off", "0", "false", "no"):
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def get_client() -> Any:
    """The lazily built Anthropic client (or the injected fake)."""
    global _client
    if _override is not None:
        return _override
    if _client is None:
        import anthropic   # imported lazily: the fallback path never needs it
        _client = anthropic.Anthropic(timeout=DEFAULT_TIMEOUT, max_retries=MAX_RETRIES)
    return _client


def cached_system(text: str) -> list[dict]:
    """A stable system prompt as a cacheable block (keep volatile content out of it)."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def create(feature: str, **kwargs: Any) -> Any:
    """One Messages API call with the team defaults. Raises AIUnavailable on any API failure or refusal."""
    if not ai_available():
        raise AIUnavailable("no credentials")
    params = {"model": MODEL, "max_tokens": 4096, "betas": [FALLBACK_BETA], "fallbacks": "default", **kwargs}
    try:
        resp = get_client().beta.messages.create(**params)
    except Exception as e:   # anthropic.APIError subclasses, timeouts, connection errors, fake-client errors
        log.warning("ai[%s] claude call failed: %s: %s", feature, type(e).__name__, e)
        raise AIUnavailable(str(e)) from e
    if getattr(resp, "stop_reason", None) == "refusal":
        log.warning("ai[%s] claude refused", feature)
        raise AIUnavailable("refusal")
    usage = getattr(resp, "usage", None)
    if usage is not None:
        log.info("ai[%s] usage in=%s out=%s cache_read=%s", feature, getattr(usage, "input_tokens", None),
                 getattr(usage, "output_tokens", None), getattr(usage, "cache_read_input_tokens", None))
    return resp


def text_of(resp: Any) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


def json_of(resp: Any) -> dict:
    """Parse a structured-output response (output_config.format json_schema)."""
    try:
        return json.loads(text_of(resp))
    except (json.JSONDecodeError, TypeError) as e:
        raise AIUnavailable(f"bad JSON from model: {e}") from e


def json_schema(schema: dict) -> dict:
    return {"format": {"type": "json_schema", "schema": schema}}


def log_engine(feature: str, engine: str, detail: str = "") -> None:
    log.info("ai[%s] answered by %s%s", feature, engine, f" ({detail})" if detail else "")
