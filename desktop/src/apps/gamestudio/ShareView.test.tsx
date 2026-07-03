import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { ShareView } from "./ShareView";

const GAME = {
  id: "share-game",
  name: "Share Game",
  prompt: "",
  template: "breakout",
  created: 1,
  updated: 2,
  files: { "index.html": "<!doctype html></html>", "game.js": "const x = 1;" },
};

function makeFetchMock(opts?: { installStatus?: number; installBody?: unknown }) {
  const installCalls: FormData[] = [];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();

    if (url === "/api/games/share-game" && method === "GET") {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(GAME) } as Response);
    }
    if (url === "/api/userspace-apps/analyze" && method === "POST") {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ findings: [], blocked: false }) } as Response);
    }
    if (url === "/api/games/share-game/package" && method === "GET") {
      return Promise.resolve({ ok: true, blob: () => Promise.resolve(new Blob(["zip-bytes"])) } as Response);
    }
    if (url === "/api/userspace-apps/install" && method === "POST") {
      installCalls.push(init?.body as FormData);
      const status = opts?.installStatus ?? 200;
      return Promise.resolve({
        ok: status < 400,
        status,
        json: () =>
          Promise.resolve(
            opts?.installBody ?? { app_id: "share-game", permissions_requested: [], needs_consent: false, new_permissions: [] },
          ),
      } as Response);
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
  });
  return { fetchMock, installCalls };
}

describe("ShareView", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows an empty state with no active game", () => {
    vi.stubGlobal("fetch", vi.fn() as unknown as typeof fetch);
    render(<ShareView gameId={null} />);
    expect(screen.getByText("Open or create a game first.")).toBeDefined();
  });

  it("builds the game's .taosapp package and installs it via the existing userspace-apps endpoint, tagged ai-generated", async () => {
    const { fetchMock, installCalls } = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<ShareView gameId="share-game" />);
    await waitFor(() => expect(screen.getByRole("button", { name: /Install on this taOS/ })).toBeDefined());
    // Wait for the security scan to finish so the install button is enabled.
    await waitFor(() => expect(screen.getByText(/No security issues found/)).toBeDefined());

    fireEvent.click(screen.getByRole("button", { name: /Install on this taOS/ }));

    await waitFor(() => expect(installCalls.length).toBe(1));
    const form = installCalls[0]!;
    expect(form.get("package")).toBeInstanceOf(File);
    expect((form.get("package") as File).name).toBe("share-game.taosapp");
    expect(form.get("provenance")).toBe("ai-generated");

    await waitFor(() => expect(screen.getByText(/Installed\. Find it in Launchpad/)).toBeDefined());
  });

  it("surfaces an install error instead of faking success", async () => {
    const { fetchMock } = makeFetchMock({ installStatus: 422, installBody: { error: "blocked_by_security_analysis" } });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<ShareView gameId="share-game" />);
    await waitFor(() => expect(screen.getByText(/No security issues found/)).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: /Install on this taOS/ }));

    await waitFor(() => expect(screen.getByText("blocked_by_security_analysis")).toBeDefined());
  });
});
