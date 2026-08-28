import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { getStatus, type GuildStatus } from "../api/bot";

const STORAGE_KEY = "musicbot.guild_id";

interface GuildContextValue {
  guilds: GuildStatus[];
  guildId: string | null;
  setGuildId: (id: string) => void;
  botName: string | null;
  error: string | null;
  loading: boolean;
  refresh: () => void;
}

const GuildContext = createContext<GuildContextValue | null>(null);

export function GuildProvider({ children }: { children: React.ReactNode }) {
  const [guilds, setGuilds] = useState<GuildStatus[]>([]);
  const [botName, setBotName] = useState<string | null>(null);
  const [guildId, setGuildIdState] = useState<string | null>(
    () => localStorage.getItem(STORAGE_KEY),
  );
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    getStatus()
      .then((res) => {
        setGuilds(res.guilds);
        setBotName(res.bot);
        setError(null);
        setGuildIdState((current) => {
          if (current && res.guilds.some((g) => g.id === current)) return current;
          return res.guilds[0]?.id ?? null;
        });
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  const setGuildId = useCallback((id: string) => {
    localStorage.setItem(STORAGE_KEY, id);
    setGuildIdState(id);
  }, []);

  return (
    <GuildContext.Provider
      value={{ guilds, guildId, setGuildId, botName, error, loading, refresh }}
    >
      {children}
    </GuildContext.Provider>
  );
}

export function useGuild(): GuildContextValue {
  const ctx = useContext(GuildContext);
  if (!ctx) throw new Error("useGuild must be used within GuildProvider");
  return ctx;
}
