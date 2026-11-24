import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { LibraryApp } from "./LibraryApp";

const MOCK_ITEMS = [
  {
    id: "lib-item-1",
    title: "YouTube Video",
    source_type: "youtube",
    source_url: "https://youtube.com/watch?v=1",
    source_id: "yt-1",
    author: "YT Author",
    summary: "Summary 1",
    content: "Content 1",
    media_path: null,
    thumbnail: null,
    categories: [],
    tags: [],
    metadata: {},
    status: "ready",
    monitor: { current_interval: 0, frequency: 0, decay_rate: 0, pinned: false, last_poll: null, last_hash: "" },
    created_at: 1700000000,
    updated_at: 1700000000,
  },
];

interface MockRule {
  id: string;
  source_pattern: string;
  quality: string;
  auto_download: number;
  enabled: number;
  created_at: number;
}

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    headers: new Map([["content-type", "application/json"]]),
    json: () => Promise.resolve(body),
  } as Response);
}

function createFetchMock() {
  const state = {
    settings: {
      preferred_quality: "720",
      storage_cap_bytes: 50 * 1024 * 1024 * 1024,
    },
    rules: [] as MockRule[],
    puts: [] as Array<Record<string, unknown>>,
    rulePosts: 0,
  };

  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (url.startsWith("/api/knowledge/items")) {
      return jsonResponse({ items: MOCK_ITEMS, count: MOCK_ITEMS.length });
    }
    if (url === "/api/agents") {
      return jsonResponse([{ name: "Agent 1", color: "#ff0000" }]);
    }
    if (url === "/api/knowledge/subscriptions") {
      return jsonResponse({ subscriptions: [] });
    }
    if (url === "/api/library/settings") {
      if (method === "PUT") {
        const updates = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
        state.puts.push(updates);
        state.settings = {
          preferred_quality:
            typeof updates.preferred_quality === "string"
              ? updates.preferred_quality
              : state.settings.preferred_quality,
          storage_cap_bytes:
            typeof updates.storage_cap_bytes === "number"
              ? updates.storage_cap_bytes
              : state.settings.storage_cap_bytes,
        };
        return jsonResponse(state.settings);
      }
      return jsonResponse(state.settings);
    }
    if (url === "/api/library/rules") {
      if (method === "POST") {
        state.rulePosts += 1;
        const form = init?.body as FormData;
        const pattern = String(form.get("source_pattern"));
        if (
          state.rules.some(
            (r) => r.source_pattern.toLowerCase() === pattern.toLowerCase(),
          )
        ) {
          return jsonResponse({ error: "duplicate" }, 409);
        }
        const rule: MockRule = {
          id: `rule-${state.rules.length + 1}`,
          source_pattern: pattern,
          quality: String(form.get("quality") ?? "720"),
          auto_download: form.get("auto_download") === "true" ? 1 : 0,
          enabled: 1,
          created_at: 1700000000,
        };
        state.rules = [...state.rules, rule];
        return jsonResponse(
          { rule_id: rule.id, source_pattern: pattern, status: "created" },
          201,
        );
      }
      return jsonResponse({ rules: state.rules, count: state.rules.length });
    }
    if (url.startsWith("/api/library/rules/") && method === "DELETE") {
      const id = url.split("/").pop();
      state.rules = state.rules.filter((r) => r.id !== id);
      return jsonResponse({ status: "deleted", rule_id: id });
    }
    return jsonResponse({}, 404);
  });

  return { fetchMock, state };
}

async function openSettings() {
  await waitFor(() => screen.getByRole("radio", { name: "settings" }));
  fireEvent.click(screen.getByRole("radio", { name: "settings" }));
  await waitFor(() => screen.getByText("Library Settings"), { timeout: 5000 });
}

