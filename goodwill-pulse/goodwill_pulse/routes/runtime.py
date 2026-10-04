"""Host runtime for the full single-page app (web/app.html, built from artifact/).

The app was written for the claude.ai artifact runtime, which gives a page `window.claude.use("db")` (a document
store) and `window.claude.use("sample")` (a model). When we host it ourselves, a small shim in the page provides the
db from the exported documents, and the model comes from here:

    GET  /api/runtime/available   -> {"claude": bool, "via": "openai" | null}
    POST /api/runtime/sample      {prompt, json: bool, tier: "quick" | null} -> {"text": ...} or {"json": ...}

The model is OpenAI (ChatGPT) via OPENAI_API_KEY. `available` keeps the `claude` field name because the page shim
in webapp.py reads it. The Ask tab sends each question here to get a spec (measure, grouping, filters, period, chart)
and the related KPIs; every number it shows is computed by code from stored data.

Env:
  OPENAI_API_KEY                 enables the model
  OPENAI_MODEL                   default tier model (default gpt-4o)
  OPENAI_QUICK_MODEL             quick tier model, tried first by the Ask tab (default gpt-4o-mini)
"""
from __future__ import annotations

import json
import os
import re

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/runtime", tags=["runtime"])

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODELS = {
    "default": os.environ.get("OPENAI_MODEL", "gpt-4o"),
    "quick": os.environ.get("OPENAI_QUICK_MODEL", "gpt-4o-mini"),
}
_blocked: dict[str, str] = {}   # via -> reason, after the provider refused us (bad key, no billing)
JSON_SYSTEM = "Reply with exactly one JSON value and nothing else: no prose, no code fences."


def _api_key() -> str | None:
    return os.environ.get("OPENAI_API_KEY") or None


class SampleBody(BaseModel):
    prompt: str
    json: bool = False
    tier: str | None = None


@router.get("/available")
def available() -> dict:
    if not _api_key():
        return {"claude": False, "via": None}
    if "openai" in _blocked:
        return {"claude": False, "via": "openai", "reason": _blocked["openai"]}
    return {"claude": True, "via": "openai"}


def _error_message(r: httpx.Response) -> str:
    try:
        return str(r.json()["error"]["message"])[:300]
    except (ValueError, TypeError, KeyError):
        return r.text[:300]


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
def sample(body: SampleBody) -> dict:
    key = _api_key()
    if key is None:
        raise HTTPException(503, "OpenAI is not configured on this server (set OPENAI_API_KEY).")
    model = MODELS["quick" if body.tier == "quick" else "default"]
    messages = ([{"role": "system", "content": JSON_SYSTEM}] if body.json else []) + [
        {"role": "user", "content": body.prompt}]
    payload = {"model": model, "max_tokens": 4096, "messages": messages}
    if body.json:
        payload["response_format"] = {"type": "json_object"}   # the Ask prompt always asks for one JSON object
    try:
        r = httpx.post(OPENAI_URL, json=payload, headers={"Authorization": f"Bearer {key}"}, timeout=60)
    except httpx.HTTPError:
        raise HTTPException(502, "OpenAI request failed.")
    if r.status_code != 200:
        reason = _error_message(r)
        if r.status_code in (401, 403):                # account/config problem: stop offering the model to the page
            _blocked["openai"] = reason
        raise HTTPException(502, f"OpenAI request failed ({r.status_code}): {reason}")
    choice = r.json()["choices"][0]
    text = choice["message"].get("content") or ""
    out = {"model": model, "via": "openai", "truncated": choice.get("finish_reason") == "length"}
    if body.json:
        try:
            out["json"] = _parse_json(text)
        except (json.JSONDecodeError, ValueError):
            raise HTTPException(502, "OpenAI did not return valid JSON.")
    else:
        out["text"] = text
    return out
