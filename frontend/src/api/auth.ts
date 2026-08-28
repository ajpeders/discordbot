import { apiFetch, apiUrl, clearToken, getToken, setToken } from "./client";

export async function login(password: string): Promise<void> {
  const res = await apiFetch<{ token: string }>("/api/login", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
  setToken(res.token);
}

export function isAuthenticated(): boolean {
  return Boolean(getToken());
}

export function logout(): void {
  clearToken();
}

export interface AuthUser {
  sub: string;
  name: string;
  avatar: string | null;
  admin: boolean;
}

export interface AuthConfig {
  password: boolean;
  discord: boolean;
}

export const getAuthConfig = () => apiFetch<AuthConfig>("/api/auth/config");

export const getMe = () => apiFetch<{ user: AuthUser | null }>("/api/auth/me");

/** Full page navigation: the OAuth handshake is a browser redirect flow. */
export function startDiscordLogin(): void {
  window.location.href = apiUrl("/api/auth/discord/login");
}

/**
 * Consume a token handed back in the URL fragment by the OAuth callback.
 *
 * The callback puts it in the fragment rather than the query so it never
 * reaches a server log or a Referer header; we clear it from the address bar
 * immediately so it does not linger in history either.
 */
export function consumeTokenFromFragment(): boolean {
  const hash = window.location.hash;
  if (!hash.startsWith("#token=")) return false;
  const token = decodeURIComponent(hash.slice("#token=".length));
  if (!token) return false;
  setToken(token);
  window.history.replaceState(null, "", window.location.pathname);
  return true;
}

const OAUTH_ERRORS: Record<string, string> = {
  denied: "You cancelled the Discord sign-in.",
  state: "That sign-in link expired or wasn't started here. Please try again.",
  exchange: "Discord rejected the sign-in. Please try again.",
  identify: "Couldn't read your Discord profile. Please try again.",
  network: "Couldn't reach Discord. Please try again.",
  not_a_member: "That Discord account isn't in this server.",
};

export function oauthErrorMessage(code: string | null): string {
  if (!code) return "";
  return OAUTH_ERRORS[code] ?? "Sign-in failed. Please try again.";
}
