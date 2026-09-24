"""
LLM provider abstraction.

Tries providers in the order configured by settings.llm_primary_provider
(default "gemini", falling back to "openrouter", or vice versa). Falls back
to the next provider on timeout / connection error / HTTP error / rate limit
/ model-unavailable / invalid-response, AND on invalid or truncated JSON in
an otherwise-successful response. Raises LLMAllProvidersFailedError if every
provider fails.
"""
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("llm")

SYSTEM_PROMPT = """You are an expert Next.js + TypeScript + Tailwind CSS developer.
You generate complete, production-quality website codebases.

You MUST respond with ONLY a single valid JSON object and nothing else -
no markdown fences, no commentary, no preamble. The JSON object must have
this exact shape:

{
  "files": [
    {"path": "package.json", "content": "..."},
    {"path": "app/page.tsx", "content": "..."}
  ]
}

Rules:
- Use the Next.js App Router (app/ directory), TypeScript, and Tailwind CSS.
- Always include package.json, tsconfig.json, next.config.js, tailwind.config.js,
  postcss.config.js, app/layout.tsx, app/globals.css, and app/page.tsx at minimum.
- package.json must include "dev" and "build" scripts using next dev / next build,
  a fixed "next", "react", "react-dom" version, and Tailwind + PostCSS devDependencies.
- Never include placeholder comments like "// rest of code here" - always write
  complete file contents.
- Never write outside of a relative project path. Never write .env, secrets,
  or absolute paths.
- Escape all string content correctly so the overall response is valid JSON.
"""


class LLMError(Exception):
    def __init__(self, provider: str, message: str, exc_type: str = "", status_code: Optional[int] = None):
        self.provider = provider
        self.message = message
        self.exc_type = exc_type
        self.status_code = status_code
        super().__init__(f"[{provider}] {exc_type or ''} {status_code or ''}: {message}".strip())


class LLMAllProvidersFailedError(Exception):
    def __init__(self, errors: list[LLMError]):
        self.errors = errors
        detail = "; ".join(str(e) for e in errors)
        super().__init__(f"All LLM providers failed: {detail}")


@dataclass
class LLMResult:
    provider: str
    raw_text: str


def _log_provider_error(provider: str, exc: Exception, status_code: Optional[int] = None):
    logger.error(
        "LLM provider failed | provider=%s | exception_type=%s | status_code=%s | message=%s",
        provider,
        type(exc).__name__,
        status_code,
        str(exc) or "(empty exception message)",
    )


