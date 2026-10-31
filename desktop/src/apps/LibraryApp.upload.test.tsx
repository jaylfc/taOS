import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
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
    categories: ["video"],
    tags: [],
    metadata: {},
    status: "ready",
    monitor: { current_interval: 3600, frequency: 3600, decay_rate: 0, pinned: false, last_poll: 1700000000, last_hash: "" },
    created_at: 1700007200,
    updated_at: 1700007300,
  },
];

const MOCK_AGENTS = [
  { name: "Agent 1", color: "#ff0000" },
  { name: "Agent 2", color: "#00ff00" },
];

const MOCK_SNAPSHOTS = [
  {
    id: 1,
    item_id: "lib-item-1",
    snapshot_at: 1700000100,
    content_hash: "abc123",
    diff_json: { title: "Updated" },
    metadata_json: {},
  },
];

const MOCK_SUBSCRIPTIONS = [
  { agent_name: "Agent 1", category: "video", auto_ingest: true },
];

function createFetchMock(overrides: Record<string, Promise<Response>> = {}) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);

    for (const [prefix, response] of Object.entries(overrides)) {
      if (url.startsWith(prefix)) return response;
    }

    if (url.includes("/snapshots")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve({ snapshots: MOCK_SNAPSHOTS }),
      } as Response);
    }

    if (url.startsWith("/api/knowledge/items/")) {
      const itemId = url.split("/").pop();
      const item = MOCK_ITEMS.find((i) => i.id === itemId);
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve(item ?? null),
      } as Response);
    }

    if (url.startsWith("/api/knowledge/items")) {
      const qs = new URLSearchParams(url.split("?")[1] || "");
      const sourceType = qs.get("source_type");
      const status = qs.get("status");
      let items = MOCK_ITEMS;
      if (sourceType) items = items.filter((i) => i.source_type === sourceType);
      if (status) items = items.filter((i) => i.status === status);
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve({ items, count: items.length }),
      } as Response);
    }

    if (url.startsWith("/api/knowledge/search")) {
      const qs = new URLSearchParams(url.split("?")[1] || "");
      const q = qs.get("q")?.toLowerCase() || "";
      const results = q ? MOCK_ITEMS.filter((i) => i.title.toLowerCase().includes(q)) : MOCK_ITEMS;
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve({ results, mode: "keyword" }),
      } as Response);
    }

    if (url === "/api/agents") {
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve(MOCK_AGENTS),
      } as Response);
    }

    if (url === "/api/knowledge/subscriptions") {
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve({ subscriptions: MOCK_SUBSCRIPTIONS }),
      } as Response);
    }

    if (url === "/api/knowledge/rules") {
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve({ rules: [] }),
      } as Response);
    }

    if (url === "/api/library/ingest") {
      return Promise.resolve({
        ok: true,
        status: 202,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve({ item_id: "lib-1", status: "pending" }),
      } as Response);
    }

    if (url === "/api/import/upload") {
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve({ status: "uploaded", filename: "srv_notes.txt", size: 5, path: "/x" }),
      } as Response);
    }

    if (url === "/api/import/embed") {
      // Mirrors import_data.py embed_files: agent_name (str) + non-empty files.
      let body: { agent_name?: unknown; files?: unknown } = {};
      try {
        body = JSON.parse(String(init?.body));
      } catch {
        body = {};
      }
      const valid =
        typeof body.agent_name === "string" &&
        Array.isArray(body.files) &&
        body.files.length > 0;
      const payload = valid
        ? {
            status: "embedded",
            embedded: true,
            count: body.files.length,
            files: body.files.map((f: unknown) => ({ filename: String(f), embedded: true })),
          }
        : { error: "agent_name is required" };
      return Promise.resolve({
        ok: valid,
        status: valid ? 200 : 400,
        statusText: valid ? "OK" : "Bad Request",
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve(payload),
        text: () => Promise.resolve(JSON.stringify(payload)),
      } as Response);
    }

    return Promise.resolve({
      ok: false,
      status: 404,
      headers: new Map([["content-type", "application/json"]]),
      json: () => Promise.resolve({}),
    } as Response);
  });
}

function parseFormData(formData: FormData): Record<string, string | File> {
  const result: Record<string, string | File> = {};
  for (const [key, value] of formData.entries()) {
    result[key] = value;
  }
  return result;
}

function callsTo(mock: ReturnType<typeof createFetchMock>, path: string) {
  return mock.mock.calls.filter((call) => String(call[0]) === path);
}

