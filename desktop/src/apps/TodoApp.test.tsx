import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
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
}));

import { TodoApp } from "./TodoApp";

// ---- Test data ----

const NOW_TS = Math.floor(Date.now() / 1000);

const TODO_LIST_A = {
  id: "tl-abc",
  owner_user_id: "user-1",
  title: "Groceries",
  created_at: NOW_TS - 3600,
  updated_at: NOW_TS - 600,
  archived_at: null,
};

const TODO_LIST_B = {
  id: "tl-def",
  owner_user_id: "user-1",
  title: "Work Tasks",
  created_at: NOW_TS - 7200,
  updated_at: NOW_TS - 300,
  archived_at: null,
};

const TODO_DETAIL = {
  ...TODO_LIST_A,
  items: [
    {
      id: "ti-1",
      list_id: "tl-abc",
      text: "Buy milk",
      done: false,
      position: 0,
      due_at: null,
      remind_at: null,
      author: "user-1",
      created_at: NOW_TS - 300,
      updated_at: NOW_TS - 300,
    },
    {
      id: "ti-2",
      list_id: "tl-abc",
      text: "Get bread",
      done: false,
      position: 1,
      due_at: NOW_TS + 86400, // tomorrow
      remind_at: null,
      author: "",
      created_at: NOW_TS - 200,
      updated_at: NOW_TS - 200,
    },
    {
      id: "ti-3",
      list_id: "tl-abc",
      text: "Pick up laundry",
      done: false,
      position: 2,
      due_at: NOW_TS - 3600, // 1 hour ago — overdue
      remind_at: null,
      author: "",
      created_at: NOW_TS - 100,
      updated_at: NOW_TS - 50,
    },
    {
      id: "ti-4",
      list_id: "tl-abc",
      text: "Already done",
      done: true,
      position: 3,
      due_at: null,
      remind_at: null,
      author: "",
      created_at: NOW_TS - 50,
      updated_at: NOW_TS - 25,
    },
  ],
};

function makeFetch(overrides: Record<string, unknown> = {}) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method ?? "GET").toUpperCase();

    // List lists
    if (u === "/api/todo" && method === "GET") {
      return {
        ok: true,
        json: async () =>
          overrides["GET /api/todo"] ?? [TODO_LIST_A, TODO_LIST_B],
      };
    }

    // Create list
    if (u === "/api/todo" && method === "POST") {
      const created = overrides["POST /api/todo"] ?? {
        id: "tl-new",
        owner_user_id: "user-1",
        title: "New List",
        created_at: NOW_TS,
        updated_at: NOW_TS,
        archived_at: null,
      };
      return { ok: true, json: async () => created };
    }

    // Get list detail
    if (u === "/api/todo/tl-abc" && method === "GET") {
      return {
        ok: true,
        json: async () => overrides["GET /api/todo/tl-abc"] ?? TODO_DETAIL,
      };
    }

    // Add item
    if (u === "/api/todo/tl-abc/items" && method === "POST") {
      const body = JSON.parse((init?.body as string) ?? "{}");
      return {
        ok: true,
        json: async () => ({
          id: "ti-new",
          list_id: "tl-abc",
          text: body.text,
          done: false,
          position: 3,
          due_at: null,
          remind_at: null,
          author: "",
          created_at: NOW_TS,
          updated_at: NOW_TS,
        }),
      };
    }

    // Patch item (toggle done, edit text)
    if (u.startsWith("/api/todo/tl-abc/items/") && method === "PATCH") {
      const body = JSON.parse((init?.body as string) ?? "{}");
      const itemId = u.split("/").pop();
      return {
        ok: true,
        json: async () => ({
          id: itemId,
          list_id: "tl-abc",
          text: body.text ?? "Buy milk",
          done: body.done ?? false,
          position: 0,
          due_at: null,
          remind_at: null,
          author: "",
          created_at: NOW_TS - 300,
          updated_at: NOW_TS,
        }),
      };
    }

    // Delete item
    if (u.startsWith("/api/todo/tl-abc/items/") && method === "DELETE") {
      return { ok: true, json: async () => ({ ok: true }) };
    }

    // Reorder
    if (u === "/api/todo/tl-abc/items/reorder" && method === "PUT") {
      return { ok: true, json: async () => ({ ok: true }) };
    }

    return { ok: true, json: async () => ({}) };
  });
}