def _extract_json(text: str) -> dict:
    """Extract a JSON object from LLM text output, tolerating markdown fences."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Fallback: find the first "{" and last "}" and try that span
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        span = cleaned[start : end + 1]
        return json.loads(span)  # let caller catch JSONDecodeError
    raise json.JSONDecodeError("No JSON object found in model output", cleaned, 0)


async def _call_openrouter(user_prompt: str) -> LLMResult:
    if not settings.openrouter_api_key:
        raise LLMError("openrouter", "OPENROUTER_API_KEY is not configured", "ConfigError")

    url = f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": settings.model_temperature,
        "max_tokens": settings.model_max_output_tokens,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.model_timeout_seconds) as client:
            resp = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException as e:
        _log_provider_error("openrouter", e)
        raise LLMError("openrouter", "Request timed out", "TimeoutException")
    except httpx.ConnectError as e:
        _log_provider_error("openrouter", e)
        raise LLMError("openrouter", "Connection error", "ConnectError")
    except httpx.HTTPError as e:
        _log_provider_error("openrouter", e)
        raise LLMError("openrouter", str(e) or "HTTP error", "HTTPError")

    if resp.status_code == 429:
        _log_provider_error("openrouter", Exception("rate limited"), resp.status_code)
        raise LLMError("openrouter", "Rate limited", "RateLimit", resp.status_code)
    if resp.status_code == 404:
        _log_provider_error("openrouter", Exception("model unavailable"), resp.status_code)
        raise LLMError("openrouter", f"Model unavailable: {settings.openrouter_model}", "ModelUnavailable", resp.status_code)
    if resp.status_code >= 400:
        _log_provider_error("openrouter", Exception(resp.text[:500]), resp.status_code)
        raise LLMError("openrouter", resp.text[:500] or "HTTP error", "HTTPStatusError", resp.status_code)

    try:
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as e:
        _log_provider_error("openrouter", e, resp.status_code)
        raise LLMError("openrouter", "Invalid response shape from OpenRouter", "InvalidResponse", resp.status_code)

    return LLMResult(provider="openrouter", raw_text=text)


async def _call_gemini(user_prompt: str) -> LLMResult:
    if not settings.gemini_api_key:
        raise LLMError("gemini", "GEMINI_API_KEY is not configured", "ConfigError")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent?key={settings.gemini_api_key}"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "generationConfig": {
            "temperature": settings.model_temperature,
            "maxOutputTokens": settings.model_max_output_tokens,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=settings.model_timeout_seconds) as client:
            resp = await client.post(url, json=body)
    except httpx.TimeoutException as e:
        _log_provider_error("gemini", e)
        raise LLMError("gemini", "Request timed out", "TimeoutException")
    except httpx.ConnectError as e:
        _log_provider_error("gemini", e)
        raise LLMError("gemini", "Connection error", "ConnectError")
    except httpx.HTTPError as e:
        _log_provider_error("gemini", e)
        raise LLMError("gemini", str(e) or "HTTP error", "HTTPError")

    if resp.status_code == 429:
        _log_provider_error("gemini", Exception("rate limited"), resp.status_code)
        raise LLMError("gemini", "Rate limited", "RateLimit", resp.status_code)
    if resp.status_code >= 400:
        _log_provider_error("gemini", Exception(resp.text[:500]), resp.status_code)
        raise LLMError("gemini", resp.text[:500] or "HTTP error", "HTTPStatusError", resp.status_code)

    try:
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, ValueError) as e:
        _log_provider_error("gemini", e, resp.status_code)
        raise LLMError("gemini", "Invalid response shape from Gemini", "InvalidResponse", resp.status_code)

    return LLMResult(provider="gemini", raw_text=text)


async def generate_json(user_prompt: str) -> tuple[dict, str]:
    """
    Call providers in the configured priority order (settings.llm_primary_provider,
    default "gemini", falling back to whichever provider isn't primary).

    Falls back to the next provider not just on network/HTTP errors, but also
    when a provider returns a 200 response whose body isn't valid/complete
    JSON (e.g. a free-tier model truncating output mid-string because it hit
    its own max-output-token cap) - that failure mode looked identical to a
    "successful" call from the caller's point of view, so it used to strand
    generation on a single provider with no fallback at all.

    Returns (parsed_json, provider_name).
    Raises LLMAllProvidersFailedError if every provider fails, for any reason.
    """
    providers = {"openrouter": _call_openrouter, "gemini": _call_gemini}

    primary = settings.llm_primary_provider.strip().lower()
    if primary not in providers:
        logger.warning("Unknown LLM_PRIMARY_PROVIDER '%s', defaulting to 'gemini'", primary)
        primary = "gemini"
    order = [primary] + [name for name in providers if name != primary]

    errors: list[LLMError] = []

    for name in order:
        call_fn = providers[name]
        try:
            result = await call_fn(user_prompt)
        except LLMError as e:
            errors.append(e)
            logger.warning("%s failed (%s)", name, e.exc_type)
            continue

        try:
            parsed = _extract_json(result.raw_text)
        except json.JSONDecodeError as e:
            err = LLMError(name, f"Returned invalid/truncated JSON: {e}", "InvalidJSON")
            errors.append(err)
            _log_provider_error(name, e)
            logger.warning(
                "%s returned invalid/truncated JSON, trying next provider if any", name
            )
            continue

        return parsed, result.provider

    raise LLMAllProvidersFailedError(errors)
