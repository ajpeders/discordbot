import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import DashboardPage from "./DashboardPage";

// DashboardPage links to the Library from its empty-queue state, so it needs
// router context.
const renderPage = () =>
  render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  );

const api = vi.hoisted(() => ({
  connectVoice: vi.fn(),
  getNowPlaying: vi.fn(),
  getVoiceChannels: vi.fn(),
  moveQueueTrack: vi.fn(),
  playbackControl: vi.fn(),
  queueTrack: vi.fn(),
  removeQueueTrack: vi.fn(),
  seekTo: vi.fn(),
  uploadFiles: vi.fn(),
}));

vi.mock("../state/guild", () => ({
  useGuild: () => ({ guildId: "42" }),
}));

vi.mock("../components/SearchBox", () => ({
  default: () => <div data-testid="search-box" />,
}));

vi.mock("../components/Visualizer", () => ({
  default: () => <div data-testid="visualizer" />,
}));

vi.mock("../api/bot", () => ({
  ...api,
}));

const nowPlaying = {
  connected: true,
  channel: "General",
  paused: false,
  elapsed: 30,
  duration: 120,
  current: {
    title: "Track",
    url: "https://example.com/track",
    source: "youtube",
    duration: 120,
    requester: "alex",
    thumbnail: null,
  },
  queue: [],
};

describe("DashboardPage seek", () => {
  beforeEach(() => {
    api.getNowPlaying.mockResolvedValue(nowPlaying);
    api.getVoiceChannels.mockResolvedValue({ channels: [] });
    api.seekTo.mockResolvedValue({ ok: true, elapsed: 60 });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("seeks to the clicked position on the progress bar", async () => {
    renderPage();

    const slider = await screen.findByRole("slider", { name: "Seek" });

    fireEvent.change(slider, { target: { value: "60" } });
    fireEvent.pointerUp(slider);

    await waitFor(() => expect(api.seekTo).toHaveBeenCalledWith("42", 60));
  });

  it("restores the previous elapsed value if seek fails", async () => {
    api.seekTo.mockRejectedValueOnce(new Error("seek failed"));
    renderPage();

    const slider = await screen.findByRole("slider", { name: "Seek" });
    // DashboardPage syncs displayElapsed in a layout effect, so the slider
    // should already read np.elapsed by the time findByRole resolves. Waiting
    // anyway keeps this from depending on that commit-timing detail.
    await waitFor(() => expect(slider).toHaveAttribute("aria-valuenow", "30"));

    fireEvent.change(slider, { target: { value: "96" } });
    fireEvent.pointerUp(slider);

    await screen.findByText(/seek failed/i);
    await waitFor(() => expect(slider).toHaveAttribute("aria-valuenow", "30"));
  });

  it("supports keyboard seeking from the slider", async () => {
    renderPage();

    const slider = await screen.findByRole("slider", { name: "Seek" });
    fireEvent.change(slider, { target: { value: "35" } });
    fireEvent.keyUp(slider, { key: "ArrowRight" });

    await waitFor(() => expect(api.seekTo).toHaveBeenCalledWith("42", 35));
  });

  it("does not commit the same seek twice when pointerup is followed by blur", async () => {
    api.seekTo.mockReturnValueOnce(new Promise(() => {}));
    renderPage();

    const slider = await screen.findByRole("slider", { name: "Seek" });
    fireEvent.change(slider, { target: { value: "70" } });
    fireEvent.pointerUp(slider);
    fireEvent.blur(slider);

    await waitFor(() => expect(api.seekTo).toHaveBeenCalledTimes(1));
    expect(api.seekTo).toHaveBeenCalledWith("42", 70);
    expect(slider).toBeDisabled();
    expect(screen.getByText("Seeking...")).toBeInTheDocument();
  });
});
