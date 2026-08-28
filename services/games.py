"""Game-server control, independent of how it was invoked.

This is the non-Discord half of what used to live entirely in `cogs/games.py`:
the dashboard-power client, the Palworld REST queries, the access allowlist,
and the in-flight start guard. The cog keeps only permission checks (which need
a Discord identity) and message formatting.

Security notes carried over from the cog, because they are the reason this is
shaped the way it is:

* **No docker.sock.** The bot executes YouTube and LLM input, so giving it the
  socket would give it the host. It calls the `dashboard-power` sidecar, which
  owns the socket, is allowlist-gated, and sits on a private network.
* **Start only, no stop.** The homelab's idle timer already stops the server
  correctly. A stop verb would just be a way for anyone to kill a live session.
* **One container.** `config.POWER_SERVICE` is a fixed name, never a
  caller-supplied argument, so this cannot reach minecraft, the VMs, or
  portainer even though the sidecar's allowlist includes them.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Optional

import config

logger = logging.getLogger(__name__)

# The server answers RCON within a few seconds of the container running, but is
# not joinable until the world finishes loading — and a boot that pulls a game
# update can take minutes. Poll a bounded while, then hand back with a caveat
# rather than blocking the caller indefinitely.
POLL_INTERVAL_S = 5
POLL_ATTEMPTS = 18  # ~90s


class GamesService:
    def __init__(self, data_dir: Optional[str] = None):
        self._data_dir = data_dir if data_dir is not None else config.DATA_DIR
        # A start takes up to ~90s of polling, and several people can invoke it
        # inside that window — now from different interfaces, not just Discord.
        # Set-and-checked with no await in between, so on a single event loop
        # the pair is atomic and needs no lock. Held until the poll loop ends,
        # not merely until the API call returns, because the container reaching
        # `running` is not the end of the boot.
        self._starting = False
        # Strong reference to the background boot watcher; asyncio only keeps a
        # weak one, so without this the task can be collected mid-flight and
        # the start guard would never be released.
        self._boot_task: Optional[asyncio.Task] = None

    @property
    def enabled(self) -> bool:
        """False disables every game command; POWER_URL unset means no sidecar."""
        return bool(config.POWER_URL)

    @property
    def starting(self) -> bool:
        return self._starting

    # --- dashboard-power client -------------------------------------------

    def _url(self, path: str) -> str:
        return f"{config.POWER_URL.rstrip('/')}/{path.lstrip('/')}"

    async def _call(self, path: str, method: str = "GET") -> tuple[Optional[dict], Optional[str]]:
        """Returns (payload, error). Blocking urllib is pushed off the loop."""
        req = urllib.request.Request(url=self._url(path), method=method)

        def do_request() -> dict:
            with urllib.request.urlopen(req, timeout=config.POWER_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))

        try:
            return await asyncio.to_thread(do_request), None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            logger.warning("power API HTTP %s on %s: %s", exc.code, path, raw[:300])
            return None, f"the power service returned HTTP {exc.code}"
        except urllib.error.URLError as exc:
            logger.warning("power API unreachable on %s: %r", path, exc.reason)
            return None, "the power service is unreachable"
        except Exception:
            logger.exception("power API call failed on %s", path)
            return None, "the power service call failed"

    async def status(self) -> tuple[Optional[dict], Optional[str]]:
        return await self._call(f"/power/{config.POWER_SERVICE}")

    async def is_running(self) -> tuple[Optional[bool], Optional[str]]:
        data, err = await self.status()
        if err:
            return None, err
        return bool(data.get("running")), None

    async def request_start(self) -> Optional[str]:
        """POST the start. Returns an error message, or None on success."""
        _, err = await self._call(f"/power/{config.POWER_SERVICE}/start", method="POST")
        return err

    async def wait_until_running(
        self, *, attempts: int = POLL_ATTEMPTS, interval: float = POLL_INTERVAL_S
    ) -> bool:
        """Poll until the container reports running. False means it timed out,
        which in practice usually means a game update is downloading."""
        for _ in range(attempts):
            await asyncio.sleep(interval)
            running, err = await self.is_running()
            if err:
                continue
            if running:
                return True
        return False

    def begin_start(self) -> bool:
        """Claim the in-flight start slot. False means someone already holds it.

        Lives here rather than in an interface so a Discord `/palworld start`
        and a web-triggered start cannot both POST at once.
        """
        if self._starting:
            return False
        self._starting = True
        return True

    def end_start(self) -> None:
        self._starting = False

    async def ensure_started(self) -> tuple[str, Optional[str]]:
        """Idempotent start that does not block for the whole boot.

        Returns (state, error) where state is one of:
          "running"  — already up, nothing to do
          "starting" — start posted; the boot window is tracked in background
          "busy"     — another caller is mid-start
          "error"    — see the error message

        Written for callers that cannot sit on a 90s poll (an HTTP request),
        who should poll `status()` afterwards. The Discord command narrates
        progress inline instead and drives the steps itself.
        """
        if not self.begin_start():
            return "busy", None
        release_guard = True
        try:
            running, err = await self.is_running()
            if err:
                return "error", err
            if running:
                return "running", None
            err = await self.request_start()
            if err:
                return "error", err
            # Hand the guard to the watcher, which holds it for the boot window.
            self._boot_task = asyncio.ensure_future(self._watch_boot())
            release_guard = False
            return "starting", None
        finally:
            if release_guard:
                self.end_start()

    async def _watch_boot(self) -> None:
        try:
            await self.wait_until_running()
        finally:
            self.end_start()

    # --- palworld queries --------------------------------------------------

    @property
    def players_configured(self) -> bool:
        return bool(config.PALWORLD_REST_URL and config.PALWORLD_ADMIN_PASSWORD)

    async def players(self) -> tuple[Optional[list[dict]], Optional[str]]:
        """Roster from Palworld's own REST API.

        Read directly rather than through dashboard-power, which is
        deliberately a start/stop-only surface and should not grow a query
        passthrough.
        """
        auth = base64.b64encode(
            f"admin:{config.PALWORLD_ADMIN_PASSWORD}".encode()
        ).decode()
        req = urllib.request.Request(
            f"{config.PALWORLD_REST_URL.rstrip('/')}/v1/api/players",
            headers={"Authorization": "Basic " + auth},
        )

        def do_request() -> dict:
            with urllib.request.urlopen(req, timeout=config.POWER_TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))

        try:
            data = await asyncio.to_thread(do_request)
        except Exception as exc:
            # Most likely the world is still loading — the container is up well
            # before the REST API answers.
            logger.warning("palworld REST players failed: %r", exc)
            return None, "the server may still be starting up"
        return list(data.get("players") or []), None

    async def connect_address(self) -> str:
        """`ip:port` for the client's "Join with IP" box.

        Palworld's client rejects hostnames there, so this must be a literal
        address. It is looked up live on every call so it survives an ISP
        rotation instead of going quietly stale — and the bot cannot resolve
        the hostname itself, because it resolves through AdGuard, which
        wildcards *.example.com to a LAN address.
        """
        def _public_ip() -> str:
            req = urllib.request.Request(
                "https://ifconfig.me/ip", headers={"User-Agent": "curl/8"}
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                return r.read().decode().strip()

        try:
            ip = await asyncio.to_thread(_public_ip)
        except Exception as exc:
            logger.warning("public IP lookup failed: %r", exc)
            ip = config.PALWORLD_CONNECT_HOST  # configured fallback
        return f"{ip}:{config.PALWORLD_CONNECT_PORT}"

    # --- access allowlist --------------------------------------------------
    # Persisted in DATA_DIR so it survives restarts and redeploys, and editable
    # from Discord itself — granting access should not need a shell on the
    # homelab.
    #
    # NOTE: DATA_DIR is the bot's own volume, which is NOT covered by the
    # nightly backup. Losing it costs the allowlist, not the server.

    def _allow_file(self) -> str:
        return os.path.join(self._data_dir, "power-allowlist.json")

    def load_allowed(self) -> set[str]:
        try:
            with open(self._allow_file(), encoding="utf-8") as fh:
                return {str(u) for u in (json.load(fh).get("users") or [])}
        except FileNotFoundError:
            return set()
        except Exception:
            # A corrupt file must not hand out access, nor brick the command.
            logger.exception("allowlist unreadable — treating as empty")
            return set()

    def save_allowed(self, users: set[str]) -> None:
        path = self._allow_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"users": sorted(users)}, fh, indent=2)
        os.replace(tmp, path)  # atomic; a torn write would lock everyone out

    def is_allowed(self, user_id) -> bool:
        return str(user_id) in self.load_allowed()

    def allow(self, user_id) -> bool:
        """Grant access. False means they already had it."""
        users = self.load_allowed()
        if str(user_id) in users:
            return False
        users.add(str(user_id))
        self.save_allowed(users)
        return True

    def deny(self, user_id) -> bool:
        """Revoke access. False means they did not have it."""
        users = self.load_allowed()
        if str(user_id) not in users:
            return False
        users.discard(str(user_id))
        self.save_allowed(users)
        return True
