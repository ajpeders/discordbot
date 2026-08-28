import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useGuild } from "../state/guild";
import SearchBox from "../components/SearchBox";
import Visualizer from "../components/Visualizer";
import {
  connectVoice,
  getNowPlaying,
  getVoiceChannels,
  moveQueueTrack,
  playbackControl,
  queueTrack,
  removeQueueTrack,
  seekTo,
  uploadFiles,
  type NowPlaying,
  type PlaybackAction,
  type VoiceChannel,
} from "../api/bot";

function fmtDuration(seconds: number | null): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "";
  const safe = Math.max(0, seconds);
  const m = Math.floor(safe / 60);
  const s = Math.floor(safe % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

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

export default function DashboardPage() {
  const { guildId } = useGuild();
  const [np, setNp] = useState<NowPlaying | null>(null);
  const [channels, setChannels] = useState<VoiceChannel[]>([]);
  const [channelId, setChannelId] = useState("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  // Local interpolation of `elapsed` between polls so the progress bar moves
  // smoothly. `baseElapsedRef` is whatever the API last told us; `baseTsRef`
  // is the wall-clock time at which we stored it. We reset both when the
  // track changes or playback pauses, and we tick `displayElapsed` ~5fps.
  const baseElapsedRef = useRef<number>(0);
  const baseTsRef = useRef<number>(performance.now());
  const lastUrlRef = useRef<string | null>(null);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const seekInFlightRef = useRef(false);
  const [displayElapsed, setDisplayElapsed] = useState<number>(0);
  const [seekDraft, setSeekDraft] = useState<number | null>(null);
  const [seekPending, setSeekPending] = useState(false);

  const refresh = useCallback(() => {
    if (!guildId) return;
    getNowPlaying(guildId).then(setNp).catch(() => setNp(null));
  }, [guildId]);

  useLayoutEffect(() => {
    refresh();
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, [refresh]);

  // Sync the interpolation base whenever a fresh NowPlaying snapshot arrives,
  // or whenever the track / paused state flips.
  //
  // useLayoutEffect, not useEffect: `displayElapsed` is derived from
  // `np.elapsed`, so a passive effect commits one painted frame where the
  // scrubber still reads 0 while the snapshot says 30 — a visible flash back
  // to the start of the track on every poll. A layout effect flushes in the
  // same commit, before paint, so that intermediate value is never observable.
  useLayoutEffect(() => {
    const url = np?.current?.url ?? null;
    const elapsed = typeof np?.elapsed === "number" ? np.elapsed : 0;

    if (url !== lastUrlRef.current) {
      lastUrlRef.current = url;
      baseElapsedRef.current = elapsed;
      baseTsRef.current = performance.now();
      setDisplayElapsed(elapsed);
      setSeekDraft(null);
      return;
    }

    // Same track: re-anchor on every poll so we don't drift from the server.
    baseElapsedRef.current = elapsed;
    baseTsRef.current = performance.now();
    setDisplayElapsed(elapsed);
  }, [np]);

  // Tick the displayed elapsed value while playing.
  useEffect(() => {
    if (!np?.current || np.paused || typeof np.elapsed !== "number") return;
    const id = setInterval(() => {
      const delta = (performance.now() - baseTsRef.current) / 1000;
      setDisplayElapsed(baseElapsedRef.current + delta);
    }, 200);
    return () => clearInterval(id);
  }, [np?.current, np?.paused, np?.elapsed]);

  useEffect(() => {
    if (!guildId) return;
    getVoiceChannels(guildId)
      .then((res) => {
        setChannels(res.channels);
        setChannelId((c) => c || res.channels[0]?.id || "");
      })
      .catch(() => setChannels([]));
  }, [guildId]);

  async function control(action: PlaybackAction) {
    if (!guildId) return;
    setBusy(true);
    try {
      await playbackControl(guildId, action);
      refresh();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function shuffle() {
    if (!guildId) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = (await playbackControl(guildId, "shuffle")) as {
        ok: boolean;
        count?: number;
      };
      const count = res.count ?? np?.queue.length ?? 0;
      setMsg(`Shuffled ${count} track${count === 1 ? "" : "s"}.`);
      refresh();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function join() {
    if (!guildId || !channelId) return;
    setBusy(true);
    setMsg(null);
    try {
      await connectVoice(guildId, channelId);
      const name = channels.find((c) => c.id === channelId)?.name ?? "channel";
      setMsg(`Joined ${name}.`);
      refresh();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onPickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const picked = e.target.files ? Array.from(e.target.files) : [];
    if (uploadInputRef.current) uploadInputRef.current.value = "";
    if (!guildId || picked.length === 0) return;
    setBusy(true);
    setMsg(null);
    try {
      setMsg(`Uploading ${picked.length} file${picked.length === 1 ? "" : "s"}…`);
      const uploaded = await uploadFiles(picked);
      let queued = 0;
      for (const name of uploaded.saved) {
        try {
          await queueTrack(guildId, name);
          queued += 1;
        } catch (err) {
          console.error("queue after upload failed", name, err);
        }
      }
      setMsg(
        `Uploaded ${uploaded.saved.length} · queued ${queued}` +
          (np?.connected ? "." : ". Join a voice channel to start playback."),
      );
      refresh();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function performSeek(target: number) {
    if (!guildId || !np?.current) return;
    const dur = np.current.duration;
    if (!dur || dur <= 0) return;
    const clamped = Math.max(0, Math.min(dur - 1, target));
    const prevElapsed = displayElapsed;
    const prevBaseElapsed = baseElapsedRef.current;
    const prevBaseTs = baseTsRef.current;

    setMsg(null);
    // Optimistic UI: snap the local bar to the new position immediately.
    baseElapsedRef.current = clamped;
    baseTsRef.current = performance.now();
    setDisplayElapsed(clamped);
    seekInFlightRef.current = true;
    setSeekPending(true);
    try {
      const res = await seekTo(guildId, clamped);
      const confirmed = typeof res.elapsed === "number" ? res.elapsed : clamped;
      baseElapsedRef.current = confirmed;
      baseTsRef.current = performance.now();
      setDisplayElapsed(confirmed);
      setSeekDraft(null);
      refresh();
    } catch (err) {
      baseElapsedRef.current = prevBaseElapsed;
      baseTsRef.current = prevBaseTs;
      setDisplayElapsed(prevElapsed);
      setSeekDraft(null);
      setMsg(err instanceof Error ? err.message : String(err));
    } finally {
      seekInFlightRef.current = false;
      setSeekPending(false);
    }
  }

  function commitSeek(value: number) {
    if (seekInFlightRef.current) return;
    setSeekDraft(value);
    void performSeek(value);
  }

  async function editQueue(op: "up" | "down" | "remove", index: number) {
    if (!guildId) return;
    setBusy(true);
    setMsg(null);
    try {
      if (op === "remove") {
        await removeQueueTrack(guildId, index);
      } else {
        const to = op === "up" ? index - 1 : index + 1;
        await moveQueueTrack(guildId, index, to);
      }
      refresh();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function submitQuery(value: string) {
    if (!guildId || !value.trim()) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await queueTrack(guildId, value.trim());
      const noun = `track${res.queued === 1 ? "" : "s"}`;
      setMsg(
        res.connected
          ? `Queued ${res.queued} ${noun}.`
          : `Queued ${res.queued} ${noun}. Join a voice channel to start playback.`,
      );
      refresh();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!guildId) {
    return <p className="text-muted">No server selected.</p>;
  }

  const totalDuration =
    typeof np?.duration === "number"
      ? np.duration
      : np?.current?.duration ?? null;
  const showProgress =
    !!np?.current &&
    typeof np?.elapsed === "number" &&
    typeof totalDuration === "number" &&
    totalDuration > 0;
  const visibleElapsed = seekDraft ?? displayElapsed;
  const progressPct = showProgress
    ? Math.min(100, Math.max(0, (visibleElapsed / (totalDuration as number)) * 100))
    : 0;
  const seekMax = showProgress ? Math.max(1, Math.floor(totalDuration as number)) : 1;
  const seekValue = Math.min(seekMax, Math.max(0, Math.round(visibleElapsed)));

  return (
    <div className="space-y-6">
      {/* Hero now-playing */}
      <section className="overflow-hidden rounded-xl border border-border bg-gradient-to-br from-bg-elev via-surface to-[#26212a] p-5 shadow-xl sm:p-8">
        <div className="flex flex-col gap-6 md:flex-row md:items-end">
          <div className="relative h-36 w-36 shrink-0 overflow-hidden rounded-lg bg-gradient-to-br from-accent/25 via-[#2b3139] to-bg-elev shadow-2xl sm:h-48 sm:w-48">
            {np?.current?.thumbnail ? (
              <img
                src={np.current.thumbnail}
                alt=""
                loading="lazy"
                className="h-full w-full object-cover"
              />
            ) : (
              <div className="flex h-full w-full items-center justify-center text-6xl text-white/20">
                ♫
              </div>
            )}
          </div>
          <div className="flex min-w-0 flex-1 flex-col gap-3">
            <div className={CARD_LABEL}>
              {np?.connected ? `Playing on ${np.channel}` : "Not connected"}
            </div>
            {np?.current ? (
              <>
                <div className="flex items-center gap-3">
                  <h1 className="min-w-0 flex-1 truncate text-2xl font-bold tracking-tight sm:text-4xl">
                    {np.current.title}
                  </h1>
                  <Visualizer active={!!np?.connected && !np?.paused} />
                </div>
                <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
                  <span className={BADGE}>{np.current.source}</span>
                  {np.current.duration ? (
                    <span>{fmtDuration(np.current.duration)}</span>
                  ) : null}
                  <span>· requested by {np.current.requester}</span>
                </div>
              </>
            ) : (
              <div className="flex items-center gap-3">
                <h1 className="min-w-0 flex-1 text-2xl font-bold tracking-tight text-muted sm:text-4xl">
                  Nothing playing
                </h1>
                <Visualizer active={!!np?.connected && !np?.paused} />
              </div>
            )}

            {showProgress && (
              <div className="mt-1">
                <input
                  type="range"
                  min={0}
                  max={seekMax}
                  step={1}
                  value={seekValue}
                  aria-label="Seek"
                  aria-valuemin={0}
                  aria-valuemax={seekMax}
                  aria-valuenow={seekValue}
                  aria-busy={seekPending}
                  disabled={busy || seekPending}
                  onChange={(e) => setSeekDraft(Number(e.currentTarget.value))}
                  onPointerUp={(e) => commitSeek(Number(e.currentTarget.value))}
                  onKeyUp={(e) => commitSeek(Number(e.currentTarget.value))}
                  onBlur={(e) => {
                    if (seekDraft !== null) commitSeek(Number(e.currentTarget.value));
                  }}
                  className="seek-range w-full"
                  style={{
                    background: `linear-gradient(to right, var(--color-accent) ${progressPct}%, var(--color-surface) ${progressPct}%)`,
                  }}
                />
                <div className="mt-1 flex justify-between text-xs text-muted">
                  <span>{fmtDuration(visibleElapsed)}</span>
                  <span>{seekPending ? "Seeking..." : fmtDuration(totalDuration as number)}</span>
                </div>
              </div>
            )}

            <div className="mt-2 flex flex-wrap gap-2">
              <button
                disabled={busy}
                onClick={() => control("prev")}
                className={BTN_GHOST}
                title="Previous"
              >
                ⏮
              </button>
              <button
                disabled={busy}
                onClick={() => control(np?.paused ? "resume" : "pause")}
                className={BTN_PRIMARY}
              >
                {np?.paused ? "▶ Resume" : "⏸ Pause"}
              </button>
              <button
                disabled={busy}
                onClick={() => control("skip")}
                className={BTN_GHOST}
                title="Skip"
              >
                ⏭
              </button>
              <button
                disabled={busy || !np?.connected}
                onClick={() => control("stop")}
                className={BTN_DANGER}
              >
                Stop &amp; leave
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* Voice channel */}
      <section className={CARD}>
        <div className="mb-3 flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
          <h2 className={CARD_LABEL}>Voice channel</h2>
          <span className="text-sm text-muted">
            {np?.connected ? (
              <>
                Connected to{" "}
                <span className="text-text">🔊 {np.channel}</span>
              </>
            ) : (
              <>
                Target:{" "}
                <span className="text-text">
                  🔊 {channels.find((c) => c.id === channelId)?.name ?? "—"}
                </span>
              </>
            )}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={channelId}
            onChange={(e) => setChannelId(e.target.value)}
            className={`${INPUT} min-w-[220px] flex-1`}
          >
            {channels.length === 0 && <option value="">No voice channels</option>}
            {channels.map((c) => (
              <option key={c.id} value={c.id}>
                🔊 {c.name} {c.members > 0 ? `(${c.members} listening)` : "(empty)"}
              </option>
            ))}
          </select>
          <button
            disabled={busy || !channelId}
            onClick={join}
            className={BTN_PRIMARY}
          >
            {np?.connected ? "Move here" : "Join"}
          </button>
          {np?.connected && (
            <button
              disabled={busy}
              onClick={() => control("leave")}
              className={BTN_GHOST}
              title="Leave voice but keep the queue"
            >
              Leave
            </button>
          )}
          <button
            disabled={busy || (np?.queue.length ?? 0) === 0}
            onClick={shuffle}
            className={BTN_GHOST}
            title="Shuffle the up-next queue"
          >
            ⇄ Shuffle
          </button>
        </div>
      </section>

      {/* Queue input with live search */}
      <section className={CARD}>
        <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-baseline sm:justify-between">
          <h2 className={CARD_LABEL}>Add to queue</h2>
          <label
            className={`${BTN_GHOST} w-fit cursor-pointer ${busy ? "pointer-events-none opacity-40" : ""}`}
            title="Upload a file from your computer and queue it"
          >
            <span aria-hidden>📁</span> From file…
            <input
              ref={uploadInputRef}
              type="file"
              accept="audio/*"
              multiple
              disabled={busy}
              onChange={onPickFile}
              className="sr-only"
            />
          </label>
        </div>
        <SearchBox
          value={query}
          onChange={setQuery}
          onPick={submitQuery}
          disabled={busy}
          placeholder="Search, or paste a YouTube / Spotify / SoundCloud / direct-download URL"
          buttonLabel="Queue"
        />
        {msg && (
          <p className="mt-3 text-sm text-muted">
            <span className="text-accent">›</span> {msg}
          </p>
        )}
      </section>

      {/* Queue (now-playing first, then up next) */}
      <section className={CARD}>
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className={CARD_LABEL}>Queue</h2>
          <span className="text-xs text-dim">
            {(np?.queue.length ?? 0) + (np?.current ? 1 : 0)} track
            {(np?.queue.length ?? 0) + (np?.current ? 1 : 0) === 1 ? "" : "s"}
          </span>
        </div>
        {(np?.current || (np && np.queue.length > 0)) ? (
          <ol className="divide-y divide-border">
            {np.current && (
              <li className="flex items-center gap-3 rounded-md bg-accent/10 px-2 py-2 text-sm">
                <span className="w-6 shrink-0 text-right text-accent" aria-label="Now playing">
                  ▶
                </span>
                {np.current.thumbnail ? (
                  <img
                    src={np.current.thumbnail}
                    alt=""
                    loading="lazy"
                    className="h-8 w-12 shrink-0 rounded object-cover"
                  />
                ) : (
                  <div className="flex h-8 w-12 shrink-0 items-center justify-center rounded bg-surface text-xs text-dim">
                    ♫
                  </div>
                )}
                <span className="flex-1 truncate font-semibold text-text">
                  {np.current.title}
                </span>
                <span className="text-[10px] uppercase tracking-wider text-accent">
                  now playing
                </span>
                <span className={BADGE}>{np.current.source}</span>
              </li>
            )}
            {np.queue.map((t, i) => (
              <li
                key={i}
                className="group flex items-center gap-3 py-2 text-sm transition-colors hover:bg-surface/40"
              >
                <span className="w-6 shrink-0 text-right text-xs text-dim">
                  {i + 1}
                </span>
                {t.thumbnail ? (
                  <img
                    src={t.thumbnail}
                    alt=""
                    loading="lazy"
                    className="h-8 w-12 shrink-0 rounded object-cover"
                  />
                ) : (
                  <div className="flex h-8 w-12 shrink-0 items-center justify-center rounded bg-surface text-xs text-dim">
                    ♫
                  </div>
                )}
                <span className="flex-1 truncate">{t.title}</span>
                <span className={BADGE}>{t.source}</span>
                <div className="flex gap-1 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
                  <button
                    disabled={busy || i === 0}
                    onClick={() => editQueue("up", i)}
                    title="Move up"
                    className="flex h-7 w-7 items-center justify-center rounded-md border border-border bg-surface text-xs text-muted transition-colors hover:border-accent hover:text-text disabled:cursor-not-allowed disabled:opacity-40"
                  >↑</button>
                  <button
                    disabled={busy || i === np.queue.length - 1}
                    onClick={() => editQueue("down", i)}
                    title="Move down"
                    className="flex h-7 w-7 items-center justify-center rounded-md border border-border bg-surface text-xs text-muted transition-colors hover:border-accent hover:text-text disabled:cursor-not-allowed disabled:opacity-40"
                  >↓</button>
                  <button
                    disabled={busy}
                    onClick={() => editQueue("remove", i)}
                    title="Remove"
                    className="flex h-7 w-7 items-center justify-center rounded-md border border-border bg-surface text-xs text-muted transition-colors hover:border-danger hover:text-danger disabled:cursor-not-allowed disabled:opacity-40"
                  >✕</button>
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-sm text-muted">
            Queue is empty. Search under{" "}
            <span className="text-text">Add to queue</span> above, or open{" "}
            <Link to="/playlists" className="text-accent hover:underline">
              Library
            </Link>{" "}
            to play a saved playlist.
          </p>
        )}
      </section>
    </div>
  );
}
