import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";

vi.mock("@/components/ui", () => ({
  Button: ({
    children,
    onClick,
    disabled,
    ...rest
  }: React.ButtonHTMLAttributes<HTMLButtonElement> & { children?: React.ReactNode }) => (
    <button onClick={onClick} disabled={disabled} {...rest}>
      {children}
    </button>
  ),
  Textarea: ({
    onChange,
    value,
    placeholder,
    ...rest
  }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) => (
    <textarea onChange={onChange} value={value} placeholder={placeholder} {...rest} />
  ),
}));

import { NotesApp, TodoApp } from "../NotesApp";

const NOTE_LIST = [
  { id: "note-1", kind: "note", title: "My First Note", updated_at: new Date().toISOString(), archived_at: null },
  { id: "note-2", kind: "note", title: "Shopping Ideas", updated_at: new Date().toISOString(), archived_at: null },
];

const NOTE_DETAIL = {
  id: "note-1",
  kind: "note",
  title: "My First Note",
  updated_at: new Date().toISOString(),
  entries: [
    { id: "entry-1", text: "Buy milk", done: false, author: null, created_at: new Date().toISOString() },
  ],
  members: [],
};

function makeFetch(overrides: Record<string, unknown> = {}) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();

    if (u === "/api/notes" && method === "GET") {
      return { ok: true, json: async () => overrides["GET /api/notes"] ?? NOTE_LIST };
    }
    if (u === "/api/notes" && method === "POST") {
      const created = overrides["POST /api/notes"] ?? {
        id: "note-new",
        kind: "note",
        title: "New Note",
        updated_at: new Date().toISOString(),
        archived_at: null,
      };
      return { ok: true, json: async () => created };
    }
    if (u.startsWith("/api/notes/note-") && method === "GET" && !u.includes("/entries") && !u.includes("/members") && !u.includes("/history")) {
      const key = `GET ${u}`;
      const detail = overrides[key] ?? overrides["GET /api/notes/note-1"] ?? NOTE_DETAIL;
      return { ok: true, json: async () => detail };
    }
    if (u.startsWith("/api/notes/note-1/members") && method === "POST") {
      const body = JSON.parse((init?.body as string) ?? "{}");
      return {
        ok: true,
        json: async () => overrides["POST /api/notes/note-1/members"] ?? body,
      };
    }
    if (u.startsWith("/api/notes/note-1/entries") && method === "POST") {
      return {
        ok: true,
        json: async () => ({ id: "entry-new", text: "New entry", done: false, author: null, created_at: new Date().toISOString() }),
      };
    }
    return { ok: true, json: async () => ({}) };
  });
}

