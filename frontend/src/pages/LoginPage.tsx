import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  consumeTokenFromFragment,
  getAuthConfig,
  login,
  oauthErrorMessage,
  startDiscordLogin,
  type AuthConfig,
} from "../api/auth";

export default function LoginPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [password, setPassword] = useState("");
  const [error, setError] = useState(() => oauthErrorMessage(params.get("error")));
  const [loading, setLoading] = useState(false);
  const [methods, setMethods] = useState<AuthConfig | null>(null);

  // The OAuth callback redirects here with the token in the fragment.
  useEffect(() => {
    if (consumeTokenFromFragment()) navigate("/", { replace: true });
  }, [navigate]);

  useEffect(() => {
    getAuthConfig()
      .then(setMethods)
      // If this fails the bot is unreachable; offering the password form is
      // still the more useful fallback than showing nothing.
      .catch(() => setMethods({ password: true, discord: false }));
  }, []);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(password);
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  const showPassword = methods?.password ?? true;
  const showDiscord = methods?.discord ?? false;

  return (
    <div className="flex min-h-full items-center justify-center bg-bg p-6 text-text">
      <div className="w-full max-w-sm rounded-lg border border-border bg-bg-elev p-8 shadow-2xl">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-lg bg-accent text-2xl text-black">
            ♫
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">Music Bot</h1>
          <p className="mt-1 text-sm text-muted">Sign in to control playback</p>
        </div>

        {error && (
          <p
            role="alert"
            className="mb-4 rounded-md bg-danger/10 px-3 py-2 text-sm text-danger"
          >
            {error}
          </p>
        )}

        {showDiscord && (
          <button
            type="button"
            onClick={startDiscordLogin}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-[#5865F2] py-2.5 text-sm font-semibold text-white transition-colors hover:bg-[#4752c4]"
          >
            Continue with Discord
          </button>
        )}

        {showDiscord && showPassword && (
          <div className="my-5 flex items-center gap-3 text-[11px] uppercase tracking-wider text-dim">
            <span className="h-px flex-1 bg-border" />
            or
            <span className="h-px flex-1 bg-border" />
          </div>
        )}

        {showPassword && (
          <form onSubmit={handleSubmit} className="space-y-4">
            <label className="block">
              <span className="mb-1 block text-xs font-medium uppercase tracking-wider text-muted">
                Password
              </span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                placeholder="••••••••"
                className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text outline-none transition-colors focus:border-accent"
              />
            </label>
            <button
              type="submit"
              disabled={loading || !password}
              className="w-full rounded-md bg-accent py-2.5 text-sm font-semibold text-black transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? "Signing in…" : "Sign in"}
            </button>
          </form>
        )}

        {methods && !showPassword && !showDiscord && (
          <p className="text-sm text-muted">
            No sign-in method is configured on this bot.
          </p>
        )}
      </div>
    </div>
  );
}
