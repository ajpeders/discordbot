import { useCallback, useEffect, useRef, useState } from "react";
import {
  getGameConnect,
  getGamePlayers,
  getGamesStatus,
  startGameServer,
  type GamePlayer,
  type GamesStatus,
} from "../api/bot";

const CARD = "rounded-xl border border-border bg-bg-elev p-6";
const CARD_LABEL =
  "text-[10px] font-semibold uppercase tracking-[0.18em] text-dim";
const BTN =
  "rounded-md px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40";
const BTN_PRIMARY = `${BTN} bg-accent text-black hover:bg-accent-hover`;
const BTN_GHOST = `${BTN} border border-border text-text hover:border-accent hover:text-accent`;

// The container reports "running" well before the world finishes loading, and a
// boot that pulls a game update takes minutes — so poll faster while a start is
// in flight and back off once it settles.
const POLL_STARTING_MS = 5000;
const POLL_IDLE_MS = 30000;

interface Connect {
  address: string;
  password: string | null;
  server_name: string | null;
}

export default function GamesPage() {
  const [status, setStatus] = useState<GamesStatus | null>(null);
  const [players, setPlayers] = useState<GamePlayer[] | null>(null);
  const [connect, setConnect] = useState<Connect | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const refresh = useCallback(async () => {
    try {
      const s = await getGamesStatus();
      setStatus(s);
      setError(null);
      if (s.enabled && s.running && s.players_configured) {
        try {
          setPlayers((await getGamePlayers()).players);
        } catch {
          // The REST API answers later than the container does; an empty
          // roster here is not worth surfacing as a page-level error.
          setPlayers(null);
        }
      } else {
        setPlayers(null);
      }
      return s;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return null;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      const s = await refresh();
      if (cancelled) return;
      const delay = s?.starting ? POLL_STARTING_MS : POLL_IDLE_MS;
      timer.current = window.setTimeout(tick, delay);
    }

    tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer.current);
    };
  }, [refresh]);

  async function start() {
    setBusy(true);
    setMsg(null);
    setError(null);
    try {
      const { state } = await startGameServer();
      setMsg(
        state === "running"
          ? "Already up — jump in."
          : "Starting… this can take a few minutes if a game update is downloading.",
      );
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function showConnect() {
    setBusy(true);
    setError(null);
    try {
      setConnect(await getGameConnect());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function copyAddress() {
    if (!connect) return;
    try {
      await navigator.clipboard.writeText(connect.address);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Couldn't copy — select the address and copy it manually.");
    }
  }

  if (status && !status.enabled) {
    return (
      <div className={CARD}>
        <h2 className={CARD_LABEL}>Game server</h2>
        <p className="mt-3 text-sm text-muted">
          Server control isn't configured on this bot. Set{" "}
          <code className="text-text">POWER_URL</code> to enable it.
        </p>
      </div>
    );
  }

  const running = status?.running;
  const starting = status?.starting;

  return (
    <div className="flex flex-col gap-4">
      <section className={CARD}>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0">
            <h2 className={CARD_LABEL}>Palworld</h2>
            <div className="mt-2 flex items-center gap-2 text-lg">
              {status === null ? (
                <span className="text-muted">Checking…</span>
              ) : starting ? (
                <span className="text-text">⏳ Starting…</span>
              ) : running ? (
                <span className="text-text">🟢 Up</span>
              ) : (
                <span className="text-muted">⚪ Off</span>
              )}
            </div>
            {!running && !starting && status !== null && (
              <p className="mt-1 text-xs text-muted">
                It stops itself once everyone logs off.
              </p>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={start}
              disabled={busy || running === true || starting === true}
              className={BTN_PRIMARY}
            >
              {starting ? "Starting…" : "Start server"}
            </button>
            <button onClick={showConnect} disabled={busy} className={BTN_GHOST}>
              How to join
            </button>
          </div>
        </div>

        {msg && <p className="mt-3 text-sm text-muted">{msg}</p>}
        {error && (
          <p role="alert" className="mt-3 text-sm text-danger">
            {error}
          </p>
        )}
      </section>

      {connect && (
        <section className={CARD}>
          <h2 className={`${CARD_LABEL} mb-3`}>Joining</h2>
          <dl className="flex flex-col gap-3 text-sm">
            <div>
              <dt className="text-xs text-dim">
                PC — Join Multiplayer Game → Join with IP
              </dt>
              <dd className="mt-1 flex flex-wrap items-center gap-2">
                <code className="rounded bg-surface px-2 py-1 text-text">
                  {connect.address}
                </code>
                <button onClick={copyAddress} className={BTN_GHOST}>
                  {copied ? "Copied" : "Copy"}
                </button>
              </dd>
            </div>
            <div>
              <dt className="text-xs text-dim">Password</dt>
              <dd className="mt-1">
                {connect.password ? (
                  <code className="rounded bg-surface px-2 py-1 text-text">
                    {connect.password}
                  </code>
                ) : (
                  <span className="text-muted">none</span>
                )}
              </dd>
            </div>
            {connect.server_name && (
              <div>
                <dt className="text-xs text-dim">Console (Xbox/PS5)</dt>
                <dd className="mt-1 text-muted">
                  Search{" "}
                  <span className="text-text">{connect.server_name}</span> in the
                  community server list — consoles have no address box.
                </dd>
              </div>
            )}
          </dl>
        </section>
      )}

      <section className={CARD}>
        <h2 className={`${CARD_LABEL} mb-3`}>Players</h2>
        {!running ? (
          <p className="text-sm text-muted">
            Nobody's on — the server is {starting ? "still starting" : "off"}.
          </p>
        ) : players === null ? (
          <p className="text-sm text-muted">
            Couldn't read the roster — the world may still be loading.
          </p>
        ) : players.length === 0 ? (
          <p className="text-sm text-muted">Server is up, but nobody's on yet.</p>
        ) : (
          <ul className="divide-y divide-border">
            {players.map((p) => (
              <li
                key={p.name}
                className="flex items-center justify-between py-2 text-sm"
              >
                <span className="truncate text-text">{p.name}</span>
                {p.level ? (
                  <span className="text-xs text-muted">level {p.level}</span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
