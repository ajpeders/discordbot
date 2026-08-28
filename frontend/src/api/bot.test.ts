import { afterEach, describe, expect, it, vi } from "vitest";
import { createPlaylist, getStatus, queueTrack, seekTo } from "./bot";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockFetch(body: unknown, ok = true, status = 200, contentType = "application/json") {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok,
    status,
    headers: new Headers({ "content-type": contentType }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response);
}

describe("bot api", () => {
  it("fetches status", async () => {
    const fetchMock = mockFetch({ bot: "Music#1", guilds: [] });
    const res = await getStatus();
    expect(res.bot).toBe("Music#1");
    expect(fetchMock).toHaveBeenCalledWith("/api/status", expect.objectContaining({}));
  });

  it("posts a queue request with the query body", async () => {
    const fetchMock = mockFetch({ queued: 1, tracks: [] });
    await queueTrack("123", "test song", "456");
    const [, init] = fetchMock.mock.calls[0];
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ query: "test song", channel_id: "456" });
  });

  it("posts a seek request with the seconds body", async () => {
    const fetchMock = mockFetch({ ok: true, elapsed: 42 });
    await seekTo("123", 42);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/guilds/123/seek");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ seconds: 42 });
  });

  it("posts a create playlist request with the name body", async () => {
    const fetchMock = mockFetch({ name: "road_trip", count: 0 });
    await createPlaylist("123", "Road Trip");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/guilds/123/playlists");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ name: "Road Trip" });
  });

  it("raises a useful error when the API returns html", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "text/html" }),
      text: async () => "<!doctype html><html><body>fallback</body></html>",
    } as Response);

    await expect(getStatus()).rejects.toEqual(
      expect.objectContaining({
        status: 502,
        message: "The web app received HTML instead of API JSON. The bot service may be down or misrouted.",
      }),
    );
  });
});
