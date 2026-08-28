"""LLM router client, independent of how it was invoked.

The non-Discord half of what used to live in `cogs/llm.py`: the HTTP client,
the SSL policy, and the response parsing. The router is tolerant about response
shapes (it fronts several backends), so normalising them is service work — the
cog should not be picking between `message.content` and `choices[0].message`.

What stays in the cog: Discord's 1800-character message limit, embed
formatting, and interaction handling. Truncation in particular is a property of
the transport, not of the model's answer, so it must not happen here — another
interface may have room for the whole thing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import urllib.error
import urllib.request
from typing import Optional

import config

logger = logging.getLogger(__name__)

_MODEL_LIST_LIMIT = 200


class LlmService:
    def __init__(self):
        self._ssl_ctx = ssl.create_default_context()
        if not config.LLM_API_VERIFY_SSL:
            self._ssl_ctx.check_hostname = False
            self._ssl_ctx.verify_mode = ssl.CERT_NONE
            logger.warning("LLM API SSL verification disabled (LLM_API_VERIFY_SSL=0)")

    @property
    def enabled(self) -> bool:
        return bool(config.LLM_API_BASE_URL)

    @property
    def default_model(self) -> str:
        return config.LLM_CHAT_MODEL

    # --- transport ---------------------------------------------------------

    async def _request(
        self, path: str, payload: Optional[dict] = None
    ) -> tuple[Optional[dict], Optional[str]]:
        base_url = config.LLM_API_BASE_URL.rstrip("/")
        url = f"{base_url}/{path.lstrip('/')}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url=url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST" if payload is not None else "GET",
        )
        ssl_ctx = self._ssl_ctx

        def do_request() -> dict:
            with urllib.request.urlopen(
                req, timeout=config.LLM_API_TIMEOUT, context=ssl_ctx
            ) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))

        try:
            return await asyncio.to_thread(do_request), None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            logger.warning("LLM API HTTP error status=%s body=%s", exc.code, raw[:500])
            return None, f"API request failed with status {exc.code}."
        except urllib.error.URLError as exc:
            logger.warning("LLM API connection error: %s", exc)
            return None, "Failed to reach the LLM API."
        except json.JSONDecodeError:
            logger.warning("LLM API returned non-JSON response")
            return None, "LLM API returned an invalid response."

    # --- operations --------------------------------------------------------

    async def list_models(self) -> tuple[list[str], Optional[str]]:
        data, err = await self._request("api/models/all")
        if err:
            return [], err

        items = []
        if isinstance(data, dict):
            for key in ("models", "data"):
                if isinstance(data.get(key), list):
                    items = data[key]
                    break

        model_ids: list[str] = []
        for item in items:
            if isinstance(item, dict):
                mid = item.get("id") or item.get("name") or item.get("model")
            else:
                mid = item
            if isinstance(mid, str):
                model_ids.append(mid)
        return sorted(set(model_ids))[:_MODEL_LIST_LIMIT], None

    async def chat(
        self, message: str, model: Optional[str] = None
    ) -> tuple[str, str, Optional[str]]:
        """Returns (text, resolved_model, error)."""
        resolved = model or self.default_model
        if not resolved:
            return "", "", "No model specified and LLM_CHAT_MODEL is not set."

        data, err = await self._request(
            "api/chat",
            {
                "model": resolved,
                "messages": [{"role": "user", "content": message}],
                "stream": False,
            },
        )
        if err:
            return "", resolved, err

        # The router fronts several backends, which disagree on response shape.
        text = ""
        if isinstance(data, dict):
            msg = data.get("message", {})
            if isinstance(msg, dict):
                text = msg.get("content", "") or ""
            if not text:
                choices = data.get("choices", [])
                if choices and isinstance(choices[0], dict):
                    text = (choices[0].get("message") or {}).get("content", "") or ""

        if not text:
            return "", resolved, "Empty response from model."
        return text, resolved, None

    async def generate(
        self, model: str, prompt: str
    ) -> tuple[str, str, Optional[str]]:
        """Returns (text, backend, error). `backend` may be empty."""
        data, err = await self._request(
            "api/generate", {"model": model, "prompt": prompt, "stream": False}
        )
        if err:
            return "", "", err

        text = data.get("response", "") if isinstance(data, dict) else ""
        if not text:
            return "", "", "Empty response from model."
        backend = data.get("x-llm-router-backend", "") if isinstance(data, dict) else ""
        return text, backend, None

    async def health(self) -> tuple[Optional[dict], Optional[str]]:
        """Returns a normalised {"backends": [{"name", "status"}, ...]} when the
        router reports backends, or the raw payload under "raw" when it does
        not, so callers do not each re-guess the shape."""
        data, err = await self._request("health")
        if err:
            return None, err
        if not isinstance(data, dict):
            return {"backends": [], "raw": {}}, None

        raw_backends = data.get("backends") or data.get("nodes") or []
        backends = []
        for b in raw_backends:
            if isinstance(b, dict):
                backends.append({
                    "name": b.get("name") or b.get("host") or str(b),
                    "status": b.get("status") or b.get("state") or "unknown",
                })
        if backends:
            return {"backends": backends, "raw": {}}, None
        return {"backends": [], "raw": data}, None