describe("LibraryApp settings view", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders the view mode toggle and switches to settings view", async () => {
    const { fetchMock } = createFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);
    await openSettings();
  });

  it("shows the server-persisted preferred quality (backend vocabulary)", async () => {
    const { fetchMock } = createFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);
    await openSettings();
    const select = screen.getByLabelText("Default download quality");
    expect(select).toBeInTheDocument();
    await waitFor(() => expect(select).toHaveValue("720"));
  });

  it("shows storage cap input with formatted default", async () => {
    const { fetchMock } = createFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);
    await openSettings();
    const input = screen.getByLabelText("Maximum storage (bytes)");
    expect(input).toBeInTheDocument();
    expect(screen.getByText(/50\.00 GB/)).toBeInTheDocument();
  });

  it("shows empty per-source rules table", async () => {
    const { fetchMock } = createFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);
    await openSettings();
    expect(screen.getByText("No source rules yet")).toBeInTheDocument();
  });

  it("adds a per-source rule via the API and shows it in the table", async () => {
    const { fetchMock, state } = createFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);
    await openSettings();

    fireEvent.change(screen.getByLabelText("Source pattern"), {
      target: { value: "*youtube.com/*" },
    });
    fireEvent.change(screen.getByLabelText("Quality"), {
      target: { value: "480" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add source rule" }));

    await waitFor(() => screen.getByText("*youtube.com/*"), { timeout: 5000 });
    expect(state.rulePosts).toBe(1);
    const table = document.querySelector("table");
    expect(table).toBeTruthy();
    expect(within(table!).getByText("480")).toBeInTheDocument();
    expect(screen.queryByText("No source rules yet")).toBeNull();
  });

  it("rejects a duplicate per-source rule without posting again", async () => {
    const { fetchMock, state } = createFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);
    await openSettings();

    fireEvent.change(screen.getByLabelText("Source pattern"), {
      target: { value: "*youtube.com/*" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add source rule" }));
    await waitFor(() => screen.getByText("*youtube.com/*"), { timeout: 5000 });
    expect(state.rulePosts).toBe(1);

    fireEvent.change(screen.getByLabelText("Source pattern"), {
      target: { value: "*youtube.com/*" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add source rule" }));

    await waitFor(() => screen.getByText(/already exists/), { timeout: 5000 });
    expect(state.rulePosts).toBe(1);
  });

  it("deletes a per-source rule via the API", async () => {
    const { fetchMock } = createFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);
    await openSettings();

    fireEvent.change(screen.getByLabelText("Source pattern"), {
      target: { value: "*youtube.com/*" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add source rule" }));
    await waitFor(() => screen.getByText("*youtube.com/*"), { timeout: 5000 });

    const table = document.querySelector("table");
    expect(table).toBeTruthy();
    fireEvent.click(
      within(table!).getByRole("button", {
        name: "Delete rule for *youtube.com/*",
      }),
    );

    await waitFor(() => screen.getByText("No source rules yet"), { timeout: 5000 });
  });

  it("persists quality changes to the server, not localStorage", async () => {
    const { fetchMock, state } = createFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);
    await openSettings();

    const qualitySelect = screen.getByLabelText("Default download quality");
    fireEvent.change(qualitySelect, { target: { value: "1080" } });

    await waitFor(
      () =>
        expect(
          state.puts.some((u) => u.preferred_quality === "1080"),
        ).toBe(true),
      { timeout: 5000 },
    );
    expect(localStorage.getItem("taos-library-settings")).toBeNull();
  });

  it("rejects a negative storage cap without saving it", async () => {
    const { fetchMock, state } = createFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(<LibraryApp windowId="test-win" />);
    await openSettings();

    const capInput = screen.getByLabelText("Maximum storage (bytes)");
    fireEvent.change(capInput, { target: { value: "-5" } });

    await waitFor(() => screen.getByText(/whole number of bytes/), {
      timeout: 5000,
    });
    expect(
      state.puts.some((u) => "storage_cap_bytes" in u),
    ).toBe(false);
  });
});
