"""Tests for LlmService.

Like the games logic, this previously lived inside a cog and had no tests. Most
of what is worth testing is response-shape normalising: the router fronts
several backends that disagree about where the text lives.
"""
import io
import json
import urllib.error
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from services.llm import LlmService


@contextmanager
def fake_urlopen(payload=None, *, error=None, body: bytes | None = None):
    @contextmanager
    def _open(req, timeout=None, context=None):
        if error is not None:
            raise error
        yield io.BytesIO(body if body is not None else json.dumps(payload).encode())

    with patch("services.llm.urllib.request.urlopen", _open):
        yield


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr("services.llm.config.LLM_API_BASE_URL", "https://llm.example")
    monkeypatch.setattr("services.llm.config.LLM_API_TIMEOUT", 5)
    monkeypatch.setattr("services.llm.config.LLM_CHAT_MODEL", "default-model")
    monkeypatch.setattr("services.llm.config.LLM_API_VERIFY_SSL", True)
    return LlmService()


# --- enablement ------------------------------------------------------------

def test_disabled_without_a_base_url(monkeypatch):
    monkeypatch.setattr("services.llm.config.LLM_API_BASE_URL", "")
    monkeypatch.setattr("services.llm.config.LLM_API_VERIFY_SSL", True)
    assert LlmService().enabled is False


def test_ssl_verification_can_be_disabled(monkeypatch):
    import ssl as ssl_mod

    monkeypatch.setattr("services.llm.config.LLM_API_BASE_URL", "https://llm.example")
    monkeypatch.setattr("services.llm.config.LLM_API_VERIFY_SSL", False)
    svc = LlmService()
    assert svc._ssl_ctx.verify_mode == ssl_mod.CERT_NONE


# --- errors ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_http_error_becomes_a_message(svc):
    err = urllib.error.HTTPError("u", 503, "Unavailable", {}, None)
    with fake_urlopen(error=err):
        models, error = await svc.list_models()
    assert models == []
    assert "503" in error


@pytest.mark.asyncio
async def test_unreachable_router_becomes_a_message(svc):
    with fake_urlopen(error=urllib.error.URLError("refused")):
        _, error = await svc.list_models()
    assert error == "Failed to reach the LLM API."


@pytest.mark.asyncio
async def test_non_json_response_becomes_a_message(svc):
    with fake_urlopen(body=b"<html>gateway</html>"):
        _, error = await svc.list_models()
    assert error == "LLM API returned an invalid response."


# --- models ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_models_accepts_the_models_key(svc):
    with fake_urlopen({"models": [{"id": "b"}, {"id": "a"}]}):
        models, err = await svc.list_models()
    assert (models, err) == (["a", "b"], None)  # sorted


@pytest.mark.asyncio
async def test_list_models_accepts_the_data_key_and_plain_strings(svc):
    with fake_urlopen({"data": ["gpt", {"name": "llama"}, {"model": "qwen"}]}):
        models, _ = await svc.list_models()
    assert models == ["gpt", "llama", "qwen"]


@pytest.mark.asyncio
async def test_list_models_deduplicates(svc):
    with fake_urlopen({"models": [{"id": "a"}, {"id": "a"}]}):
        models, _ = await svc.list_models()
    assert models == ["a"]


@pytest.mark.asyncio
async def test_list_models_is_empty_for_an_unrecognised_shape(svc):
    with fake_urlopen({"unexpected": True}):
        models, err = await svc.list_models()
    assert (models, err) == ([], None)


# --- chat ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_reads_the_ollama_shape(svc):
    with fake_urlopen({"message": {"content": "hello"}}):
        text, model, err = await svc.chat("hi")
    assert (text, model, err) == ("hello", "default-model", None)


@pytest.mark.asyncio
async def test_chat_falls_back_to_the_openai_shape(svc):
    with fake_urlopen({"choices": [{"message": {"content": "hello"}}]}):
        text, _, err = await svc.chat("hi")
    assert (text, err) == ("hello", None)


@pytest.mark.asyncio
async def test_chat_prefers_an_explicit_model_over_the_default(svc):
    with fake_urlopen({"message": {"content": "x"}}):
        _, model, _ = await svc.chat("hi", "specific-model")
    assert model == "specific-model"


@pytest.mark.asyncio
async def test_chat_without_any_model_is_a_caller_error(svc, monkeypatch):
    monkeypatch.setattr("services.llm.config.LLM_CHAT_MODEL", "")
    text, model, err = await svc.chat("hi")
    assert (text, model) == ("", "")
    assert "LLM_CHAT_MODEL" in err


@pytest.mark.asyncio
async def test_chat_reports_an_empty_answer(svc):
    with fake_urlopen({"message": {"content": ""}}):
        _, _, err = await svc.chat("hi")
    assert err == "Empty response from model."


@pytest.mark.asyncio
async def test_chat_does_not_truncate(svc):
    """Truncation is Discord's message limit, not a property of the answer —
    another interface may have room for all of it."""
    long_text = "x" * 5000
    with fake_urlopen({"message": {"content": long_text}}):
        text, _, _ = await svc.chat("hi")
    assert len(text) == 5000


# --- generate --------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_returns_text_and_backend(svc):
    with fake_urlopen({"response": "out", "x-llm-router-backend": "node-1"}):
        text, backend, err = await svc.generate("m", "p")
    assert (text, backend, err) == ("out", "node-1", None)


@pytest.mark.asyncio
async def test_generate_tolerates_a_missing_backend_header(svc):
    with fake_urlopen({"response": "out"}):
        _, backend, err = await svc.generate("m", "p")
    assert (backend, err) == ("", None)


@pytest.mark.asyncio
async def test_generate_reports_an_empty_answer(svc):
    with fake_urlopen({"response": ""}):
        _, _, err = await svc.generate("m", "p")
    assert err == "Empty response from model."


# --- health ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_normalises_backends(svc):
    with fake_urlopen({"backends": [{"name": "n1", "status": "up"}]}):
        data, err = await svc.health()
    assert err is None
    assert data["backends"] == [{"name": "n1", "status": "up"}]


@pytest.mark.asyncio
async def test_health_accepts_the_nodes_key_and_host_state_fields(svc):
    with fake_urlopen({"nodes": [{"host": "h1", "state": "ready"}]}):
        data, _ = await svc.health()
    assert data["backends"] == [{"name": "h1", "status": "ready"}]


@pytest.mark.asyncio
async def test_health_defaults_an_unknown_status(svc):
    with fake_urlopen({"backends": [{"name": "n1"}]}):
        data, _ = await svc.health()
    assert data["backends"][0]["status"] == "unknown"


@pytest.mark.asyncio
async def test_health_hands_back_the_raw_payload_when_there_are_no_backends(svc):
    with fake_urlopen({"ok": True, "version": "1.2"}):
        data, _ = await svc.health()
    assert data["backends"] == []
    assert data["raw"] == {"ok": True, "version": "1.2"}
