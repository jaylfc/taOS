import { describe, it, expect, vi, afterEach } from "vitest";
import { deleteSong, getSong, listSongs, renameSong, saveSong, slugify } from "./songs-api";
import { createDefaultSong } from "./types";

function jsonResponse(body: unknown, ok = true, status = ok ? 200 : 400): Response {
  return {
    ok,
    status,
    json: () => Promise.resolve(body),
  } as Response;
}

describe("songs-api", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("listSongs GETs /api/songs and returns the array", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse([{ id: "song-1", name: "A", created_at: 1, updated_at: 2 }])));
    vi.stubGlobal("fetch", fetchMock);

    const songs = await listSongs();
    expect(fetchMock).toHaveBeenCalledWith("/api/songs");
    expect(songs).toEqual([{ id: "song-1", name: "A", created_at: 1, updated_at: 2 }]);
  });

  it("getSong GETs the record and reconstructs a Song from its JSON content", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        jsonResponse({
          id: "song-1",
          name: "A",
          content: JSON.stringify({ tempo: 100, key: "C maj", timeSig: "4/4", tracks: [] }),
          created_at: 1,
          updated_at: 2,
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const song = await getSong("song-1");
    expect(fetchMock).toHaveBeenCalledWith("/api/songs/song-1");
    expect(song).toEqual({ id: "song-1", name: "A", tempo: 100, key: "C maj", timeSig: "4/4", tracks: [] });
  });

  it("getSong surfaces a friendly error (not a raw SyntaxError) when content is corrupt", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        jsonResponse({ id: "song-1", name: "Broken", content: "{not valid json", created_at: 1, updated_at: 2 }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(getSong("song-1")).rejects.toThrow(/couldn't load/i);
  });

  it("saveSong rejects an over-cap song before hitting the network", async () => {
    const song = createDefaultSong();
    // A single note whose JSON blows past the 5 MB content cap.
    song.tracks[0].clips[0].notes[0].id = "x".repeat(6 * 1024 * 1024);
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({})));
    vi.stubGlobal("fetch", fetchMock);

    await expect(saveSong(song)).rejects.toThrow(/too large/i);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("saveSong POSTs a not-yet-persisted (local-) song and returns the server id", async () => {
    const song = createDefaultSong();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      expect(input).toBe("/api/songs");
      expect(init?.method).toBe("POST");
      const body = JSON.parse(String(init?.body));
      expect(body.name).toBe(song.name);
      const content = JSON.parse(body.content);
      expect(content.tempo).toBe(song.tempo);
      expect(content.tracks).toHaveLength(song.tracks.length);
      return Promise.resolve(
        jsonResponse({ id: "song-server1", name: song.name, content: body.content, created_at: 1, updated_at: 1 }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const saved = await saveSong(song);
    expect(saved.id).toBe("song-server1");
  });

  it("saveSong PUTs an already-persisted song", async () => {
    const song = { ...createDefaultSong(), id: "song-existing" };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      expect(input).toBe("/api/songs/song-existing");
      expect(init?.method).toBe("PUT");
      return Promise.resolve(
        jsonResponse({ id: "song-existing", name: song.name, content: "{}", created_at: 1, updated_at: 2 }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    await saveSong(song);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("renameSong fetches the current song then saves it with the new name", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/songs/song-1" && (!init || init.method === undefined)) {
        return Promise.resolve(
          jsonResponse({ id: "song-1", name: "Old", content: JSON.stringify({ tempo: 90, key: "A min", timeSig: "4/4", tracks: [] }), created_at: 1, updated_at: 1 }),
        );
      }
      if (url === "/api/songs/song-1" && init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        expect(body.name).toBe("New");
        return Promise.resolve(jsonResponse({ id: "song-1", name: "New", content: body.content, created_at: 1, updated_at: 2 }));
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const renamed = await renameSong("song-1", "New");
    expect(renamed.name).toBe("New");
  });

  it("deleteSong DELETEs the song", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      expect(input).toBe("/api/songs/song-1");
      expect(init?.method).toBe("DELETE");
      return Promise.resolve(jsonResponse({ status: "deleted", id: "song-1" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    await deleteSong("song-1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("throws the server error message on a failed request", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({ error: "name is required" }, false, 400)));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listSongs()).rejects.toThrow("name is required");
  });

  it("slugify lowercases, strips non-alphanumerics and falls back to 'song'", () => {
    expect(slugify("My Cool Track!!")).toBe("my-cool-track");
    expect(slugify("   ")).toBe("song");
  });
});
