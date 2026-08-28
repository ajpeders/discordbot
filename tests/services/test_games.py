"""Tests for GamesService.

The game-server logic previously lived inside a Discord cog and had no tests at
all, because reaching it meant constructing an Interaction. Extracting the
service made it directly testable.
"""
import io
import json
import urllib.error
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest

from services.games import GamesService


@contextmanager
def fake_urlopen(payload=None, *, error=None):
    """Patch the urlopen used by the service with a canned response."""
    @contextmanager
    def _open(req, timeout=None):
        if error is not None:
            raise error
        yield io.BytesIO(json.dumps(payload).encode())

    with patch("services.games.urllib.request.urlopen", _open):
        yield


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr("services.games.config.POWER_URL", "http://power:8080")
    monkeypatch.setattr("services.games.config.POWER_SERVICE", "palworld")
    monkeypatch.setattr("services.games.config.POWER_TIMEOUT", 5)
    return GamesService(data_dir=str(tmp_path))


# --- enablement ------------------------------------------------------------

def test_disabled_without_a_power_url(tmp_path, monkeypatch):
    monkeypatch.setattr("services.games.config.POWER_URL", "")
    assert GamesService(data_dir=str(tmp_path)).enabled is False


def test_enabled_with_a_power_url(svc):
    assert svc.enabled is True


# --- power control ---------------------------------------------------------

@pytest.mark.asyncio
async def test_is_running_reads_the_sidecar(svc):
    with fake_urlopen({"running": True}):
        running, err = await svc.is_running()
    assert (running, err) == (True, None)


@pytest.mark.asyncio
async def test_is_running_reports_an_unreachable_sidecar(svc):
    with fake_urlopen(error=urllib.error.URLError("refused")):
        running, err = await svc.is_running()
    assert running is None
    assert "unreachable" in err


@pytest.mark.asyncio
async def test_a_sidecar_http_error_becomes_a_message_not_an_exception(svc):
    err502 = urllib.error.HTTPError("u", 502, "Bad Gateway", {}, None)
    with fake_urlopen(error=err502):
        data, err = await svc.status()
    assert data is None
    assert "502" in err


@pytest.mark.asyncio
async def test_request_start_returns_none_on_success(svc):
    with fake_urlopen({"ok": True}):
        assert await svc.request_start() is None


@pytest.mark.asyncio
async def test_wait_until_running_gives_up_after_the_attempt_budget(svc):
    with patch.object(svc, "is_running", AsyncMock(return_value=(False, None))), \
         patch("services.games.asyncio.sleep", AsyncMock()):
        assert await svc.wait_until_running(attempts=3, interval=0) is False


@pytest.mark.asyncio
async def test_wait_until_running_succeeds_once_the_container_reports_up(svc):
    with patch.object(svc, "is_running", AsyncMock(side_effect=[(False, None), (True, None)])), \
         patch("services.games.asyncio.sleep", AsyncMock()):
        assert await svc.wait_until_running(attempts=5, interval=0) is True


# --- start guard -----------------------------------------------------------

def test_only_one_caller_can_hold_the_start_slot(svc):
    """The guard is on the service, so a Discord start and a web start cannot
    both POST during the ~90s boot window."""
    assert svc.begin_start() is True
    assert svc.begin_start() is False
    assert svc.starting is True

    svc.end_start()
    assert svc.starting is False
    assert svc.begin_start() is True


# --- players ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_players_returns_the_roster(svc, monkeypatch):
    monkeypatch.setattr("services.games.config.PALWORLD_REST_URL", "http://pal:8212")
    monkeypatch.setattr("services.games.config.PALWORLD_ADMIN_PASSWORD", "pw")

    with fake_urlopen({"players": [{"name": "alex", "level": 12}]}):
        people, err = await svc.players()

    assert err is None
    assert people[0]["name"] == "alex"


@pytest.mark.asyncio
async def test_players_treats_an_unreachable_rest_api_as_still_starting(svc, monkeypatch):
    monkeypatch.setattr("services.games.config.PALWORLD_REST_URL", "http://pal:8212")
    monkeypatch.setattr("services.games.config.PALWORLD_ADMIN_PASSWORD", "pw")

    with fake_urlopen(error=OSError("connection refused")):
        people, err = await svc.players()

    assert people is None
    assert "starting up" in err


def test_players_are_unconfigured_without_credentials(svc, monkeypatch):
    monkeypatch.setattr("services.games.config.PALWORLD_REST_URL", "")
    assert svc.players_configured is False


# --- connect address -------------------------------------------------------