describe("LibraryApp local file upload (tsk-6gtsvj)", () => {
  let fetchMock: ReturnType<typeof createFetchMock>;

  beforeEach(() => {
    fetchMock = createFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows a file drop zone in the items view", async () => {
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("YouTube Video"), { timeout: 5000 });

    const dropZone = screen.getByLabelText(/drop files here or click to browse/i);
    expect(dropZone).toBeInTheDocument();
  });

  it("uploads a file via drag-and-drop and posts it to /api/library/ingest", async () => {
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("YouTube Video"), { timeout: 5000 });

    const dropZone = screen.getByLabelText(/drop files here or click to browse/i);
    const testFile = new File(["test content"], "test.txt", { type: "text/plain" });

    await act(async () => {
      fireEvent.drop(dropZone, { dataTransfer: { files: [testFile], types: ["Files"] } });
    });

    await waitFor(() => {
      const ingestCalls = callsTo(fetchMock, "/api/library/ingest");
      expect(ingestCalls.length).toBe(1);
      expect(ingestCalls[0][1]?.method).toBe("POST");

      const body = ingestCalls[0][1]?.body as FormData;
      expect(body).toBeInstanceOf(FormData);
      const formData = parseFormData(body);
      expect(formData["file"]).toBeInstanceOf(File);
      expect((formData["file"] as File).name).toBe("test.txt");
      expect(formData["title"]).toBe("test.txt");
      expect(formData["category"]).toBeUndefined();
    });

    // The queue is refreshed after the upload.
    await waitFor(() => {
      expect(callsTo(fetchMock, "/api/library/jobs").length).toBeGreaterThan(0);
    });
    // The drop zone no longer uses the import upload route.
    expect(callsTo(fetchMock, "/api/import/upload")).toHaveLength(0);
  });

  it("uploads a file via file picker (click) and posts it to /api/library/ingest", async () => {
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("YouTube Video"), { timeout: 5000 });

    const dropZone = screen.getByLabelText(/drop files here or click to browse/i);
    const fileInput = dropZone.querySelector('input[type="file"]') as HTMLInputElement;
    expect(fileInput).toBeInTheDocument();

    const testFile = new File(["test content"], "test.txt", { type: "text/plain" });

    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [testFile] } });
    });

    await waitFor(() => {
      const ingestCalls = callsTo(fetchMock, "/api/library/ingest");
      expect(ingestCalls.length).toBe(1);
      const formData = parseFormData(ingestCalls[0][1]?.body as FormData);
      expect(formData["file"]).toBeInstanceOf(File);
      expect((formData["file"] as File).name).toBe("test.txt");
      expect(formData["title"]).toBe("test.txt");
    });
    expect(callsTo(fetchMock, "/api/import/upload")).toHaveLength(0);
  });

  it("supports keyboard activation (Enter/Space) on the Browse button", async () => {
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("YouTube Video"), { timeout: 5000 });

    const dropZone = screen.getByLabelText(/drop files here or click to browse/i);
    // The drop zone is a plain region; it must not be a button containing a button.
    expect(dropZone).not.toHaveAttribute("role", "button");
    expect(dropZone).toHaveAttribute("role", "region");

    const browse = within(dropZone).getByRole("button", { name: "Browse" });
    const fileInput = dropZone.querySelector('input[type="file"]') as HTMLInputElement;
    expect(fileInput).toBeInTheDocument();

    const clickSpy = vi.spyOn(fileInput, "click");

    await act(async () => {
      fireEvent.keyDown(browse, { key: "Enter" });
    });
    expect(clickSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      fireEvent.keyDown(browse, { key: " " });
    });
    expect(clickSpy).toHaveBeenCalledTimes(2);
    clickSpy.mockRestore();

    // Keyboard pick still goes through the ingest route.
    const testFile = new File(["test content"], "kbd.txt", { type: "text/plain" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [testFile] } });
    });
    await waitFor(() => {
      expect(callsTo(fetchMock, "/api/library/ingest")).toHaveLength(1);
    });
    expect(callsTo(fetchMock, "/api/import/upload")).toHaveLength(0);
  });

  async function openEmbedPickerAndChooseFile() {
    render(<LibraryApp windowId="test-win" />);

    await waitFor(() => screen.getByText("YouTube Video"), { timeout: 5000 });

    fireEvent.click(screen.getByRole("button", { name: /embed files into an agent/i }));

    const modal = await screen.findByRole("dialog", { name: /embed file into agent/i });
    const picker = modal.querySelector('input[type="file"]') as HTMLInputElement;
    expect(picker).toBeInTheDocument();

    const notes = new File(["hello"], "notes.txt", { type: "text/plain" });
    await act(async () => {
      fireEvent.change(picker, { target: { files: [notes] } });
    });
    await waitFor(() => expect(within(modal).getByText("notes.txt")).toBeInTheDocument());

    // Agents are plain buttons, not listbox options.
    const agent = within(modal).getByRole("button", { name: "Agent 1" });
    expect(agent).not.toHaveAttribute("role", "option");
    expect(agent).not.toHaveAttribute("aria-selected");
    return { modal, agent };
  }

  it("embeds picked files into an agent with the server filename and agent_name", async () => {
    const { agent } = await openEmbedPickerAndChooseFile();

    fireEvent.click(agent);

    await waitFor(() => {
      const embedCalls = callsTo(fetchMock, "/api/import/embed");
      expect(embedCalls).toHaveLength(1);
      expect(JSON.parse(embedCalls[0][1]?.body as string)).toEqual({
        agent_name: "Agent 1",
        files: ["srv_notes.txt"],
      });
    });

    const uploadCalls = callsTo(fetchMock, "/api/import/upload");
    expect(uploadCalls).toHaveLength(1);
    const formData = parseFormData(uploadCalls[0][1]?.body as FormData);
    expect((formData["file"] as File).name).toBe("notes.txt");

    await waitFor(() => {
      expect(screen.getByText(/embedded 1 file into agent 1/i)).toBeInTheDocument();
    });
  });

  it("shows an error to the user when /api/import/embed returns 400", async () => {
    const failing = createFetchMock({
      "/api/import/embed": Promise.resolve({
        ok: false,
        status: 400,
        statusText: "Bad Request",
        headers: new Map([["content-type", "application/json"]]),
        json: () => Promise.resolve({ error: "agent_name is required" }),
        text: () => Promise.resolve('{"error":"agent_name is required"}'),
      } as Response),
    });
    vi.stubGlobal("fetch", failing as unknown as typeof fetch);

    const { modal, agent } = await openEmbedPickerAndChooseFile();
    fireEvent.click(agent);

    const alert = await within(modal).findByRole("alert");
    expect(alert).toHaveTextContent(/embed failed/i);
    expect(alert).toHaveTextContent(/400/);
    expect(alert).toHaveTextContent(/agent_name is required/);
  });

  it("shows an error and keeps the picker open when embed returns embedded:false", async () => {
    const failing = createFetchMock({
      "/api/import/embed": Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () =>
          Promise.resolve({
            status: "embedded",
            embedded: false,
            count: 1,
            files: [{ filename: "srv_notes.txt", embedded: false }],
          }),
        text: () =>
          Promise.resolve(
            JSON.stringify({
              status: "embedded",
              embedded: false,
              count: 1,
              files: [{ filename: "srv_notes.txt", embedded: false }],
            }),
          ),
      } as Response),
    });
    vi.stubGlobal("fetch", failing as unknown as typeof fetch);

    const { modal, agent } = await openEmbedPickerAndChooseFile();
    fireEvent.click(agent);

    const alert = await within(modal).findByRole("alert");
    expect(alert).toHaveTextContent(/embed failed/i);
    expect(alert).toHaveTextContent(/srv_notes.txt/);

    // The picker must stay open: the dialog is still in the document.
    expect(await screen.findByRole("dialog", { name: /embed file into agent/i })).toBeInTheDocument();

    // No success message about embedding a file.
    expect(screen.queryByText(/embedded 1 file into agent 1/i)).not.toBeInTheDocument();
  });

  it("clears a previous embed error when new files are picked", async () => {
    const failing = createFetchMock({
      "/api/import/embed": Promise.resolve({
        ok: true,
        status: 200,
        headers: new Map([["content-type", "application/json"]]),
        json: () =>
          Promise.resolve({
            status: "embedded",
            embedded: false,
            count: 1,
            files: [{ filename: "srv_notes.txt", embedded: false }],
          }),
        text: () => Promise.resolve(""),
      } as Response),
    });
    vi.stubGlobal("fetch", failing as unknown as typeof fetch);

    const { modal, agent } = await openEmbedPickerAndChooseFile();
    fireEvent.click(agent);
    expect(await within(modal).findByRole("alert")).toHaveTextContent(/embed failed/i);

    const picker = modal.querySelector('input[type="file"]') as HTMLInputElement;
    const other = new File(["again"], "other.txt", { type: "text/plain" });
    await act(async () => {
      fireEvent.change(picker, { target: { files: [other] } });
    });
    await waitFor(() => expect(within(modal).getByText("other.txt")).toBeInTheDocument());
    expect(within(modal).queryByRole("alert")).not.toBeInTheDocument();
  });
});
