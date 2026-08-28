import { apiFetch, apiUrl, getToken } from "./client";

export interface GuildStatus {
  id: string;
  name: string;
  connected: boolean;
  voice_channel: string | null;
  now_playing: string | null;
  queue_length: number;
  paused: boolean;
}

export interface StatusResponse {
  bot: string | null;
  guilds: GuildStatus[];
}

export interface VoiceChannel {
  id: string;
  name: string;
  members: number;
}

export interface TrackInfo {
  title: string;
  url: string;
  source: string;
  duration: number | null;
  requester: string;
  thumbnail: string | null;
}

export interface NowPlaying {
  connected: boolean;
  channel?: string | null;
  paused: boolean;
  current: TrackInfo | null;
  queue: TrackInfo[];
  elapsed?: number | null;
  duration?: number | null;
}

export interface LocalFile {
  path: string;
  name: string;
  size: number | null;
}

export interface PlaylistSummary {
  name: string;
  count: number;
}

export interface PlaylistEntry {
  title: string;
  url: string;
  source: string;
  added_by: string;
}

export type PlaybackAction =
  | "pause"
  | "resume"
  | "skip"
  | "prev"
  | "stop"
  | "leave"
  | "shuffle";

export interface SearchResultItem {
  title: string;
  url: string;
  source: string;
  duration: number | null;
  uploader: string | null;
  thumbnail: string | null;
}

export const getStatus = () => apiFetch<StatusResponse>("/api/status");

export const searchSongs = (q: string, limit = 5) =>
  apiFetch<{ results: SearchResultItem[] }>(
    `/api/search?q=${encodeURIComponent(q)}&limit=${limit}`,
  );

export const getVoiceChannels = (gid: string) =>
  apiFetch<{ channels: VoiceChannel[] }>(`/api/guilds/${gid}/voice-channels`);

export const getNowPlaying = (gid: string) =>
  apiFetch<NowPlaying>(`/api/guilds/${gid}/now-playing`);

export const getLocalFiles = () =>
  apiFetch<{ files: LocalFile[]; truncated?: boolean }>("/api/files");

export interface HistoryEntry {
  ts: number;
  title: string;
  url: string;
  source: string;
  duration: number | null;
  requester: string;
  thumbnail: string | null;
}

export const getHistory = (gid: string, limit = 50, offset = 0) =>
  apiFetch<{ entries: HistoryEntry[] }>(
    `/api/guilds/${gid}/history?limit=${limit}&offset=${offset}`,
  );

export async function uploadFiles(files: File[]): Promise<{ saved: string[] }> {
  const form = new FormData();
  for (const f of files) form.append("file", f, f.name);
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  // Don't set Content-Type — the browser fills the multipart boundary.
  const res = await fetch(apiUrl("/api/upload"), {
    method: "POST",
    headers,
    body: form,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Upload failed (${res.status})`);
  }
  return (await res.json()) as { saved: string[] };
}

export const playbackControl = (gid: string, action: PlaybackAction) =>
  apiFetch<{ ok: boolean }>(`/api/guilds/${gid}/playback/${action}`, { method: "POST" });

export const connectVoice = (gid: string, channel_id: string) =>
  apiFetch<{ ok: boolean }>(`/api/guilds/${gid}/connect`, {
    method: "POST",
    body: JSON.stringify({ channel_id }),
  });

export const seekTo = (gid: string, seconds: number) =>
  apiFetch<{ ok: boolean; elapsed: number | null }>(
    `/api/guilds/${gid}/seek`,
    { method: "POST", body: JSON.stringify({ seconds }) },
  );

export const queueTrack = (gid: string, query: string, channel_id?: string) =>
  apiFetch<{ queued: number; connected: boolean; tracks: TrackInfo[] }>(
    `/api/guilds/${gid}/queue`,
    { method: "POST", body: JSON.stringify({ query, channel_id }) },
  );

export const removeQueueTrack = (gid: string, index: number) =>
  apiFetch<{ removed: TrackInfo }>(`/api/guilds/${gid}/queue/${index}`, {
    method: "DELETE",
  });

export const moveQueueTrack = (gid: string, from: number, to: number) =>
  apiFetch<{ ok: boolean }>(`/api/guilds/${gid}/queue/move`, {
    method: "POST",
    body: JSON.stringify({ from, to }),
  });

export const listPlaylists = (gid: string) =>
  apiFetch<{ playlists: PlaylistSummary[] }>(`/api/guilds/${gid}/playlists`);

export const createPlaylist = (gid: string, name: string) =>
  apiFetch<{ name: string; count: number }>(`/api/guilds/${gid}/playlists`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });

export const getPlaylist = (gid: string, name: string) =>
  apiFetch<{ name: string; entries: PlaylistEntry[] }>(
    `/api/guilds/${gid}/playlists/${encodeURIComponent(name)}`,
  );

export const addToPlaylist = (gid: string, name: string, query: string) =>
  apiFetch<{ position: number; entry: PlaylistEntry }>(
    `/api/guilds/${gid}/playlists/${encodeURIComponent(name)}`,
    { method: "POST", body: JSON.stringify({ query }) },
  );

export const deletePlaylist = (gid: string, name: string) =>
  apiFetch<{ ok: boolean }>(`/api/guilds/${gid}/playlists/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });

export const removeTrack = (gid: string, name: string, index: number) =>
  apiFetch<{ removed: PlaylistEntry }>(
    `/api/guilds/${gid}/playlists/${encodeURIComponent(name)}/tracks/${index}`,
    { method: "DELETE" },
  );

export const reorderPlaylist = (gid: string, name: string, entries: PlaylistEntry[]) =>
  apiFetch<{ ok: boolean; count: number }>(
    `/api/guilds/${gid}/playlists/${encodeURIComponent(name)}`,
    { method: "PUT", body: JSON.stringify({ entries }) },
  );

export const syncPlaylist = (gid: string, name: string, url: string, replace: boolean) =>
  apiFetch<{ imported: number; total: number }>(
    `/api/guilds/${gid}/playlists/${encodeURIComponent(name)}/sync`,
    { method: "POST", body: JSON.stringify({ url, replace }) },
  );

export const importPlaylist = (gid: string, url: string, name?: string) =>
  apiFetch<{ name: string; imported: number }>(
    `/api/guilds/${gid}/playlists/import`,
    { method: "POST", body: JSON.stringify({ url, name }) },
  );

export const playPlaylist = (gid: string, name: string, channel_id?: string) =>
  apiFetch<{ queued: number }>(
    `/api/guilds/${gid}/playlists/${encodeURIComponent(name)}/play`,
    { method: "POST", body: JSON.stringify({ channel_id }) },
  );

// --- games -----------------------------------------------------------------

export interface GamesStatus {
  enabled: boolean;
  running?: boolean | null;
  starting?: boolean;
  players_configured?: boolean;
  error?: string | null;
}

export interface GamePlayer {
  name: string;
  level?: number | null;
}

export const getGamesStatus = () => apiFetch<GamesStatus>("/api/games/status");

export const startGameServer = () =>
  apiFetch<{ state: string }>("/api/games/start", { method: "POST" });

export const getGamePlayers = () =>
  apiFetch<{ running: boolean; players: GamePlayer[] }>("/api/games/players");

export const getGameConnect = () =>
  apiFetch<{
    address: string;
    password: string | null;
    server_name: string | null;
  }>("/api/games/connect");