@pytest.mark.asyncio
async def test_connect_address_uses_the_live_public_ip(svc, monkeypatch):
    monkeypatch.setattr("services.games.config.PALWORLD_CONNECT_PORT", "8211")

    @contextmanager
    def _open(req, timeout=None):
        yield io.BytesIO(b"203.0.113.9\n")

    with patch("services.games.urllib.request.urlopen", _open):
        assert await svc.connect_address() == "203.0.113.9:8211"


@pytest.mark.asyncio
async def test_connect_address_falls_back_when_the_lookup_fails(svc, monkeypatch):
    """Must be a literal address either way — Palworld's client rejects
    hostnames in the Join with IP box."""
    monkeypatch.setattr("services.games.config.PALWORLD_CONNECT_PORT", "8211")
    monkeypatch.setattr("services.games.config.PALWORLD_CONNECT_HOST", "198.51.100.4")

    with fake_urlopen(error=OSError("dns")):
        assert await svc.connect_address() == "198.51.100.4:8211"


# --- allowlist -------------------------------------------------------------

def test_allowlist_starts_empty(svc):
    assert svc.load_allowed() == set()
    assert svc.is_allowed(123) is False


def test_allow_then_deny_round_trips(svc):
    assert svc.allow(123) is True
    assert svc.is_allowed(123) is True
    assert svc.is_allowed("123") is True  # id type must not matter

    assert svc.deny(123) is True
    assert svc.is_allowed(123) is False


def test_allow_is_idempotent(svc):
    assert svc.allow(123) is True
    assert svc.allow(123) is False


def test_deny_reports_when_there_was_nothing_to_revoke(svc):
    assert svc.deny(999) is False


def test_allowlist_survives_a_new_service_instance(tmp_path, svc):
    svc.allow(42)
    assert GamesService(data_dir=str(tmp_path)).is_allowed(42) is True


def test_a_corrupt_allowlist_denies_rather_than_grants(svc):
    """Failing open here would hand server control to everyone."""
    with open(svc._allow_file(), "w") as fh:
        fh.write("{not json")

    assert svc.load_allowed() == set()
    assert svc.is_allowed(123) is False


# --- ensure_started (the non-blocking path used by HTTP) --------------------

@pytest.mark.asyncio
async def test_ensure_started_reports_already_running(svc):
    with patch.object(svc, "is_running", AsyncMock(return_value=(True, None))):
        state, err = await svc.ensure_started()
    assert (state, err) == ("running", None)
    # Nothing was left holding the guard.
    assert svc.starting is False


@pytest.mark.asyncio
async def test_ensure_started_posts_and_hands_the_guard_to_the_watcher(svc):
    with patch.object(svc, "is_running", AsyncMock(return_value=(False, None))), \
         patch.object(svc, "request_start", AsyncMock(return_value=None)), \
         patch.object(svc, "wait_until_running", AsyncMock(return_value=True)):
        state, err = await svc.ensure_started()
        assert (state, err) == ("starting", None)
        # Held for the boot window, not released when the POST returns.
        assert svc.starting is True
        await svc._boot_task
    assert svc.starting is False


@pytest.mark.asyncio
async def test_ensure_started_is_busy_while_a_start_is_in_flight(svc):
    svc.begin_start()
    state, err = await svc.ensure_started()
    assert (state, err) == ("busy", None)


@pytest.mark.asyncio
async def test_ensure_started_releases_the_guard_when_the_sidecar_fails(svc):
    """A failed start must not wedge the guard and block every later attempt."""
    with patch.object(svc, "is_running", AsyncMock(return_value=(None, "unreachable"))):
        state, err = await svc.ensure_started()
    assert state == "error"
    assert err == "unreachable"
    assert svc.starting is False


@pytest.mark.asyncio
async def test_ensure_started_releases_the_guard_when_the_post_fails(svc):
    with patch.object(svc, "is_running", AsyncMock(return_value=(False, None))), \
         patch.object(svc, "request_start", AsyncMock(return_value="boom")):
        state, err = await svc.ensure_started()
    assert (state, err) == ("error", "boom")
    assert svc.starting is False


@pytest.mark.asyncio
async def test_the_guard_is_released_even_if_the_boot_watch_raises(svc):
    with patch.object(svc, "is_running", AsyncMock(return_value=(False, None))), \
         patch.object(svc, "request_start", AsyncMock(return_value=None)), \
         patch.object(svc, "wait_until_running", AsyncMock(side_effect=RuntimeError("x"))):
        await svc.ensure_started()
        with pytest.raises(RuntimeError):
            await svc._boot_task
    assert svc.starting is False
