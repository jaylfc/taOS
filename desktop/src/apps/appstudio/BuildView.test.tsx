import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { BuildView } from "./BuildView";
import { setBuildSession, getBuildSession } from "./build-state";

function ndjsonResponse(deltas: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const d of deltas) {
        controller.enqueue(encoder.encode(JSON.stringify({ delta: d }) + "\n"));
      }
      controller.close();
    },
  });
  return new Response(body, { status: 200 });
}

const APP_RESPONSE = [
  "### FILE: index.html",
  "```html",
  "<!doctype html><html><body>Chore tracker</body></html>",
  "```",
].join("\n");

function makeFetchMock(opts?: { findings?: unknown[] }) {
  const installCalls: FormData[] = [];
  const packageCalls: unknown[] = [];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();

    if (url === "/api/taos-agent/settings") {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ model: "test-model" }) } as Response);
    }
    if (url === "/api/taos-agent/chat" && method === "POST") {
      return Promise.resolve(ndjsonResponse([APP_RESPONSE]));
    }
    if (url === "/api/userspace-apps/analyze" && method === "POST") {
      const findings = opts?.findings ?? [];
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ findings, blocked: findings.length > 0 }),
      } as Response);
    }
    if (url === "/api/userspace-apps/package" && method === "POST") {
      packageCalls.push(JSON.parse(String(init?.body ?? "{}")));
      return Promise.resolve({
        ok: true,
        blob: () => Promise.resolve(new Blob(["zip-bytes"])),
        headers: new Headers({ "content-disposition": 'attachment; filename="chore-tracker-ab12.taosapp"' }),
      } as Response);
    }
    if (url === "/api/userspace-apps/install" && method === "POST") {
      installCalls.push(init?.body as FormData);
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            app_id: "chore-tracker-ab12",
            permissions_requested: [],
            needs_consent: false,
            new_permissions: [],
          }),
      } as Response);
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
  });
  return { fetchMock, installCalls, packageCalls };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setBuildSession(null);
});

describe("BuildView", () => {
  it("runs generate -> analyze -> package -> install and renders the installed app", async () => {
    const { fetchMock, installCalls } = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<BuildView />);
    await waitFor(() => expect(screen.getByRole("button", { name: /^build$/i })).not.toBeDisabled());

    fireEvent.click(screen.getByRole("button", { name: /^build$/i }));

    await waitFor(() => expect(installCalls.length).toBe(1));
    expect(installCalls[0]!.get("provenance")).toBe("ai-generated");

    await waitFor(() => expect(screen.getByTestId("provenance-badge")).toHaveAttribute("data-provenance", "ai-generated"));
    // the build session is handed off for PublishView to pick up
    await waitFor(() => expect(getBuildSession()?.appId).toBe("chore-tracker-ab12"));
  });

  it("blocks the pipeline before packaging when the scan finds a critical issue", async () => {
    const { fetchMock, packageCalls, installCalls } = makeFetchMock({
      findings: [
        { severity: "critical", rule_id: "eval-like-execution", file: "index.html", line: 1, message: "unsafe" },
      ],
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<BuildView />);
    await waitFor(() => expect(screen.getByRole("button", { name: /^build$/i })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: /^build$/i }));

    await waitFor(() => expect(screen.getByTestId("security-findings-blocked")).toBeInTheDocument());
    expect(packageCalls.length).toBe(0);
    expect(installCalls.length).toBe(0);
    expect(getBuildSession()).toBeNull();
  });
});
