import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useGuild } from "../state/guild";
import {
  createPlaylist,
  importPlaylist,
  listPlaylists,
  type PlaylistSummary,
} from "../api/bot";

const CARD = "rounded-xl border border-border bg-bg-elev p-6";
const CARD_LABEL =
  "text-[10px] font-semibold uppercase tracking-[0.18em] text-dim";
const INPUT =
  "rounded-md border border-border bg-surface px-3 py-2 text-sm text-text outline-none transition-colors focus:border-accent placeholder:text-dim";
const BTN =
  "rounded-md px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40";
const BTN_PRIMARY = `${BTN} bg-accent text-black hover:bg-accent-hover`;

export default function PlaylistsPage() {
  const { guildId } = useGuild();
  const navigate = useNavigate();
  const [playlists, setPlaylists] = useState<PlaylistSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [createName, setCreateName] = useState("");
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [importUrl, setImportUrl] = useState("");
  const [importName, setImportName] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    if (!guildId) return;
    listPlaylists(guildId)
      .then((res) => {
        setPlaylists(res.playlists);
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [guildId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (!guildId) return <p className="text-muted">No server selected.</p>;

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    const name = createName.trim();
    if (!name) return;
    setCreateBusy(true);
    setCreateError(null);
    try {
      const res = await createPlaylist(guildId!, name);
      setCreateName("");
      refresh();
      navigate(`/playlists/${encodeURIComponent(res.name)}`);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : String(err));
    } finally {
      setCreateBusy(false);
    }
  }

  async function handleImport(e: React.FormEvent) {
    e.preventDefault();
    const url = importUrl.trim();
    if (!url) return;
    setImportBusy(true);
    setImportError(null);
    try {
      const res = await importPlaylist(
        guildId!,
        url,
        importName.trim() || undefined,
      );
      setImportUrl("");
      setImportName("");
      refresh();
      navigate(`/playlists/${encodeURIComponent(res.name)}`);
    } catch (err) {
      setImportError(err instanceof Error ? err.message : String(err));
    } finally {
      setImportBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Library</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted">
            Saved playlists for this server. Create a blank list, import a URL,
            or open an existing playlist to add tracks.
          </p>
        </div>
        <div className="rounded-md border border-border bg-surface px-3 py-2 text-xs text-muted">
          {playlists.length} playlist{playlists.length === 1 ? "" : "s"}
        </div>
      </header>

      {error && (
        <div className="rounded-lg border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(280px,0.8fr)_minmax(360px,1.2fr)]">
        <section className={CARD}>
          <h2 className={`${CARD_LABEL} mb-3`}>Create playlist</h2>
          <form className="flex flex-wrap gap-2" onSubmit={handleCreate}>
            <input
              type="text"
              placeholder="Playlist name"
              value={createName}
              onChange={(e) => setCreateName(e.target.value)}
              className={`${INPUT} min-w-[220px] flex-1`}
              required
            />
            <button
              type="submit"
              disabled={createBusy || !createName.trim()}
              className={BTN_PRIMARY}
            >
              {createBusy ? "Creating…" : "Create"}
            </button>
          </form>
          {createError && (
            <p className="mt-3 text-sm text-danger">{createError}</p>
          )}
        </section>

        <section className={CARD}>
          <h2 className={`${CARD_LABEL} mb-3`}>Import a playlist</h2>
          <form className="flex flex-wrap gap-2" onSubmit={handleImport}>
            <input
              type="text"
              placeholder="YouTube / Spotify / SoundCloud / Apple Music URL"
              value={importUrl}
              onChange={(e) => setImportUrl(e.target.value)}
              className={`${INPUT} min-w-[260px] flex-1`}
              required
            />
            <input
              type="text"
              placeholder="Name (optional)"
              value={importName}
              onChange={(e) => setImportName(e.target.value)}
              className={`${INPUT} min-w-[180px] flex-1`}
            />
            <button
              type="submit"
              disabled={importBusy || !importUrl.trim()}
              className={BTN_PRIMARY}
            >
              {importBusy ? "Importing…" : "Import"}
            </button>
          </form>
          {importError && (
            <p className="mt-3 text-sm text-danger">{importError}</p>
          )}
        </section>
      </div>

      {playlists.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-bg-elev p-10 text-center">
          <p className="text-muted">No playlists yet. Create one to start adding tracks.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {playlists.map((p) => (
            <Link
              key={p.name}
              to={`/playlists/${encodeURIComponent(p.name)}`}
              className="group rounded-lg border border-border bg-bg-elev p-5 transition-all hover:-translate-y-0.5 hover:border-accent hover:bg-surface"
            >
              <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-md bg-gradient-to-br from-accent/25 via-[#2b3139] to-surface text-2xl text-white/70">
                ♫
              </div>
              <div className="truncate text-lg font-semibold tracking-tight group-hover:text-accent">
                {p.name}
              </div>
              <div className="mt-1 text-xs text-muted">
                {p.count} track{p.count === 1 ? "" : "s"}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
