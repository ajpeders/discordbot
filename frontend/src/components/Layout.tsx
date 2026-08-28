import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useGuild } from "../state/guild";
import { getMe, logout, type AuthUser } from "../api/auth";

const navItems = [
  { to: "/", label: "Home", icon: "⌂", end: true },
  { to: "/playlists", label: "Library", icon: "♫" },
  { to: "/local", label: "Local", icon: "♪" },
  { to: "/history", label: "History", icon: "⟲" },
  { to: "/games", label: "Games", icon: "◈" },
];

export default function Layout() {
  const { guilds, guildId, setGuildId, botName, error } = useGuild();
  const navigate = useNavigate();
  const [user, setUser] = useState<AuthUser | null>(null);

  useEffect(() => {
    // Null for password logins and API keys, which carry no identity.
    getMe()
      .then((res) => setUser(res.user))
      .catch(() => setUser(null));
  }, []);

  function handleLogout() {
    logout();
    navigate("/login");
  }

  return (
    <div className="flex min-h-full w-full flex-col bg-bg text-text md:flex-row">
      <aside className="flex shrink-0 flex-col gap-4 border-b border-border bg-bg-elev p-4 md:h-screen md:w-60 md:gap-6 md:border-b-0 md:border-r md:p-5">
        <div className="flex items-baseline justify-between gap-3 md:block">
          <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-dim">
            Music Bot
          </div>
          <div className="mt-1 truncate text-sm text-muted">
            {botName ?? "—"}
          </div>
        </div>

        <nav className="flex gap-1 overflow-x-auto md:flex-col md:overflow-visible">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex shrink-0 items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
                  isActive
                    ? "bg-surface text-text"
                    : "text-muted hover:bg-surface hover:text-text"
                }`
              }
            >
              <span className="text-base leading-none">{item.icon}</span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="grid gap-3 sm:grid-cols-[1fr_auto] md:mt-auto md:block md:space-y-3">
          <div className="min-w-0">
            <label
              htmlFor="guild-select"
              className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.16em] text-dim"
            >
              Server
            </label>
            <select
              id="guild-select"
              value={guildId ?? ""}
              onChange={(e) => setGuildId(e.target.value)}
              className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-text outline-none transition-colors focus:border-accent"
            >
              {guilds.length === 0 && <option value="">No servers</option>}
              {guilds.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2 sm:col-span-2 md:flex-col md:items-stretch md:gap-2">
            {user && (
              <div className="flex min-w-0 flex-1 items-center gap-2 rounded-md border border-border px-3 py-2">
                {user.avatar ? (
                  <img
                    src={user.avatar}
                    alt=""
                    className="h-6 w-6 shrink-0 rounded-full"
                  />
                ) : (
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface text-[10px] text-muted">
                    {user.name.slice(0, 1).toUpperCase()}
                  </span>
                )}
                <span className="truncate text-sm text-text">{user.name}</span>
                {user.admin && (
                  <span className="ml-auto shrink-0 rounded border border-border px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-dim">
                    admin
                  </span>
                )}
              </div>
            )}

            <button
              type="button"
              onClick={handleLogout}
              className="rounded-md border border-border px-3 py-2 text-sm text-muted transition-colors hover:border-accent hover:text-text md:w-full"
            >
              Log out
            </button>
          </div>
        </div>
      </aside>

      <main className="min-w-0 flex-1 overflow-y-auto">
        {error && (
          <div className="border-b border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger sm:px-8">
            Can't reach the bot: {error}
          </div>
        )}
        <div className="mx-auto max-w-6xl px-4 py-6 sm:px-8 sm:py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
