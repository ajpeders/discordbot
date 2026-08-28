import { useCallback, useEffect, useState } from "react";
import { useGuild } from "../state/guild";
import { getHistory, queueTrack, type HistoryEntry } from "../api/bot";

const CARD = "rounded-xl border border-border bg-bg-elev p-6";
const BADGE =
  "inline-flex items-center rounded-md border border-border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-muted";
const BTN =
  "rounded-md px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40";
const BTN_GHOST = `${BTN} border border-border text-text hover:border-accent hover:text-accent`;

function fmtAgo(ts: number): string {
  const now = Date.now() / 1000;
  const dt = Math.max(0, now - ts);
  if (dt < 60) return "just now";
  if (dt < 3600) return `${Math.floor(dt / 60)}m ago`;
  if (dt < 86400) return `${Math.floor(dt / 3600)}h ago`;
  if (dt < 86400 * 7) return `${Math.floor(dt / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export default function HistoryPage() {
  const { guildId } = useGuild();
  const [entries, setEntries] = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    if (!guildId) return;
    setLoading(true);
    getHistory(guildId, 100)
      .then((res) => {
        setEntries(res.entries);
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [guildId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function queueAgain(e: HistoryEntry) {
    if (!guildId) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await queueTrack(guildId, e.url);
      setMsg(
        res.connected
          ? `Queued “${e.title}”.`
          : `Queued “${e.title}”. Join a voice channel to start playback.`,
      );
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!guildId) return <p className="text-muted">No server selected.</p>;

  return (
    <div className="space-y-6">
      <header className="flex items-baseline justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">History</h1>
          <p className="mt-1 text-sm text-muted">
            Tracks the bot has played in this server, newest first. Click a row to queue it again.
          </p>
        </div>
        <button onClick={refresh} disabled={loading} className={BTN_GHOST}>
          {loading ? "Loading…" : "Refresh"}
        </button>
      </header>

      {error && (
        <div className="rounded-lg border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}

      {msg && (
        <p className="text-sm text-muted">
          <span className="text-accent">›</span> {msg}
        </p>
      )}

      <section className={CARD}>
        {entries.length === 0 ? (
          <p className="text-sm text-muted">No plays recorded yet — anything that plays from now on will appear here.</p>
        ) : (
          <ol className="divide-y divide-border">
            {entries.map((e, i) => (
              <li
                key={`${e.ts}-${i}`}
                className="group flex items-center gap-3 py-2 text-sm transition-colors hover:bg-surface/40"
              >
                {e.thumbnail ? (
                  <img
                    src={e.thumbnail}
                    alt=""
                    loading="lazy"
                    className="h-8 w-12 shrink-0 rounded object-cover"
                  />
                ) : (
                  <div className="flex h-8 w-12 shrink-0 items-center justify-center rounded bg-surface text-xs text-dim">
                    ♫
                  </div>
                )}
                <div className="min-w-0 flex-1">
                  <div className="truncate text-text">{e.title}</div>
                  <div className="truncate text-xs text-muted">
                    {fmtAgo(e.ts)} · {e.requester || "unknown"}
                  </div>
                </div>
                <span className={BADGE}>{e.source}</span>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => queueAgain(e)}
                  title="Queue this track again"
                  className="rounded-md border border-border px-3 py-1 text-xs font-semibold text-muted transition-colors hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-40"
                >
                  ▶ Queue
                </button>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
