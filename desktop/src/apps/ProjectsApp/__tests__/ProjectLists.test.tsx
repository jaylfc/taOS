import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act, fireEvent, waitFor, within } from "@testing-library/react";
import type { Project } from "@/lib/projects";
import { ProjectLists } from "../ProjectLists";

vi.mock("../../../hooks/use-is-mobile", () => ({
  useIsMobile: vi.fn(),
}));
import { useIsMobile } from "../../../hooks/use-is-mobile";

const fakeProject: Project = {
  id: "p1",
  slug: "p1",
  name: "P1",
  description: "",
  status: "active",
  created_by: "u1",
  created_at: 0,
  updated_at: 0,
};

function ok(data: unknown) {
  return { ok: true, status: 200, json: async () => data };
}

describe("ProjectLists", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let listsData: { id: string; project_id: string; title: string; description: string; status: string; created_by: string; created_at: number; updated_at: number }[];
  let entriesData: { id: string; list_id: string; project_id: string; text: string; original_text: string; category: string | null; status: string; done: number; author_kind: string; author_id: string; edited_by: string | null; position: number; created_at: number; updated_at: number }[];

  beforeEach(() => {
    listsData = [
      { id: "lst-1", project_id: "p1", title: "Shopping", description: "", status: "active", created_by: "u1", created_at: 0, updated_at: 0 },
    ];
    entriesData = [
      { id: "ent-1", list_id: "lst-1", project_id: "p1", text: "Milk", original_text: "Milk", category: "groceries", status: "new", done: 0, author_kind: "user", author_id: "u1", edited_by: null, position: 0, created_at: 0, updated_at: 0 },
      { id: "ent-2", list_id: "lst-1", project_id: "p1", text: "Bread", original_text: "Whole grain bread", category: null, status: "actioned", done: 1, author_kind: "user", author_id: "u1", edited_by: "u1", position: 1, created_at: 0, updated_at: 0 },
      { id: "ent-3", list_id: "lst-1", project_id: "p1", text: "Call plumber", original_text: "Call plumber", category: null, status: "discuss", done: 0, author_kind: "user", author_id: "u1", edited_by: null, position: 2, created_at: 0, updated_at: 0 },
      { id: "ent-4", list_id: "lst-1", project_id: "p1", text: "Review PR", original_text: "Review PR", category: null, status: "seen", done: 0, author_kind: "user", author_id: "u1", edited_by: null, position: 3, created_at: 0, updated_at: 0 },
    ];

    fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url === "/api/projects/p2/lists") {
        return Promise.resolve(ok({ items: [{ id: "lst-p2", project_id: "p2", title: "Other project list", description: "", status: "active", created_by: "u1", created_at: 0, updated_at: 0 }] }));
      }
      if (url === "/api/projects/p2/lists/lst-p2/entries") {
        return Promise.resolve(ok({ items: [] }));
      }
      if (url === "/api/projects/p1/lists") {
        if (init?.method === "POST") {
          // The mock has to behave like the server: a created list is in the
          // NEXT list response. It used to return a fixed array, so a test
          // could not tell a refreshed rail from a stale one.
          const created = { id: "lst-new", project_id: "p1", title: "New list", description: "", status: "active", created_by: "u1", created_at: 0, updated_at: 0 };
          listsData.push(created);
          return Promise.resolve(ok(created));
        }
        return Promise.resolve(ok({ items: [...listsData] }));
      }
      // A freshly created list serves an empty entry set, like the real route.
      if (url === "/api/projects/p1/lists/lst-new/entries") {
        return Promise.resolve(ok({ items: [] }));
      }
      if (url === "/api/projects/p1/lists/lst-1/entries") {
        if (init?.method === "POST") {
          const newEntry = { id: "ent-new", list_id: "lst-1", project_id: "p1", text: "new entry", original_text: "new entry", category: null as string | null, status: "new" as string, done: 0 as number, author_kind: "user" as string, author_id: "u1" as string, edited_by: null as string | null, position: entriesData.length as number, created_at: 0 as number, updated_at: 0 as number };
          entriesData.push(newEntry);
          return Promise.resolve(ok(newEntry));
        }
        return Promise.resolve(ok({ items: [...entriesData] }));
      }
      if (url.startsWith("/api/projects/p1/lists/lst-1/entries/") && init?.method === "PATCH") {
        const entryId = url.split("/").pop()!;
        const entry = entriesData.find((e) => e.id === entryId);
        if (!entry) return Promise.resolve(ok({}));
        const patch = JSON.parse(init.body as string);
        Object.assign(entry, patch);
        return Promise.resolve(ok({ ...entry }));
      }
      if (url === "/api/projects/p1/lists/lst-1" && init?.method === "DELETE") {
        listsData = listsData.filter((l) => l.id !== "lst-1");
        return Promise.resolve(ok({ ok: true }));
      }
      if (url.startsWith("/api/projects/p1/lists/lst-1/entries/") && init?.method === "DELETE") {
        const entryId = url.split("/").pop()!;
        entriesData = entriesData.filter((e) => e.id !== entryId);
        return Promise.resolve(ok({ ok: true }));
      }
      return Promise.resolve(ok({}));
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  it("renders lists rail and entries for the first list", async () => {
    await act(async () => {
      render(<ProjectLists project={fakeProject} />);
    });
    const shoppingItems = screen.getAllByText("Shopping");
    expect(shoppingItems.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Milk")).toBeInTheDocument();
    expect(screen.getByText("Bread")).toBeInTheDocument();
  });

  it("renders status pills with correct colors", async () => {
    await act(async () => {
      render(<ProjectLists project={fakeProject} />);
    });
    const newPills = screen.getAllByText("new");
    expect(newPills.length).toBeGreaterThanOrEqual(1);
    expect(newPills[0]!.className).toContain("bg-blue-500/15");
    const actionedPills = screen.getAllByText("actioned");
    expect(actionedPills.length).toBeGreaterThanOrEqual(1);
    const discussPills = screen.getAllByText("discuss");
    expect(discussPills.length).toBeGreaterThanOrEqual(1);
    const seenPills = screen.getAllByText("seen");
    expect(seenPills.length).toBeGreaterThanOrEqual(1);
  });

  it("shows the original text indicator when text is tidied", async () => {
    await act(async () => {
      render(<ProjectLists project={fakeProject} />);
    });
    expect(screen.getByText("original")).toBeInTheDocument();
  });

  it("quick-add creates a new entry on Enter", async () => {
    await act(async () => {
      render(<ProjectLists project={fakeProject} />);
    });
    const input = screen.getByLabelText(/quick add/i);
    fireEvent.change(input, { target: { value: "new entry" } });
    await act(async () => {
      fireEvent.submit(input.closest("form")!);
    });
    await waitFor(() => expect(screen.getByText("new entry")).toBeInTheDocument());
  });

  it("done toggle marks entry as done and strikes through text", async () => {
    await act(async () => {
      render(<ProjectLists project={fakeProject} />);
    });
    const checkbox = screen.getByLabelText(/mark milk as done/i);
    fireEvent.click(checkbox);
    await waitFor(() => expect(screen.getByText("Milk").className).toContain("line-through"));
  });

  // The rail is what the user reads to know their list exists. Both of these
  // failed before the fix: refreshLists() fetched and threw the result away
  // (only the mount effect ever called setLists), so a created list never
  // appeared and a deleted one never left.
  it("a created list appears in the rail", async () => {
    await act(async () => {
      render(<ProjectLists project={fakeProject} />);
    });
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Create new list"));
    });
    const dialog = screen.getByRole("dialog", { name: /new list/i });
    const input = within(dialog).getByRole("textbox");
    fireEvent.change(input, { target: { value: "New list" } });
    await act(async () => {
      fireEvent.submit(input.closest("form")!);
    });
    await waitFor(() => {
      const rail = screen.getByLabelText("Project lists");
      expect(within(rail).getByText("New list")).toBeInTheDocument();
    });
  });

  it("switching project does not keep the previous project's selected list", async () => {
    // Neither ProjectWorkspace nor ProjectLists is keyed by project, so the
    // component is reused across a switch and the old selection survived.
    let view: ReturnType<typeof render>;
    await act(async () => {
      view = render(<ProjectLists project={fakeProject} />);
    });
    await act(async () => {
      view!.rerender(<ProjectLists project={{ ...fakeProject, id: "p2", slug: "p2", name: "P2" }} />);
    });
    await waitFor(() => expect(screen.getAllByText("Other project list").length).toBeGreaterThanOrEqual(1));
    // the old project's entries must not still be on screen
    expect(screen.queryByText("Milk")).not.toBeInTheDocument();
    const badFetch = fetchMock.mock.calls.find(([u]) => String(u) === "/api/projects/p2/lists/lst-1/entries");
    expect(badFetch).toBeUndefined();
  });

  it("a whitespace-only list name creates nothing", async () => {
    await act(async () => {
      render(<ProjectLists project={fakeProject} />);
    });
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Create new list"));
    });
    const dialog = screen.getByRole("dialog", { name: /new list/i });
    const input = within(dialog).getByRole("textbox");
    fireEvent.change(input, { target: { value: "   " } });
    await act(async () => {
      fireEvent.submit(input.closest("form")!);
    });
    const posted = fetchMock.mock.calls.find(([u, i]) => String(u) === "/api/projects/p1/lists" && (i as RequestInit | undefined)?.method === "POST");
    expect(posted).toBeUndefined();
  });

  it("a slow entries response for a deselected list does not overwrite the current one", async () => {
    // Deterministic race: lst-1's fetch is held open, the user picks lst-2,
    // then lst-1's response lands last. Without the guard it wins and shows
    // one list's entries under the other's heading.
    let releaseSlow: (() => void) | null = null;
    const slow = new Promise<void>((res) => { releaseSlow = () => res(); });
    const raceFetch = vi.fn((url: string) => {
      if (url === "/api/projects/p1/lists") {
        return Promise.resolve(ok({ items: [
          { id: "lst-1", project_id: "p1", title: "Shopping", description: "", status: "active", created_by: "u1", created_at: 0, updated_at: 0 },
          { id: "lst-2", project_id: "p1", title: "Chores", description: "", status: "active", created_by: "u1", created_at: 0, updated_at: 0 },
        ] }));
      }
      if (url === "/api/projects/p1/lists/lst-1/entries") {
        return slow.then(() => ok({ items: [{ id: "e-slow", list_id: "lst-1", project_id: "p1", text: "STALE MILK", original_text: "STALE MILK", category: null, status: "new", done: 0, author_kind: "user", author_id: "u1", edited_by: null, position: 0, created_at: 0, updated_at: 0 }] }));
      }
      if (url === "/api/projects/p1/lists/lst-2/entries") {
        return Promise.resolve(ok({ items: [{ id: "e-fast", list_id: "lst-2", project_id: "p1", text: "Sweep floor", original_text: "Sweep floor", category: null, status: "new", done: 0, author_kind: "user", author_id: "u1", edited_by: null, position: 0, created_at: 0, updated_at: 0 }] }));
      }
      return Promise.resolve(ok({ items: [] }));
    });
    vi.stubGlobal("fetch", raceFetch);

    await act(async () => {
      render(<ProjectLists project={fakeProject} />);
    });
    await act(async () => {
      fireEvent.click(screen.getByText("Chores"));
    });
    await waitFor(() => expect(screen.getByText("Sweep floor")).toBeInTheDocument());
    await act(async () => {
      releaseSlow!();
      await slow;
    });
    expect(screen.queryByText("STALE MILK")).not.toBeInTheDocument();
    expect(screen.getByText("Sweep floor")).toBeInTheDocument();
  });

  it("a deleted list disappears from the rail", async () => {
    await act(async () => {
      render(<ProjectLists project={fakeProject} />);
    });
    expect(screen.getAllByText("Shopping").length).toBeGreaterThanOrEqual(1);
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Delete Shopping"));
    });
    const confirmDialog = screen.getByRole("dialog", { name: /delete list/i });
    expect(confirmDialog).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(within(confirmDialog).getByRole("button", { name: "Delete" }));
    });
    await waitFor(() => {
      const rail = screen.queryByLabelText("Project lists");
      if (rail) {
        expect(within(rail).queryByText("Shopping")).not.toBeInTheDocument();
      } else {
        expect(screen.queryByText("Shopping")).not.toBeInTheDocument();
      }
    });
  });

  it("stacks the rail and entries panel on mobile", async () => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(true);
    await act(async () => {
      render(<ProjectLists project={fakeProject} />);
    });
    const container = document.querySelector("[class*='listsContainer']");
    expect(container).toBeTruthy();
    expect(screen.getByLabelText("Project lists")).toBeInTheDocument();
    expect(screen.getByLabelText("List entries")).toBeInTheDocument();
  });

  it("removes an entry after confirming the delete dialog", async () => {
    await act(async () => {
      render(<ProjectLists project={fakeProject} />);
    });
    await waitFor(() => expect(screen.getByText("Milk")).toBeInTheDocument());
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Delete entry Milk"));
    });
    const confirmDialog = screen.getByRole("dialog", { name: /remove entry/i });
    expect(confirmDialog).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(within(confirmDialog).getByRole("button", { name: "Remove" }));
    });
    await waitFor(() => expect(screen.queryByText("Milk")).not.toBeInTheDocument());
  });

  it("shows the original text in a popover instead of alert when the original button is clicked", async () => {
    const alertMock = vi.fn();
    vi.stubGlobal("alert", alertMock);
    await act(async () => {
      render(<ProjectLists project={fakeProject} />);
    });
    await waitFor(() => expect(screen.getByText("original")).toBeInTheDocument());
    await act(async () => {
      fireEvent.click(screen.getByLabelText("View original text for Bread"));
    });
    expect(screen.getByText("Whole grain bread")).toBeInTheDocument();
    expect(alertMock).not.toHaveBeenCalled();
  });
});
