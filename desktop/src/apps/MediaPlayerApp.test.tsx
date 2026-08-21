import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MediaPlayerApp } from "./MediaPlayerApp";
import Plyr from "plyr";

// Same mockFetch + flush helpers from
// desktop/src/apps/ProjectsApp/ProjectDecisions.test.tsx. MediaPlayerApp does
// not fetch on mount, but stubbing fetch prevents unrelated on-mount
// integrations in the shared environment from interfering with assertions.
function mockFetch(
  responses: Record<string, { ok: boolean; status?: number; body: unknown }>,
) {
  return vi.fn().mockImplementation((input: string) => {
    const hit = responses[input] ?? responses["*"];
    if (!hit) throw new Error(`Unmocked fetch: ${input}`);
    return Promise.resolve({
      ok: hit.ok,
      status: hit.status ?? (hit.ok ? 200 : 500),
      json: () => Promise.resolve(hit.body),
    });
  });
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

// Plyr manipulates the real DOM and exposes a constructor used by the
// component. Mock the module so new Plyr(...) returns a fake instance that
// records play()/destroy() calls without touching the DOM.
const mockPlyrInstance = {
  play: vi.fn().mockResolvedValue(undefined),
  destroy: vi.fn(),
};

vi.mock("plyr", () => ({
  default: vi.fn(function () {
    return mockPlyrInstance;
  }),
}));

const MockedPlyr = vi.mocked(Plyr);

function makeFile(name: string, type = "video/mp4") {
  return new File([new ArrayBuffer(16)], name, { type });
}

function getFileInput(container: HTMLElement) {
  return container.querySelector('input[type="file"]') as HTMLInputElement;
}

describe("MediaPlayerApp", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubGlobal("fetch", mockFetch({ "*": { ok: true, body: {} } }));
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:mock-url");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    MockedPlyr.mockClear();
    mockPlyrInstance.play.mockClear();
    mockPlyrInstance.destroy.mockClear();
  });

  it("renders the empty state before a file is chosen", () => {
    const { container } = render(<MediaPlayerApp windowId="win-1" />);
    expect(MockedPlyr).not.toHaveBeenCalled();
    expect(screen.getByText("No media loaded")).toBeInTheDocument();
    expect(screen.getByText(/pick a video or audio file/i)).toBeInTheDocument();
    expect(screen.getByText("Open File")).toBeInTheDocument();
    expect(getFileInput(container)).toHaveAttribute(
      "accept",
      "video/*,audio/*",
    );
  });

  it("shows the basename of a decoded routed url, not the full nested path", async () => {
    const url = `http://localhost/api/files/${encodeURIComponent("nested/intro.mp4")}`;
    render(<MediaPlayerApp windowId="win-1" url={url} />);
    await flush();
    expect(screen.getByText("intro.mp4")).toBeInTheDocument();
    expect(screen.queryByText("nested/intro.mp4")).not.toBeInTheDocument();
  });

  it("loads a selected file, shows its name, and mounts the video element", () => {
    const { container } = render(<MediaPlayerApp windowId="win-1" />);
    fireEvent.change(getFileInput(container), {
      target: { files: [makeFile("clip.mp4")] },
    });

    expect(URL.createObjectURL).toHaveBeenCalledWith(
      expect.objectContaining({ name: "clip.mp4" }),
    );
    expect(screen.getByText("clip.mp4")).toBeInTheDocument();
    const video = container.querySelector("video") as HTMLVideoElement;
    expect(video).toBeInTheDocument();
    expect(video).toHaveAttribute("src", "blob:mock-url");
    expect(video).toHaveAttribute("aria-label", "Media player");
  });

  it("instantiates Plyr with the video element and controls, and starts playback", async () => {
    const { container } = render(<MediaPlayerApp windowId="win-1" />);
    fireEvent.change(getFileInput(container), {
      target: { files: [makeFile("clip.mp4")] },
    });
    await flush();

    expect(MockedPlyr).toHaveBeenCalledTimes(1);
    expect(MockedPlyr).toHaveBeenCalledWith(
      expect.any(HTMLVideoElement),
      expect.objectContaining({
        controls: expect.arrayContaining([
          "play-large",
          "play",
          "progress",
          "current-time",
          "mute",
          "volume",
          "fullscreen",
        ]),
      }),
    );
    expect(mockPlyrInstance.play).toHaveBeenCalled();
  });

  it("revokes the previous object URL when switching files", () => {
    const { container } = render(<MediaPlayerApp windowId="win-1" />);

    fireEvent.change(getFileInput(container), {
      target: { files: [makeFile("first.mp4")] },
    });
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();

    fireEvent.change(getFileInput(container), {
      target: { files: [makeFile("second.mp4")] },
    });
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
  });

  it("ignores a file picker cancel (no files selected)", () => {
    const { container } = render(<MediaPlayerApp windowId="win-1" />);
    fireEvent.change(getFileInput(container), {
      target: { files: [] },
    });

    expect(screen.getByText("No media loaded")).toBeInTheDocument();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(MockedPlyr).not.toHaveBeenCalled();
  });

  it("destroys the Plyr instance and revokes the object URL on unmount", async () => {
    const { container, unmount } = render(
      <MediaPlayerApp windowId="win-1" />,
    );
    fireEvent.change(getFileInput(container), {
      target: { files: [makeFile("clip.mp4")] },
    });
    await flush();

    unmount();

    expect(mockPlyrInstance.destroy).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
  });
});
