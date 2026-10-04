"""Host runtime for the full single-page app (web/app.html, built from artifact/).

The app was written for the claude.ai artifact runtime, which gives a page `window.claude.use("db")` (a document
store) and `window.claude.use("sample")` (Claude). When we host it ourselves, a small shim in the page provides the
db from the exported documents, and Claude comes from here:

    GET  /api/runtime/available   -> {"claude": bool, "via": "anthropic" | "gateway" | null}
    POST /api/runtime/sample      {prompt, json: bool, tier: "quick" | null} -> {"text": ...} or {"json": ...}

Claude is reached with ANTHROPIC_API_KEY if set, otherwise through Vercel AI Gateway (AI_GATEWAY_API_KEY, or the
deployment's OIDC token, which Vercel passes on every request). The page only asks Claude to interpret questions and
map columns; every number it shows is computed by code from stored data.
"""
from __future__ import annotations

import json
import os
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/runtime", tags=["runtime"])

GATEWAY_URL = "https://ai-gateway.vercel.sh"
MODELS = {  # (direct Anthropic id, AI Gateway id) per tier
    "default": (os.environ.get("CLAUDE_MODEL", "claude-opus-5-5"), os.environ.get("GATEWAY_MODEL", "anthropic/claude-opus-5.5")),
    "quick": (os.environ.get("CLAUDE_QUICK_MODEL", "claude-haiku-4-5"), os.environ.get("GATEWAY_QUICK_MODEL", "anthropic/claude-haiku-4.5")),
}
_blocked: dict[str, str] = {}   # via -> reason, after the provider refused us (e.g. gateway needs a card on file)
JSON_SYSTEM = "Reply with exactly one JSON value and nothing else: no prose, no code fences."


def _credentials(request: Request) -> tuple[str, str] | None:
    """(via, secret) for the first available way to reach Claude."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic", os.environ["ANTHROPIC_API_KEY"]
    token = (os.environ.get("AI_GATEWAY_API_KEY") or request.headers.get("x-vercel-oidc-token")
             or os.environ.get("VERCEL_OIDC_TOKEN"))
    return ("gateway", token) if token else None


class SampleBody(BaseModel):
    prompt: str
    json: bool = False
    tier: str | None = None


@router.get("/available")
def available(request: Request) -> dict:
    cred = _credentials(request)
    if cred and cred[0] in _blocked:
        return {"claude": False, "via": cred[0], "reason": _blocked[cred[0]]}
    return {"claude": cred is not None, "via": cred[0] if cred else None}


def _error_message(e) -> str:
    try:
        return str(e.body["error"]["message"])[:300]
    except (TypeError, KeyError):
        return str(e.message)[:300]


def _parse_json(text: str):
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"[\[{].*[\]}]", t, re.S)          # tolerate a stray sentence around the JSON
        if m:
            return json.loads(m.group(0))
        raise


@router.post("/sample")
def sample(body: SampleBody, request: Request) -> dict:
    cred = _credentials(request)
    if cred is None:
        raise HTTPException(503, "Claude is not configured on this server.")
    import anthropic

    via, secret = cred
    direct_model, gateway_model = MODELS["quick" if body.tier == "quick" else "default"]
    if via == "anthropic":
        client, model = anthropic.Anthropic(api_key=secret, timeout=60, max_retries=1), direct_model
    else:
        client = anthropic.Anthropic(base_url=GATEWAY_URL, api_key=secret, auth_token=secret, timeout=60, max_retries=1)
        model = gateway_model
    kwargs = {"system": JSON_SYSTEM} if body.json else {}
    try:
        msg = client.messages.create(model=model, max_tokens=4096,
                                     messages=[{"role": "user", "content": body.prompt}], **kwargs)
    except anthropic.APIStatusError as e:
        reason = _error_message(e)
        if e.status_code in (401, 403):                # account/config problem: stop offering Claude to the page
            _blocked[via] = reason
        raise HTTPException(502, f"Claude request failed ({e.status_code}): {reason}")
    except anthropic.APIError:
        raise HTTPException(502, "Claude request failed.")
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    out = {"model": model, "via": via, "truncated": msg.stop_reason == "max_tokens"}
    if body.json:
        try:
            out["json"] = _parse_json(text)
        except (json.JSONDecodeError, ValueError):
            raise HTTPException(502, "Claude did not return valid JSON.")
    else:
        out["text"] = text
    return out
