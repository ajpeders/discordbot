import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useGuild } from "../state/guild";
import {
  getLocalFiles,
  queueTrack,
  uploadFiles,
  type LocalFile,
} from "../api/bot";

const CARD = "rounded-xl border border-border bg-bg-elev p-6";
const CARD_LABEL =
  "text-[10px] font-semibold uppercase tracking-[0.18em] text-dim";
const BTN =
  "rounded-md px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40";
const BTN_GHOST = `${BTN} border border-border text-text hover:border-accent hover:text-accent`;

function fmtSize(bytes: number | null): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unit]}`;
}

interface TreeNode {
  folders: Map<string, TreeNode>;
  files: LocalFile[];
}

// Build a folder tree from the flat file list. Each file's path (relative to
// /music) is split on "/"; the leading segments form nested folders and the
// final segment is the file. Files come pre-sorted by path from the backend,
// so Map insertion order (preserved) keeps folders and files alphabetical.
function buildTree(files: LocalFile[]): TreeNode {
  const root: TreeNode = { folders: new Map(), files: [] };
  for (const f of files) {
    const parts = f.path.split("/");
    let node = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const seg = parts[i];
      let child = node.folders.get(seg);
      if (!child) {
        child = { folders: new Map(), files: [] };
        node.folders.set(seg, child);
      }
      node = child;
    }
    node.files.push(f);
  }
  return root;
}

function countFiles(node: TreeNode): number {
  let total = node.files.length;
  for (const child of node.folders.values()) total += countFiles(child);
  return total;
}

export default function LocalPage() {
  const { guildId } = useGuild();
  const [files, setFiles] = useState<LocalFile[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const fileInputRef = useRef<HTMLInputElement>(null);

  const tree = useMemo(() => buildTree(files), [files]);

  const toggle = useCallback((path: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    getLocalFiles()
      .then((res) => {
        setFiles(res.files);
        setTruncated(!!res.truncated);
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function queue(file: LocalFile) {
    if (!guildId) {
      setMsg("No server selected.");
      return;
    }
    setBusyPath(file.path);
    setMsg(null);
    try {
      // queueTrack treats any path ending in an audio extension as a LOCAL
      // source, so passing the relative path is enough.
      const res = await queueTrack(guildId, file.path);
      const noun = `track${res.queued === 1 ? "" : "s"}`;
      setMsg(
        res.connected
          ? `Queued ${file.name} (${res.queued} ${noun}).`
          : `Queued ${file.name}. Join a voice channel to start playback.`,
      );
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyPath(null);
    }
  }

  function renderNode(
    node: TreeNode,
    prefix: string,
    depth: number,
  ): React.ReactNode {
    const indent = `${depth * 1.25 + 0.5}rem`;
    return (
      <>
        {[...node.folders.entries()].map(([name, child]) => {
          const fullPath = prefix ? `${prefix}/${name}` : name;
          const isCollapsed = collapsed.has(fullPath);
          return (
            <li key={`d:${fullPath}`}>
              <button
                type="button"
                onClick={() => toggle(fullPath)}
                className="flex w-full items-center gap-2 py-2 text-left text-sm font-semibold text-text transition-colors hover:bg-surface/40"
                style={{ paddingLeft: indent }}
              >
                <span className="w-3 shrink-0 text-dim">
                  {isCollapsed ? "▸" : "▾"}
                </span>
                <span className="flex-1 truncate">{name}</span>
                <span className="shrink-0 text-xs text-dim">
                  {countFiles(child)}
                </span>
              </button>
              {!isCollapsed && (
                <ul>{renderNode(child, fullPath, depth + 1)}</ul>
              )}
            </li>
          );
        })}
        {node.files.map((f) => (
          <li key={`f:${f.path}`}>
            <button
              type="button"
              onClick={() => queue(f)}
              disabled={busyPath === f.path || !guildId}
              className="flex w-full items-center gap-3 py-2 text-left text-sm transition-colors hover:bg-surface/40 disabled:cursor-not-allowed disabled:opacity-50"
              style={{ paddingLeft: indent }}
              title={f.path}
            >
              <span className="flex-1 truncate text-text">{f.name}</span>
              <span className="shrink-0 text-xs text-muted">
                {fmtSize(f.size)}
              </span>
              <span className="shrink-0 text-xs text-accent">
                {busyPath === f.path ? "…" : "Queue"}
              </span>
            </button>
          </li>
        ))}
      </>
    );
  }

  async function onUploadChange(e: React.ChangeEvent<HTMLInputElement>) {
    const list = e.target.files;
    if (!list || list.length === 0) return;
    const picked = Array.from(list);
    setUploadStatus(
      `Uploading ${picked.length} file${picked.length === 1 ? "" : "s"}…`,
    );
    setError(null);
    try {
      const res = await uploadFiles(picked);
      setUploadStatus(
        `Uploaded ${res.saved.length} file${res.saved.length === 1 ? "" : "s"}.`,
      );
      refresh();
    } catch (err) {
      setUploadStatus(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      // Clear the input so re-selecting the same file fires onChange again.
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">Local</h1>
        <p className="mt-1 text-sm text-muted">
          Files under the bot's music share at{" "}
          <code className="rounded bg-surface px-1.5 py-0.5 text-xs">/music</code>.
          Click to queue.
        </p>
      </header>

      {error && (
        <div className="rounded-lg border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}

      <section className={CARD}>
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className={CARD_LABEL}>Upload</h2>
          {uploadStatus && (
            <span className="text-xs text-muted">{uploadStatus}</span>
          )}
        </div>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="audio/*"
          onChange={onUploadChange}
          className="block w-full text-sm text-muted file:mr-3 file:cursor-pointer file:rounded-md file:border-0 file:bg-accent file:px-4 file:py-2 file:text-sm file:font-semibold file:text-black file:transition-colors hover:file:bg-accent-hover"
        />
        <p className="mt-2 text-xs text-dim">
          Audio files only. Uploaded into the bot's music share, then
          discoverable below.
        </p>
      </section>

      <section className={CARD}>
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className={CARD_LABEL}>Files</h2>
          <div className="flex items-center gap-3">
            <span className="text-xs text-dim">
              {files.length} file{files.length === 1 ? "" : "s"}
              {truncated ? " (truncated to 500)" : ""}
            </span>
            <button
              type="button"
              onClick={refresh}
              disabled={loading}
              className={BTN_GHOST}
            >
              {loading ? "Refreshing…" : "Refresh"}
            </button>
          </div>
        </div>

        {truncated && (
          <p className="mb-3 rounded-md border border-border bg-surface px-3 py-2 text-xs text-muted">
            Truncated to 500 files. Trim the share or use a more specific path
            to see more.
          </p>
        )}

        {files.length === 0 ? (
          <p className="text-sm text-muted">
            {loading ? (
              "Loading…"
            ) : (
              <>
                No audio files in the library. Add one under{" "}
                <span className="text-text">Upload</span> above, or check that
                the bot's <code className="text-text">MUSIC_DIR</code> points at
                your music share.
              </>
            )}
          </p>
        ) : (
          <ul>{renderNode(tree, "", 0)}</ul>
        )}

        {msg && (
          <p className="mt-3 text-sm text-muted">
            <span className="text-accent">›</span> {msg}
          </p>
        )}
      </section>

      {!guildId && (
        <p className="text-sm text-muted">
          Select a server in the sidebar to queue files.
        </p>
      )}
    </div>
  );
}
