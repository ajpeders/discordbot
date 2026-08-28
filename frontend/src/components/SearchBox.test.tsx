import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SearchBox from "./SearchBox";

const api = vi.hoisted(() => ({
  searchSongs: vi.fn(),
}));

vi.mock("../api/bot", () => ({ ...api }));

function renderBox(value: string) {
  return render(
    <SearchBox value={value} onChange={() => {}} onPick={() => {}} />,
  );
}

describe("SearchBox", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows an inline error when the search request fails", async () => {
    api.searchSongs.mockRejectedValue(new Error("Could not reach the bot"));

    renderBox("radiohead");

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        /could not reach the bot/i,
      ),
    );
  });

  it("distinguishes an empty result set from a failure", async () => {
    api.searchSongs.mockResolvedValue({ results: [] });

    renderBox("radiohead");

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/no results/i),
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("renders results and no error on success", async () => {
    api.searchSongs.mockResolvedValue({
      results: [
        {
          title: "Creep",
          url: "https://example.com/creep",
          source: "youtube",
          duration: 238,
          uploader: "Radiohead",
          thumbnail: null,
        },
      ],
    });

    renderBox("radiohead");

    expect(await screen.findByText("Creep")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("clears a previous error once a later search succeeds", async () => {
    api.searchSongs.mockRejectedValueOnce(new Error("boom"));

    const { rerender } = render(
      <SearchBox value="radiohea" onChange={() => {}} onPick={() => {}} />,
    );
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());

    api.searchSongs.mockResolvedValue({
      results: [
        {
          title: "Creep",
          url: "https://example.com/creep",
          source: "youtube",
          duration: 238,
          uploader: "Radiohead",
          thumbnail: null,
        },
      ],
    });
    rerender(
      <SearchBox value="radiohead" onChange={() => {}} onPick={() => {}} />,
    );

    expect(await screen.findByText("Creep")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });
});
