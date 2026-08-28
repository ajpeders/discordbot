import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import GamesPage from "./GamesPage";

const api = vi.hoisted(() => ({
  getGamesStatus: vi.fn(),
  getGamePlayers: vi.fn(),
  getGameConnect: vi.fn(),
  startGameServer: vi.fn(),
}));

vi.mock("../api/bot", () => ({ ...api }));

describe("GamesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getGamesStatus.mockResolvedValue({
      enabled: true,
      running: false,
      starting: false,
      players_configured: true,
      error: null,
    });
    api.getGamePlayers.mockResolvedValue({ running: true, players: [] });
    api.startGameServer.mockResolvedValue({ state: "starting" });
    api.getGameConnect.mockResolvedValue({
      address: "203.0.113.9:8211",
      password: "hunter2",
      server_name: "The Whip",
    });
  });

  it("shows the server as off and offers to start it", async () => {
    render(<GamesPage />);

    expect(await screen.findByText(/⚪ Off/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /start server/i }),
    ).toBeEnabled();
  });

  it("starts the server and explains the boot may take a while", async () => {
    const user = userEvent.setup();
    render(<GamesPage />);

    await user.click(await screen.findByRole("button", { name: /start server/i }));

    await waitFor(() => expect(api.startGameServer).toHaveBeenCalledOnce());
    expect(await screen.findByText(/game update is downloading/i)).toBeInTheDocument();
  });

  it("does not offer to start a server that is already up", async () => {
    api.getGamesStatus.mockResolvedValue({
      enabled: true,
      running: true,
      starting: false,
      players_configured: true,
    });

    render(<GamesPage />);

    expect(await screen.findByText(/🟢 Up/)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /start server/i })).toBeDisabled(),
    );
  });

  it("lists who is online when the server is up", async () => {
    api.getGamesStatus.mockResolvedValue({
      enabled: true,
      running: true,
      starting: false,
      players_configured: true,
    });
    api.getGamePlayers.mockResolvedValue({
      running: true,
      players: [{ name: "alex", level: 12 }],
    });

    render(<GamesPage />);

    expect(await screen.findByText("alex")).toBeInTheDocument();
    expect(screen.getByText(/level 12/)).toBeInTheDocument();
  });

  it("shows the join address on request", async () => {
    const user = userEvent.setup();
    render(<GamesPage />);

    await user.click(await screen.findByRole("button", { name: /how to join/i }));

    expect(await screen.findByText("203.0.113.9:8211")).toBeInTheDocument();
    expect(screen.getByText("hunter2")).toBeInTheDocument();
  });

  it("explains when server control is not configured", async () => {
    api.getGamesStatus.mockResolvedValue({ enabled: false });

    render(<GamesPage />);

    expect(await screen.findByText(/isn't configured/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /start server/i })).toBeNull();
  });

  it("surfaces a failure to reach the bot", async () => {
    api.getGamesStatus.mockRejectedValue(new Error("Could not reach the bot"));

    render(<GamesPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /could not reach the bot/i,
    );
  });
});