describe("TodoApp", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ---- List rendering ----

  it("renders the todo lists from GET /api/todo", async () => {
    global.fetch = makeFetch() as typeof fetch;
    render(<TodoApp windowId="w1" />);

    await waitFor(() => {
      expect(screen.getByText("Groceries")).toBeDefined();
      expect(screen.getByText("Work Tasks")).toBeDefined();
    });
  });

  it("shows the Todo header and new-list button", async () => {
    global.fetch = makeFetch() as typeof fetch;
    render(<TodoApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("Todo")).toBeDefined());
    expect(screen.getByLabelText("New list")).toBeDefined();
  });

  it("shows the empty state when there are no lists", async () => {
    global.fetch = makeFetch({ "GET /api/todo": [] }) as typeof fetch;
    render(<TodoApp windowId="w1" />);

    await waitFor(() =>
      expect(screen.getByText(/no lists yet/i)).toBeDefined(),
    );
    expect(screen.getByText(/create one/i)).toBeDefined();
  });

  // ---- Create list ----

  it("creates a list via POST /api/todo and shows it", async () => {
    const fetchMock = makeFetch({
      "POST /api/todo": {
        id: "tl-shopping",
        owner_user_id: "user-1",
        title: "Shopping",
        created_at: NOW_TS,
        updated_at: NOW_TS,
        archived_at: null,
      },
    });
    global.fetch = fetchMock as typeof fetch;
    render(<TodoApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("Groceries")).toBeDefined());

    // Open create form
    fireEvent.click(screen.getByLabelText("New list"));
    const input = await screen.findByLabelText("New list title");
    fireEvent.change(input, { target: { value: "Shopping" } });
    fireEvent.click(screen.getByLabelText("Create list"));

    await waitFor(() => {
      const postCalls = (
        fetchMock.mock.calls as [string, RequestInit?][]
      ).filter(
        ([u, init]) =>
          String(u) === "/api/todo" &&
          (init?.method ?? "GET").toUpperCase() === "POST",
      );
      expect(postCalls.length).toBeGreaterThan(0);
      const body = JSON.parse(postCalls[0]![1]!.body as string);
      expect(body.title).toBe("Shopping");
    });

    await waitFor(() => expect(screen.getByText("Shopping")).toBeDefined());
  });

  // ---- Item display ----

  it("shows items when a list is selected", async () => {
    global.fetch = makeFetch() as typeof fetch;
    render(<TodoApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("Groceries")).toBeDefined());
    fireEvent.click(screen.getByText("Groceries"));

    await waitFor(() => {
      expect(screen.getByText("Buy milk")).toBeDefined();
      expect(screen.getByText("Get bread")).toBeDefined();
    });
  });

  it("splits incomplete and completed items into sections", async () => {
    global.fetch = makeFetch() as typeof fetch;
    render(<TodoApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("Groceries")).toBeDefined());
    fireEvent.click(screen.getByText("Groceries"));

    await waitFor(() => {
      expect(screen.getByLabelText("Incomplete tasks")).toBeDefined();
      expect(screen.getByLabelText("Completed tasks")).toBeDefined();
      expect(screen.getByText(/completed/i)).toBeDefined();
    });

    // Completed item should show in completed section
    const completedSection = screen.getByLabelText("Completed tasks");
    expect(completedSection).toBeDefined();
    // "Already done" has done:true — should be in the completed section
    expect(screen.getByText("Already done")).toBeDefined();
    // "Pick up laundry" has done:false — should be in the incomplete section
    const incompleteSection = screen.getByLabelText("Incomplete tasks");
    expect(incompleteSection.textContent).toContain("Pick up laundry");
  });

  // ---- Toggle done (optimistic) ----

  it("toggles a task done via PATCH and shows optimistic update", async () => {
    const fetchMock = makeFetch();
    global.fetch = fetchMock as typeof fetch;
    render(<TodoApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("Groceries")).toBeDefined());
    fireEvent.click(screen.getByText("Groceries"));

    await waitFor(() => expect(screen.getByText("Buy milk")).toBeDefined());

    // Click the checkbox for "Buy milk"
    const checkboxes = screen.getAllByLabelText("Mark task done");
    fireEvent.click(checkboxes[0]!);

    await waitFor(() => {
      const patchCalls = (
        fetchMock.mock.calls as [string, RequestInit?][]
      ).filter(
        ([u, init]) =>
          String(u).startsWith("/api/todo/tl-abc/items/ti-1") &&
          (init?.method ?? "GET").toUpperCase() === "PATCH",
      );
      expect(patchCalls.length).toBeGreaterThan(0);
      const body = JSON.parse(patchCalls[0]![1]!.body as string);
      expect(body.done).toBe(true);
    });
  });

  // ---- Delete item ----

  it("deletes an item via DELETE", async () => {
    const fetchMock = makeFetch();
    global.fetch = fetchMock as typeof fetch;
    render(<TodoApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("Groceries")).toBeDefined());
    fireEvent.click(screen.getByText("Groceries"));

    await waitFor(() => expect(screen.getByText("Buy milk")).toBeDefined());

    // Find delete button (hover-revealed, but still in DOM)
    const deleteButtons = screen.getAllByLabelText("Delete task");
    fireEvent.click(deleteButtons[0]!);

    await waitFor(() => {
      const delCalls = (
        fetchMock.mock.calls as [string, RequestInit?][]
      ).filter(
        ([u, init]) =>
          String(u).startsWith("/api/todo/tl-abc/items/ti-1") &&
          (init?.method ?? "GET").toUpperCase() === "DELETE",
      );
      expect(delCalls.length).toBeGreaterThan(0);
    });
  });

  // ---- Add item ----

  it("adds a new item via POST /items", async () => {
    const fetchMock = makeFetch();
    global.fetch = fetchMock as typeof fetch;
    render(<TodoApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("Groceries")).toBeDefined());
    fireEvent.click(screen.getByText("Groceries"));

    await waitFor(() => expect(screen.getByText("Buy milk")).toBeDefined());

    const input = screen.getByLabelText("New task text");
    fireEvent.change(input, { target: { value: "Buy eggs" } });
    fireEvent.click(screen.getByLabelText("Add task"));

    await waitFor(() => {
      const postCalls = (
        fetchMock.mock.calls as [string, RequestInit?][]
      ).filter(
        ([u, init]) =>
          String(u) === "/api/todo/tl-abc/items" &&
          (init?.method ?? "GET").toUpperCase() === "POST",
      );
      expect(postCalls.length).toBeGreaterThan(0);
      const body = JSON.parse(postCalls[0]![1]!.body as string);
      expect(body.text).toBe("Buy eggs");
    });
  });

  // ---- Due date display + overdue highlighting ----

  it("displays due dates and highlights overdue items", async () => {
    global.fetch = makeFetch() as typeof fetch;
    render(<TodoApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("Groceries")).toBeDefined());
    fireEvent.click(screen.getByText("Groceries"));

    await waitFor(() => {
      // "Get bread" has a tomorrow due date — should show but not overdue
      expect(screen.getByText("Get bread")).toBeDefined();
      // Overdue text indicator for "Pick up laundry" (overdue + done — shows in completed section)
      expect(screen.getByText(/overdue/i)).toBeDefined();
    });
  });

  // ---- Move up/down ----

  it("reorders items via PUT /reorder", async () => {
    const fetchMock = makeFetch();
    global.fetch = fetchMock as typeof fetch;
    render(<TodoApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("Groceries")).toBeDefined());
    fireEvent.click(screen.getByText("Groceries"));

    await waitFor(() => expect(screen.getByText("Buy milk")).toBeDefined());

    // "Buy milk" is position 0, so move-up should be disabled.
    // Move it down (to pos 1)
    const moveDownButtons = screen.getAllByLabelText("Move task down");
    fireEvent.click(moveDownButtons[0]!);

    await waitFor(() => {
      const reorderCalls = (
        fetchMock.mock.calls as [string, RequestInit?][]
      ).filter(
        ([u, init]) =>
          String(u) === "/api/todo/tl-abc/items/reorder" &&
          (init?.method ?? "GET").toUpperCase() === "PUT",
      );
      expect(reorderCalls.length).toBeGreaterThan(0);
      const body = JSON.parse(reorderCalls[0]![1]!.body as string);
      expect(body.items).toBeDefined();
      expect(Array.isArray(body.items)).toBe(true);
    });
  });

  // ---- Inline edit ----

  it("edits an item inline via PATCH", async () => {
    const fetchMock = makeFetch();
    global.fetch = fetchMock as typeof fetch;
    render(<TodoApp windowId="w1" />);

    await waitFor(() => expect(screen.getByText("Groceries")).toBeDefined());
    fireEvent.click(screen.getByText("Groceries"));

    await waitFor(() => expect(screen.getByText("Buy milk")).toBeDefined());

    // Click edit button
    const editButtons = screen.getAllByLabelText("Edit task");
    fireEvent.click(editButtons[0]!);

    // Edit the text
    const textarea = await screen.findByLabelText("Edit task text");
    fireEvent.change(textarea, { target: { value: "Buy almond milk" } });
    fireEvent.click(screen.getByLabelText("Save edit"));

    await waitFor(() => {
      const patchCalls = (
        fetchMock.mock.calls as [string, RequestInit?][]
      ).filter(
        ([u, init]) =>
          String(u).startsWith("/api/todo/tl-abc/items/ti-1") &&
          (init?.method ?? "GET").toUpperCase() === "PATCH",
      );
      expect(patchCalls.length).toBeGreaterThan(0);
      const body = JSON.parse(patchCalls[0]![1]!.body as string);
      expect(body.text).toBe("Buy almond milk");
    });
  });
});
