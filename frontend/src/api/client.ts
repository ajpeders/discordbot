const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
const TOKEN_STORAGE = "musicbot.token";

export function apiUrl(path: string): string {
  if (!API_BASE_URL) return path;
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_STORAGE, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_STORAGE);
}

function looksLikeHtml(body: string): boolean {
  const sample = body.trimStart().slice(0, 64).toLowerCase();
  return sample.startsWith("<!doctype html") || sample.startsWith("<html");
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let res: Response;
  try {
    res = await fetch(apiUrl(path), { ...options, headers });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Network request failed";
    throw new ApiError(0, `Could not reach the bot: ${message}`);
  }

  const contentType = res.headers.get("content-type") ?? "";

  if (!res.ok) {
    if (res.status === 401 && !path.endsWith("/login")) {
      clearToken();
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
      throw new ApiError(401, "Authentication required");
    }
    const body = await res.text();
    try {
      const parsed = JSON.parse(body) as { error?: unknown; reason?: unknown };
      const detail = parsed.error ?? parsed.reason;
      if (typeof detail === "string") throw new ApiError(res.status, detail);
    } catch (err) {
      if (err instanceof ApiError) throw err;
    }
    if (looksLikeHtml(body)) {
      throw new ApiError(res.status, "The web app received HTML instead of API JSON. The bot service may be down or misrouted.");
    }
    throw new ApiError(res.status, body || res.statusText);
  }

  if (res.status === 204) return undefined as T;

  const body = await res.text();
  if (!body.trim()) return undefined as T;
  if (!contentType.includes("application/json")) {
    if (looksLikeHtml(body)) {
      throw new ApiError(502, "The web app received HTML instead of API JSON. The bot service may be down or misrouted.");
    }
    throw new ApiError(502, `Expected JSON from the bot API, got ${contentType || "an unknown content type"}.`);
  }

  try {
    return JSON.parse(body) as T;
  } catch {
    throw new ApiError(502, "The bot API returned invalid JSON.");
  }
}
