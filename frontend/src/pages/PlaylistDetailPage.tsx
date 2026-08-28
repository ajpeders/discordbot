import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useGuild } from "../state/guild";
import SearchBox from "../components/SearchBox";
import {
  addToPlaylist,
  deletePlaylist,
  getPlaylist,
  getVoiceChannels,
  playPlaylist,
  removeTrack,
  reorderPlaylist,
  syncPlaylist,
  type PlaylistEntry,
  type VoiceChannel,
} from "../api/bot";

const CARD = "rounded-xl border border-border bg-bg-elev p-6";
const CARD_LABEL =
  "text-[10px] font-semibold uppercase tracking-[0.18em] text-dim";
const BADGE =
  "inline-flex items-center rounded-md border border-border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-muted";
const INPUT =
  "rounded-md border border-border bg-surface px-3 py-2 text-sm text-text outline-none transition-colors focus:border-accent placeholder:text-dim";
const BTN =
  "rounded-md px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40";
const BTN_PRIMARY = `${BTN} bg-accent text-black hover:bg-accent-hover`;
const BTN_GHOST = `${BTN} border border-border text-text hover:border-accent hover:text-accent`;
const BTN_DANGER = `${BTN} border border-danger/50 text-danger hover:bg-danger/10`;
const ICON_BTN =
  "flex h-8 w-8 items-center justify-center rounded-md border border-border bg-surface text-xs text-muted transition-colors hover:border-accent hover:text-text disabled:cursor-not-allowed disabled:opacity-40";

