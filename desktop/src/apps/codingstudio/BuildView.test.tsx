import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { BuildView } from "./BuildView";
import { streamTaosAgentChat } from "../appstudio/stream-chat";

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
      text: () =>
        Promise.resolve(typeof hit.body === "string" ? hit.body : JSON.stringify(hit.body)),
    } as Response);
  });
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

vi.mock("../appstudio/stream-chat", () => ({
  streamTaosAgentChat: vi.fn(),
}));

describe("BuildView", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubGlobal("fetch", mockFetch({
      "/api/coding/workspaces": {
        ok: true,
        body: [{ id: "ws-1", name: "My Workspace", path: "/tmp/ws", created_at: "2024-01-01" }],
      },
      "/api/taos-agent/settings": { ok: true, body: { model: "gpt-4" } },
      "/api/coding/workspaces/ws-1/diff": { ok: true, body: [] },
      "*": { ok: true, body: {} },
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("streams agent response and shows ApplyBlocksPanel when code blocks are detected", async () => {
    streamTaosAgentChat.mockImplementation(
      async (_messages, onDelta, _onError, _opts) => {
        onDelta(
          "Here is your file:\n```tsx\n// path: src/App.tsx\nexport default function App() { return <div>Hi</div>; }\n```",
        );
      },
    );

    render(<BuildView />);
    await flush();

    await waitFor(() => {
      expect(screen.getByLabelText("Select workspace")).toBeTruthy();
    });

    const select = screen.getByLabelText("Select workspace");
    fireEvent.change(select, { target: { value: "ws-1" } });
    await flush();

    const textarea = screen.getByPlaceholderText(/describe what you want to build or change/i);
    fireEvent.change(textarea, { target: { value: "Create an app" } });
    fireEvent.keyDown(textarea, { key: "Enter", metaKey: true, code: "Enter" });
    await flush();

    await waitFor(() => {
      expect(screen.getByText(/1 file detected in response/i)).toBeTruthy();
    });

    expect(screen.getByLabelText("Apply files to workspace")).toBeTruthy();
  });

  it("applies blocks and advances to diff tab on success", async () => {
    streamTaosAgentChat.mockImplementation(
      async (_messages, onDelta, _onError, _opts) => {
        onDelta(
          "Here is your file:\n```tsx\n// path: src/App.tsx\nexport default function App() { return <div>Hi</div>; }\n```",
        );
      },
    );

    render(<BuildView />);
    await flush();

    await waitFor(() => {
      expect(screen.getByLabelText("Select workspace")).toBeTruthy();
    });

    const select = screen.getByLabelText("Select workspace");
    fireEvent.change(select, { target: { value: "ws-1" } });
    await flush();

    const textarea = screen.getByPlaceholderText(/describe what you want to build or change/i);
    fireEvent.change(textarea, { target: { value: "Create an app" } });
    fireEvent.keyDown(textarea, { key: "Enter", metaKey: true, code: "Enter" });
    await flush();

    await waitFor(() => {
      expect(screen.getByText(/1 file detected in response/i)).toBeTruthy();
    });

    fireEvent.click(screen.getByLabelText("Apply files to workspace"));
    await flush();

    await waitFor(() => {
      const fetchCalls = (global.fetch as any).mock.calls;
      expect(
        fetchCalls.some(
          (c: any[]) =>
            String(c[0]).includes("/api/coding/workspaces/ws-1/apply-blocks") &&
            c[1]?.method === "POST",
        ),
      ).toBe(true);
    });

    await waitFor(() => {
      expect(screen.getByText("Diff review")).toBeTruthy();
    });
  });
});
