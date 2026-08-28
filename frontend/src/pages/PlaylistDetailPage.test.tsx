import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PlaylistDetailPage from "./PlaylistDetailPage";

const api = vi.hoisted(() => ({
  addToPlaylist: vi.fn(),
  deletePlaylist: vi.fn(),
  getPlaylist: vi.fn(),
  getVoiceChannels: vi.fn(),
  playPlaylist: vi.fn(),
  removeTrack: vi.fn(),
  reorderPlaylist: vi.fn(),
  syncPlaylist: vi.fn(),
}));

const navigate = vi.hoisted(() => vi.fn());

vi.mock("../api/bot", () => ({ ...api }));

vi.mock("../state/guild", () => ({
  useGuild: () => ({ guildId: "42" }),
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => navigate,
  useParams: () => ({ name: "roadtrip" }),
}));

vi.mock("../components/SearchBox", () => ({
  default: () => <div data-testid="search-box" />,
}));

describe("PlaylistDetailPage delete confirmation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getPlaylist.mockResolvedValue({
      name: "roadtrip",
      entries: [
        {
          title: "Creep",
          url: "https://example.com/creep",
          source: "youtube",
          added_by: "alex",
        },
      ],
    });
    api.getVoiceChannels.mockResolvedValue({ channels: [] });
    api.deletePlaylist.mockResolvedValue({});
  });

  it("does not delete until the action is confirmed", async () => {
    const user = userEvent.setup();
    render(<PlaylistDetailPage />);

    await user.click(await screen.findByRole("button", { name: /^delete$/i }));

    // Still nothing deleted — we only revealed the confirmation.
    expect(api.deletePlaylist).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: /confirm delete/i }),
    ).toBeInTheDocument();
  });

  it("deletes and navigates away once confirmed", async () => {
    const user = userEvent.setup();
    render(<PlaylistDetailPage />);

    await user.click(await screen.findByRole("button", { name: /^delete$/i }));
    await user.click(screen.getByRole("button", { name: /confirm delete/i }));

    await waitFor(() =>
      expect(api.deletePlaylist).toHaveBeenCalledWith("42", "roadtrip"),
    );
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/playlists"));
  });

  it("cancelling restores the plain delete button and deletes nothing", async () => {
    const user = userEvent.setup();
    render(<PlaylistDetailPage />);

    await user.click(await screen.findByRole("button", { name: /^delete$/i }));
    await user.click(screen.getByRole("button", { name: /cancel/i }));

    expect(api.deletePlaylist).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: /confirm delete/i }),
    ).toBeNull();
    expect(screen.getByRole("button", { name: /^delete$/i })).toBeInTheDocument();
  });

  it("does not use the native confirm dialog", async () => {
    const nativeConfirm = vi
      .spyOn(window, "confirm")
      .mockReturnValue(true);
    const user = userEvent.setup();
    render(<PlaylistDetailPage />);

    await user.click(await screen.findByRole("button", { name: /^delete$/i }));

    expect(nativeConfirm).not.toHaveBeenCalled();
    nativeConfirm.mockRestore();
  });
});