export default function PlaylistDetailPage() {
  const { guildId } = useGuild();
  const { name = "" } = useParams();
  const navigate = useNavigate();

  const [entries, setEntries] = useState<PlaylistEntry[]>([]);
  const [channels, setChannels] = useState<VoiceChannel[]>([]);
  const [channelId, setChannelId] = useState("");
  const [addQuery, setAddQuery] = useState("");
  const [syncUrl, setSyncUrl] = useState("");
  const [replace, setReplace] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  // Two-step delete, in-app rather than window.confirm(), so the styling,
  // focus handling, and cancel affordance match the rest of the UI.
  const [confirmDelete, setConfirmDelete] = useState(false);

  const refresh = useCallback(() => {
    if (!guildId) return;
    getPlaylist(guildId, name)
      .then((res) => setEntries(res.entries))
      .catch((err) => setMsg(err instanceof Error ? err.message : String(err)));
  }, [guildId, name]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!guildId) return;
    getVoiceChannels(guildId)
      .then((res) => {
        setChannels(res.channels);
        setChannelId((c) => c || res.channels[0]?.id || "");
      })
      .catch(() => setChannels([]));
  }, [guildId]);

  async function run<T>(fn: () => Promise<T>, ok?: (r: T) => string) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await fn();
      if (ok) setMsg(ok(r));
      refresh();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!guildId) return <p className="text-muted">No server selected.</p>;

  async function move(index: number, dir: -1 | 1) {
    const next = [...entries];
    const target = index + dir;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setEntries(next);
    await run(() => reorderPlaylist(guildId!, name, next));
  }

  return (
    <div className="space-y-6">
      <button
        onClick={() => navigate("/playlists")}
        className="text-sm text-muted transition-colors hover:text-accent"
      >
        ‹ Library
      </button>

      {/* Header */}
      <section className="overflow-hidden rounded-xl border border-border bg-gradient-to-br from-bg-elev via-surface to-[#26212a] p-8 shadow-xl">
        <div className="flex flex-col gap-6 md:flex-row md:items-end">
          <div className="flex h-40 w-40 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-accent/30 via-[#2b3139] to-bg-elev text-5xl text-white/30 shadow-2xl">
            ♫
          </div>
          <div className="min-w-0 flex-1 space-y-3">
            <div className={CARD_LABEL}>Playlist</div>
            <h1 className="truncate text-4xl font-bold tracking-tight">{name}</h1>
            <p className="text-sm text-muted">
              {entries.length} track{entries.length === 1 ? "" : "s"}
            </p>

            <div className="flex flex-wrap items-center gap-2 pt-2">
              <select
                value={channelId}
                onChange={(e) => setChannelId(e.target.value)}
                className={`${INPUT} min-w-[220px]`}
              >
                {channels.length === 0 && (
                  <option value="">No voice channels</option>
                )}
                {channels.map((c) => (
                  <option key={c.id} value={c.id}>
                    🔊 {c.name}{" "}
                    {c.members > 0 ? `(${c.members})` : "(empty)"}
                  </option>
                ))}
              </select>
              <button
                disabled={busy || entries.length === 0}
                onClick={() =>
                  run(
                    () => playPlaylist(guildId!, name, channelId || undefined),
                    (r) => `Queued ${r.queued} tracks.`,
                  )
                }
                className={BTN_PRIMARY}
              >
                ▶ Play all
              </button>
              {confirmDelete ? (
                <>
                  <button
                    autoFocus
                    disabled={busy}
                    onClick={() => {
                      setConfirmDelete(false);
                      run(() => deletePlaylist(guildId!, name)).then(() =>
                        navigate("/playlists"),
                      );
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Escape") setConfirmDelete(false);
                    }}
                    className={BTN_DANGER}
                  >
                    Confirm delete
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => setConfirmDelete(false)}
                    className={BTN_GHOST}
                  >
                    Cancel
                  </button>
                </>
              ) : (
                <button
                  disabled={busy}
                  onClick={() => setConfirmDelete(true)}
                  className={BTN_DANGER}
                >
                  Delete
                </button>
              )}
            </div>
          </div>
        </div>
      </section>

      {/* Add + Import */}
      <div className="grid gap-4 md:grid-cols-2">
        <section className={CARD}>
          <h2 className={`${CARD_LABEL} mb-3`}>Add a track</h2>
          <SearchBox
            value={addQuery}
            onChange={setAddQuery}
            onPick={(value) => {
              run(
                () => addToPlaylist(guildId!, name, value),
                (r) => `Added ${r.entry.title}.`,
              );
            }}
            disabled={busy}
            placeholder="Song name or URL"
            buttonLabel="Add"
          />
        </section>

        <section className={CARD}>
          <h2 className={`${CARD_LABEL} mb-3`}>Import from URL</h2>
          <form
            className="flex flex-wrap gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (!syncUrl.trim()) return;
              run(
                () => syncPlaylist(guildId!, name, syncUrl.trim(), replace),
                (r) => `Imported ${r.imported} tracks (total ${r.total}).`,
              ).then(() => setSyncUrl(""));
            }}
          >
            <input
              type="text"
              placeholder="Spotify / YouTube / SoundCloud playlist URL"
              value={syncUrl}
              onChange={(e) => setSyncUrl(e.target.value)}
              className={`${INPUT} min-w-[200px] flex-1`}
            />
            <label className="inline-flex items-center gap-2 text-xs text-muted">
              <input
                type="checkbox"
                checked={replace}
                onChange={(e) => setReplace(e.target.checked)}
                className="h-3 w-3 accent-[var(--color-accent)]"
              />
              replace
            </label>
            <button
              type="submit"
              disabled={busy || !syncUrl.trim()}
              className={BTN_GHOST}
            >
              Import
            </button>
          </form>
        </section>
      </div>

      {msg && (
        <p className="text-sm text-muted">
          <span className="text-accent">›</span> {msg}
        </p>
      )}

      {/* Tracks */}
      <section className={CARD}>
        <h2 className={`${CARD_LABEL} mb-3`}>Tracks</h2>
        {entries.length === 0 ? (
          <p className="text-sm text-muted">
            No tracks yet. Search for one under{" "}
            <span className="text-text">Add a track</span>, or paste a Spotify,
            Apple Music, or YouTube playlist link under{" "}
            <span className="text-text">Import from URL</span>.
          </p>
        ) : (
          <ol className="divide-y divide-border">
            {entries.map((e, i) => (
              <li
                key={`${e.url}-${i}`}
                className="flex items-center gap-3 py-2 text-sm transition-colors hover:bg-surface/40"
              >
                <span className="w-6 shrink-0 text-right text-xs text-dim">
                  {i + 1}
                </span>
                <span className="flex-1 truncate">{e.title}</span>
                <span className={BADGE}>{e.source}</span>
                <div className="flex gap-1">
                  <button
                    disabled={busy || i === 0}
                    onClick={() => move(i, -1)}
                    className={ICON_BTN}
                    title="Move up"
                  >
                    ↑
                  </button>
                  <button
                    disabled={busy || i === entries.length - 1}
                    onClick={() => move(i, 1)}
                    className={ICON_BTN}
                    title="Move down"
                  >
                    ↓
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => run(() => removeTrack(guildId!, name, i + 1))}
                    className={`${ICON_BTN} hover:border-danger hover:text-danger`}
                    title="Remove"
                  >
                    ✕
                  </button>
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
