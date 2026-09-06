import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { VideoStudioApp } from "./VideoStudioApp";

/* ------------------------------------------------------------------ */
/*  Fetch mock helpers                                                 */
/* ------------------------------------------------------------------ */

function mockFetch(
  responses: Record<string, { ok: boolean; status?: number; body: unknown }>,
) {
  return vi.fn().mockImplementation((input: string, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    const key = `${method} ${input}`;
    const hit = responses[key] ?? responses[input] ?? responses["*"];
    if (!hit) throw new Error(`Unmocked fetch: ${key}`);
    return Promise.resolve({
      ok: hit.ok,
      status: hit.status ?? (hit.ok ? 200 : 422),
      json: () => Promise.resolve(hit.body),
    });
  });
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

function renderApp() {
  return render(<VideoStudioApp windowId="test-window" />);
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("VideoStudioApp", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the Create/Library rail and Create view by default", async () => {
    vi.stubGlobal("fetch", mockFetch({ "GET /api/video": { ok: true, body: { videos: [] } } }));
    renderApp();
    await flush();

    const nav = screen.getByRole("navigation", { name: "Video Studio views" });
    expect(nav).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Library" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Create" })).toBeTruthy();
    expect(screen.getByPlaceholderText(/describe the video/i)).toBeTruthy();
  });

  it("posts the prompt, enqueues a job, and polls it to done", async () => {
    const fetchMock = mockFetch({
      "GET /api/video": { ok: true, body: { videos: [] } },
      "POST /api/video/generate": {
        ok: true,
        status: 202,
        body: { job_id: "job-1", status: "queued" },
      },
      "GET /api/video/jobs/job-1": {
        ok: true,
        body: {
          job_id: "job-1",
          status: "done",
          result: {
            filename: "123_456.mp4",
            path: "/data/videos/123_456.mp4",
            prompt: "a cat riding a skateboard",
            model: "wan2.1-1.3b",
            duration: 5,
            resolution: "480x832",
            seed: 456,
          },
        },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp();
    await flush();

    const textarea = screen.getByPlaceholderText(/describe the video/i);
    fireEvent.change(textarea, { target: { value: "a cat riding a skateboard" } });
    fireEvent.click(screen.getByRole("button", { name: /^Generate$/ }));
    await flush();

    const call = fetchMock.mock.calls.find(
      ([url]: [string]) => url === "/api/video/generate",
    );
    expect(call).toBeTruthy();
    const [, init] = call as [string, RequestInit];
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body).toMatchObject({
      prompt: "a cat riding a skateboard",
      model: "wan2.1-1.3b",
      duration: 5,
      resolution: "480x832",
    });
    // No seed pinned by the user, so it should be omitted (backend randomizes).
    expect(body.seed).toBeUndefined();

    // The job endpoint was polled.
    expect(
      fetchMock.mock.calls.some(([url]: [string]) => url === "/api/video/jobs/job-1"),
    ).toBe(true);

    await waitFor(() => {
      expect(screen.getByText(/Generate$/)).toBeTruthy();
    });

    // The completed result is rendered in the stage.
    const video = document.querySelector("video");
    expect(video?.getAttribute("src")).toBe("/api/video/123_456.mp4");
  });

  it("shows the job's error message when the poll reports a failure", async () => {
    const fetchMock = mockFetch({
      "GET /api/video": { ok: true, body: { videos: [] } },
      "POST /api/video/generate": {
        ok: true,
        status: 202,
        body: { job_id: "job-2", status: "queued" },
      },
      "GET /api/video/jobs/job-2": {
        ok: true,
        body: {
          job_id: "job-2",
          status: "error",
          error: "Cannot connect to video backend at http://localhost:9000. Is it running?",
        },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp();
    await flush();

    fireEvent.change(screen.getByPlaceholderText(/describe the video/i), {
      target: { value: "a dog surfing" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Generate$/ }));
    await flush();

    expect(
      await screen.findByText(/Cannot connect to video backend/),
    ).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByText(/Generate$/)).toBeTruthy();
    });
  });

  it("shows the install-a-backend prompt on a 503 response", async () => {
    const fetchMock = mockFetch({
      "GET /api/video": { ok: true, body: { videos: [] } },
      "POST /api/video/generate": {
        ok: false,
        status: 503,
        body: { error: "No video generation backend configured." },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp();
    await flush();

    fireEvent.change(screen.getByPlaceholderText(/describe the video/i), {
      target: { value: "a dog surfing" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Generate$/ }));
    await flush();

    expect(await screen.findByText("No video generation backend configured.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Open Store" })).toBeTruthy();
  });

  it("renders the results grid from GET /api/video in the Library view", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "GET /api/video": {
          ok: true,
          body: {
            videos: [
              {
                filename: "111_222.mp4",
                path: "/data/videos/111_222.mp4",
                size_bytes: 1024,
                prompt: "a rocket launch",
                model: "wan2.1-1.3b",
                duration: 5,
                resolution: "480x832",
                seed: 222,
              },
            ],
          },
        },
      }),
    );
    renderApp();
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Library" }));
    await flush();

    expect(screen.getByRole("heading", { name: "Library" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Open video: a rocket launch/ })).toBeTruthy();
  });

  it("shows an empty state in the Library view when there are no videos", async () => {
    vi.stubGlobal("fetch", mockFetch({ "GET /api/video": { ok: true, body: { videos: [] } } }));
    renderApp();
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Library" }));
    await flush();

    expect(screen.getByText("No videos yet")).toBeTruthy();
  });

  it("calls DELETE /api/video/{filename} when deleting from the Library", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = mockFetch({
      "GET /api/video": {
        ok: true,
        body: {
          videos: [
            {
              filename: "111_222.mp4",
              path: "/data/videos/111_222.mp4",
              size_bytes: 1024,
              prompt: "a rocket launch",
              model: "wan2.1-1.3b",
              duration: 5,
              resolution: "480x832",
              seed: 222,
            },
          ],
        },
      },
      "DELETE /api/video/111_222.mp4": { ok: true, body: { status: "deleted", filename: "111_222.mp4" } },
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp();
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Library" }));
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Open video: a rocket launch" }));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Delete video" }));
    await flush();

    expect(confirmSpy).toHaveBeenCalledWith('Delete "111_222.mp4"? This can\'t be undone.');
    const call = fetchMock.mock.calls.find(
      ([url, init]: [string, RequestInit?]) =>
        url === "/api/video/111_222.mp4" && init?.method === "DELETE",
    );
    expect(call).toBeTruthy();
    expect(screen.getByText("No videos yet")).toBeTruthy();
    confirmSpy.mockRestore();
  });

  it("keeps the video and shows an error when DELETE rejects", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = mockFetch({
      "GET /api/video": {
        ok: true,
        body: {
          videos: [
            {
              filename: "111_222.mp4",
              path: "/data/videos/111_222.mp4",
              size_bytes: 1024,
              prompt: "a rocket launch",
              model: "wan2.1-1.3b",
              duration: 5,
              resolution: "480x832",
              seed: 222,
            },
          ],
        },
      },
      "DELETE /api/video/111_222.mp4": { ok: false, status: 500, body: { error: "Server error" } },
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp();
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Library" }));
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Open video: a rocket launch" }));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Delete video" }));
    await flush();

    expect(screen.getByRole("button", { name: /Open video: a rocket launch/ })).toBeTruthy();
    expect(screen.getByText(/Failed to delete video/)).toBeTruthy();
    confirmSpy.mockRestore();
  });

  it("does not send DELETE when the user cancels the confirm", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const fetchMock = mockFetch({
      "GET /api/video": {
        ok: true,
        body: {
          videos: [
            {
              filename: "111_222.mp4",
              path: "/data/videos/111_222.mp4",
              size_bytes: 1024,
              prompt: "a rocket launch",
              model: "wan2.1-1.3b",
              duration: 5,
              resolution: "480x832",
              seed: 222,
            },
          ],
        },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    renderApp();
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Library" }));
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Open video: a rocket launch" }));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Delete video" }));
    await flush();

    expect(confirmSpy).toHaveBeenCalledWith('Delete "111_222.mp4"? This can\'t be undone.');
    expect(
      fetchMock.mock.calls.some(([url, init]: [string, RequestInit?]) =>
        url === "/api/video/111_222.mp4" && init?.method === "DELETE",
      ),
    ).toBe(false);
    expect(screen.getByRole("button", { name: /Open video: a rocket launch/ })).toBeTruthy();
    confirmSpy.mockRestore();
  });
});