describe("NotesApp", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the note list from GET /api/notes", async () => {
    global.fetch = makeFetch() as typeof fetch;
    render(<NotesApp windowId="w1" />);

    await waitFor(() => {
      expect(screen.getByText("My First Note")).toBeDefined();
      expect(screen.getByText("Shopping Ideas")).toBeDefined();
    });
  });

  it("shows the Notes header and a new-note button", async () => {
    global.fetch = makeFetch() as typeof fetch;
    render(<NotesApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("Notes")).toBeDefined());
    expect(screen.getByLabelText("New note")).toBeDefined();
  });

  it("creates a note via POST /api/notes and shows it in the list", async () => {
    const fetchMock = makeFetch({
      "POST /api/notes": {
        id: "note-new",
        kind: "note",
        title: "New Note",
        updated_at: new Date().toISOString(),
        archived_at: null,
      },
    });
    global.fetch = fetchMock as typeof fetch;
    render(<NotesApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("My First Note")).toBeDefined());

    fireEvent.click(screen.getByLabelText("New note"));

    const input = await screen.findByLabelText("New note title");
    fireEvent.change(input, { target: { value: "New Note" } });

    fireEvent.click(screen.getByLabelText("Create note"));

    await waitFor(() => {
      const postCalls = (fetchMock.mock.calls as [string, RequestInit?][]).filter(
        ([u, init]) => String(u) === "/api/notes" && (init?.method ?? "GET").toUpperCase() === "POST",
      );
      expect(postCalls.length).toBeGreaterThan(0);
      const body = JSON.parse(postCalls[0]![1]!.body as string);
      expect(body.kind).toBe("note");
      expect(body.title).toBe("New Note");
    });

    await waitFor(() => expect(screen.getByText("New Note")).toBeDefined());
  });

  it("shows note entries when a note is selected", async () => {
    global.fetch = makeFetch() as typeof fetch;
    render(<NotesApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("My First Note")).toBeDefined());
    fireEvent.click(screen.getByText("My First Note"));

    await waitFor(() => expect(screen.getByText("Buy milk")).toBeDefined());
  });

  it("posts the share contract with permission + action when adding an agent member", async () => {
    const fetchMock = makeFetch({
      "POST /api/notes/note-1/members": {
        member_type: "agent",
        member_id: "researcher",
        permission: "contributor",
        action: "research",
        standing_instruction: "Focus on recent papers",
      },
    });
    global.fetch = fetchMock as typeof fetch;
    render(<NotesApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("My First Note")).toBeDefined());
    fireEvent.click(screen.getByText("My First Note"));

    await waitFor(() => expect(screen.getByLabelText("Share note")).toBeDefined());
    fireEvent.click(screen.getByLabelText("Share note"));

    await waitFor(() => expect(screen.getByLabelText("Member type")).toBeDefined());

    // Switch to agent
    fireEvent.click(screen.getByRole("button", { name: /agent/i }));

    // Fill member id
    const memberInput = screen.getByLabelText("Member ID or email");
    fireEvent.change(memberInput, { target: { value: "researcher" } });

    // Set permission to contributor
    fireEvent.click(screen.getByRole("button", { name: "Contributor" }));

    // Set action to research
    fireEvent.click(screen.getByRole("button", { name: "Research" }));

    // Fill standing instruction
    const instructionArea = screen.getByLabelText("Standing instruction");
    fireEvent.change(instructionArea, { target: { value: "Focus on recent papers" } });

    // Submit
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => {
      const postCalls = (fetchMock.mock.calls as [string, RequestInit?][]).filter(
        ([u, init]) =>
          String(u).startsWith("/api/notes/note-1/members") &&
          (init?.method ?? "GET").toUpperCase() === "POST",
      );
      expect(postCalls.length).toBeGreaterThan(0);
      const body = JSON.parse(postCalls[0]![1]!.body as string);
      expect(body.member_type).toBe("agent");
      expect(body.member_id).toBe("researcher");
      expect(body.permission).toBe("contributor");
      expect(body.action).toBe("research");
      expect(body.standing_instruction).toBe("Focus on recent papers");
    });
  });
});

// ---- Todo (list) variant ----

const MIXED_LIST = [
  { id: "note-1", kind: "note", title: "My First Note", updated_at: new Date().toISOString(), archived_at: null },
  { id: "list-1", kind: "list", title: "Groceries", updated_at: new Date().toISOString(), archived_at: null },
];

const LIST_DETAIL = {
  id: "list-1",
  kind: "list",
  title: "Groceries",
  updated_at: new Date().toISOString(),
  entries: [
    { id: "task-1", text: "Buy milk", done: false, author: null, created_at: new Date().toISOString() },
  ],
  members: [],
};

function makeListFetch() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();

    if (u === "/api/notes" && method === "GET") {
      return { ok: true, json: async () => MIXED_LIST };
    }
    if (u === "/api/notes/list-1" && method === "GET") {
      return { ok: true, json: async () => LIST_DETAIL };
    }
    if (u.startsWith("/api/notes/list-1/entries/task-1") && method === "PATCH") {
      return { ok: true, json: async () => ({ ok: true }) };
    }
    return { ok: true, json: async () => ({}) };
  });
}

describe("TodoApp", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the Todo header and only list-kind docs", async () => {
    global.fetch = makeListFetch() as typeof fetch;
    render(<TodoApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("Todo")).toBeDefined());
    // A list doc shows; a note doc is filtered out.
    await waitFor(() => expect(screen.getByText("Groceries")).toBeDefined());
    expect(screen.queryByText("My First Note")).toBeNull();
    expect(screen.getByLabelText("New list")).toBeDefined();
  });

  it("toggles a task done via PATCH /entries/{id}", async () => {
    const fetchMock = makeListFetch();
    global.fetch = fetchMock as typeof fetch;
    render(<TodoApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("Groceries")).toBeDefined());
    fireEvent.click(screen.getByText("Groceries"));

    await waitFor(() => expect(screen.getByText("Buy milk")).toBeDefined());
    fireEvent.click(screen.getByLabelText("Mark task done"));

    await waitFor(() => {
      const patch = (fetchMock.mock.calls as [string, RequestInit?][]).filter(
        ([u, init]) =>
          String(u) === "/api/notes/list-1/entries/task-1" &&
          (init?.method ?? "GET").toUpperCase() === "PATCH",
      );
      expect(patch.length).toBeGreaterThan(0);
      const body = JSON.parse(patch[0]![1]!.body as string);
      expect(body.done).toBe(true);
    });
  });
});
