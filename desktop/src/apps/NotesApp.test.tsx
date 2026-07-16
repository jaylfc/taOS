import { render, screen, act, waitFor, within, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { NotesApp, TodoApp } from "./NotesApp";

function mockFetch(
  resolver: (url: string, init?: RequestInit) => { ok: boolean; status?: number; body: unknown },
) {
  return vi.fn().mockImplementation((input: string, init?: RequestInit) => {
    const hit = resolver(input, init);
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

function past(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

const noteDoc = {
  id: "note-1",
  kind: "note" as const,
  title: "Project kickoff",
  updated_at: past(30),
  archived_at: null,
};

const listDoc = {
  id: "list-1",
  kind: "list" as const,
  title: "Sprint backlog",
  updated_at: past(10),
  archived_at: null,
};

describe("NotesApp", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches the note list from /api/notes on mount", async () => {
    const fetchMock = mockFetch(() => ({ ok: true, body: [] }));
    vi.stubGlobal("fetch", fetchMock);
    render(<NotesApp windowId="w1" />);
    await flush();
    expect(fetchMock).toHaveBeenCalledWith("/api/notes");
  });

  it("shows the empty state when there are no notes yet", async () => {
    vi.stubGlobal("fetch", mockFetch(() => ({ ok: true, body: [] })));
    render(<NotesApp windowId="w1" />);
    await flush();
    await waitFor(() => expect(screen.getByText(/no notes yet/i)).toBeTruthy());
    expect(screen.getByRole("button", { name: /create one/i })).toBeTruthy();
  });

  it("renders only notes, filtering out lists that share the same API", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(() => ({ ok: true, body: [noteDoc, listDoc] })),
    );
    render(<NotesApp windowId="w1" />);
    await flush();

    await waitFor(() =>
      expect(screen.getByText("Project kickoff")).toBeTruthy(),
    );
    // The list doc lives in the same store but NotesApp must hide it.
    expect(screen.queryByText("Sprint backlog")).toBeNull();
    // The relative updated time is rendered from updated_at.
    expect(screen.getByText(/30m ago/i)).toBeTruthy();
  });

  it("loads the selected note's detail via /api/notes/:id", async () => {
    const fetchMock = mockFetch((url) => {
      if (url === "/api/notes") {
        return { ok: true, body: [noteDoc] };
      }
      if (url === `/api/notes/${noteDoc.id}`) {
        return {
          ok: true,
          body: {
            id: noteDoc.id,
            kind: "note",
            title: noteDoc.title,
            updated_at: noteDoc.updated_at,
            entries: [
              {
                id: "entry-1",
                text: "Draft the RFC",
                done: false,
                author: null,
                created_at: past(5),
              },
            ],
            members: [],
          },
        };
      }
      throw new Error(`Unmocked fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NotesApp windowId="w1" />);
    await flush();

    await waitFor(() => expect(screen.getByText("Project kickoff")).toBeTruthy());
    const item = screen.getByText("Project kickoff").closest("button")!;
    await act(async () => {
      fireEvent.click(item);
    });
    await waitFor(() => expect(screen.getByText("Draft the RFC")).toBeTruthy());
    expect(fetchMock).toHaveBeenCalledWith(`/api/notes/${noteDoc.id}`);
  });

  it("creates a note via the inline form and selects it", async () => {
    let createdStore: unknown[] = [noteDoc];
    const fetchMock = mockFetch((url, init) => {
      if (url === "/api/notes" && (!init || init.method == null || init.method === "GET")) {
        return { ok: true, body: createdStore };
      }
      if (url === "/api/notes" && init?.method === "POST") {
        const newDoc = {
          id: "note-2",
          kind: "note",
          title: "Standup notes",
          updated_at: past(0),
          archived_at: null,
        };
        createdStore = [newDoc, ...createdStore];
        return { ok: true, body: newDoc };
      }
      if (url === `/api/notes/note-2`) {
        return {
          ok: true,
          body: {
            id: "note-2",
            kind: "note",
            title: "Standup notes",
            updated_at: past(0),
            entries: [],
            members: [],
          },
        };
      }
      throw new Error(`Unmocked fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NotesApp windowId="w1" />);
    await flush();

    // Open the create form.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /new note/i }));
    });
    const titleInput = await screen.findByLabelText(/new note title/i);
    await act(async () => {
      fireEvent.change(titleInput, { target: { value: "Standup notes" } });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /create note/i }));
    });

    await waitFor(() =>
      expect(screen.getAllByText("Standup notes").length).toBeGreaterThan(0),
    );
    expect(fetchMock).toHaveBeenCalledWith("/api/notes", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ kind: "note", title: "Standup notes" }),
    }));
  });
});

describe("TodoApp", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders only lists, filtering out notes that share the same API", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(() => ({ ok: true, body: [noteDoc, listDoc] })),
    );
    render(<TodoApp windowId="w2" />);
    await flush();

    await waitFor(() => expect(screen.getByText("Sprint backlog")).toBeTruthy());
    expect(screen.queryByText("Project kickoff")).toBeNull();
  });

  it("shows the todo empty state when there are no lists", async () => {
    vi.stubGlobal("fetch", mockFetch(() => ({ ok: true, body: [] })));
    render(<TodoApp windowId="w2" />);
    await flush();
    await waitFor(() => expect(screen.getByText(/no lists yet/i)).toBeTruthy());
  });
});
