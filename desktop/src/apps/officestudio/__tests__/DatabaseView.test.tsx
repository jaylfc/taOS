import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { DatabaseView } from "../DatabaseView";

/* A tiny in-memory fake of the /api/office/docs backend, keyed by URL and
 * method, mirroring the convention used for GameStudioApp's fetch mock. */
type StoredDoc = {
  id: string;
  kind: string;
  title: string;
  content: string;
  created_at: number;
  updated_at: number;
};

function makeFetchMock(seed: StoredDoc[] = []) {
  const docs = new Map<string, StoredDoc>(seed.map((d) => [d.id, d]));
  let nextId = 1;

  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();

    if (url === "/api/office/docs" && method === "GET") {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve(
            [...docs.values()].map(({ id, kind, title, updated_at }) => ({
              id,
              kind,
              title,
              updated_at,
            })),
          ),
      } as Response);
    }

    if (url === "/api/office/docs" && method === "POST") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      const id = `doc-${nextId++}`;
      const now = Date.now() / 1000;
      const record: StoredDoc = {
        id,
        kind: body.kind,
        title: body.title,
        content: body.content,
        created_at: now,
        updated_at: now,
      };
      docs.set(id, record);
      return Promise.resolve({ ok: true, json: () => Promise.resolve(record) } as Response);
    }

    const docMatch = url.match(/^\/api\/office\/docs\/([^/]+)$/);
    if (docMatch) {
      const id = docMatch[1]!;
      if (method === "GET") {
        const record = docs.get(id);
        if (!record) {
          return Promise.resolve({
            ok: false,
            status: 404,
            json: () => Promise.resolve({ error: "not found" }),
          } as Response);
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve(record) } as Response);
      }
      if (method === "PUT") {
        const existing = docs.get(id);
        const body = JSON.parse(String(init?.body ?? "{}"));
        const record: StoredDoc = {
          id,
          kind: body.kind ?? existing?.kind ?? "db",
          title: body.title ?? existing?.title ?? "",
          content: body.content ?? existing?.content ?? "",
          created_at: existing?.created_at ?? Date.now() / 1000,
          updated_at: Date.now() / 1000,
        };
        docs.set(id, record);
        return Promise.resolve({ ok: true, json: () => Promise.resolve(record) } as Response);
      }
    }

    return Promise.resolve({ ok: true, json: () => Promise.resolve([]) } as Response);
  });
}

function lastCallTo(mock: ReturnType<typeof vi.fn>, url: string, method: string) {
  const calls = mock.mock.calls as [RequestInfo | URL, RequestInit | undefined][];
  const match = [...calls]
    .reverse()
    .find(([u, init]) => String(u) === url && (init?.method ?? "GET").toUpperCase() === method);
  if (!match) throw new Error(`no ${method} call to ${url} recorded`);
  return match[1];
}

describe("DatabaseView", () => {
  let fetchMock: ReturnType<typeof makeFetchMock>;

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the default table with a title, Name column and one row", async () => {
    fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(<DatabaseView />);

    expect(await screen.findByLabelText("Table title")).toHaveValue("Untitled table");
    expect(screen.getAllByLabelText("Column name")[0]).toHaveValue("Name");
    expect(screen.getByLabelText("Name, row 1")).toBeDefined();
    expect(await screen.findByText("No saved tables yet")).toBeDefined();
  });

  it("creates a new doc on Save with kind db and the default column/row shape", async () => {
    fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(<DatabaseView />);
    await screen.findByText("No saved tables yet");

    fireEvent.change(screen.getByLabelText("Table title"), { target: { value: "Contacts" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => lastCallTo(fetchMock, "/api/office/docs", "POST"));
    const init = lastCallTo(fetchMock, "/api/office/docs", "POST")!;
    const body = JSON.parse(String(init.body));
    expect(body.kind).toBe("db");
    expect(body.title).toBe("Contacts");
    const content = JSON.parse(body.content);
    expect(content.columns).toHaveLength(1);
    expect(content.columns[0].name).toBe("Name");
    expect(content.columns[0].type).toBe("text");
    expect(content.rows).toHaveLength(1);
  });

  it("adds a column and a row, edits a cell, and saves the full content shape", async () => {
    fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(<DatabaseView />);
    await screen.findByText("No saved tables yet");

    fireEvent.click(screen.getByRole("button", { name: "Column" }));
    fireEvent.click(screen.getByRole("button", { name: "Row" }));

    const names = screen.getAllByLabelText("Column name");
    expect(names).toHaveLength(2);
    fireEvent.change(names[1], { target: { value: "Email" } });

    fireEvent.change(screen.getByLabelText("Name, row 1"), { target: { value: "Ada" } });
    fireEvent.change(screen.getByLabelText("Email, row 1"), { target: { value: "ada@example.com" } });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => lastCallTo(fetchMock, "/api/office/docs", "POST"));
    const init = lastCallTo(fetchMock, "/api/office/docs", "POST")!;
    const body = JSON.parse(String(init.body));
    const content = JSON.parse(body.content);
    expect(content.columns.map((c: { name: string }) => c.name)).toEqual(["Name", "Email"]);
    expect(content.rows).toHaveLength(2);
    const [nameCol, emailCol] = content.columns;
    expect(content.rows[0].cells[nameCol.id]).toBe("Ada");
    expect(content.rows[0].cells[emailCol.id]).toBe("ada@example.com");
  });

  it("loads an existing doc and renders its saved columns and rows", async () => {
    const seedContent = JSON.stringify({
      version: 1,
      columns: [
        { id: "c1", name: "Task", type: "text" },
        { id: "c2", name: "Done", type: "checkbox" },
      ],
      rows: [
        { id: "r1", cells: { c1: "Ship database view", c2: true } },
        { id: "r2", cells: { c1: "Write tests", c2: false } },
      ],
    });
    fetchMock = makeFetchMock([
      {
        id: "doc-seed",
        kind: "db",
        title: "Roadmap",
        content: seedContent,
        created_at: 1,
        updated_at: 2,
      },
    ]);
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    render(<DatabaseView />);

    fireEvent.click(await screen.findByText("Roadmap"));

    expect(await screen.findByLabelText("Table title")).toHaveValue("Roadmap");
    const columnNames = screen.getAllByLabelText("Column name").map((el) => (el as HTMLInputElement).value);
    expect(columnNames).toEqual(["Task", "Done"]);
    expect(screen.getByLabelText("Task, row 1")).toHaveValue("Ship database view");
    expect(screen.getByLabelText("Task, row 2")).toHaveValue("Write tests");
    expect(screen.getByLabelText("Done, row 1")).toHaveProperty("checked", true);
    expect(screen.getByLabelText("Done, row 2")).toHaveProperty("checked", false);
  });
});
