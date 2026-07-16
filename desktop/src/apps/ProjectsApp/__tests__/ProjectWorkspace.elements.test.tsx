import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { Project, ProjectElement } from "@/lib/projects";
import { ProjectWorkspace } from "../ProjectWorkspace";

const h = vi.hoisted(() => ({ current: [] as ProjectElement[] }));

vi.mock("@/lib/projects", () => ({
  projectsApi: {
    elements: { list: () => Promise.resolve(h.current) },
    members: { list: vi.fn().mockResolvedValue([]) },
    tasks: { create: vi.fn() },
  },
}));

vi.mock("../../../hooks/use-is-mobile", () => ({ useIsMobile: () => false }));
vi.mock("../ProjectWorkspacePane", () => ({ ProjectWorkspacePane: () => <div data-testid="workspace-pane" /> }));
vi.mock("../board/ProjectBoard", () => ({
  ProjectBoard: (props: { elementId?: string | null }) => (
    <div data-testid="board" data-element={props.elementId ?? ""} />
  ),
}));
vi.mock("../board/TaskModal", () => ({ TaskModal: () => <div /> }));
vi.mock("../canvas/CanvasView", () => ({ CanvasView: () => <div /> }));
vi.mock("../ProjectTaskList", () => ({ ProjectTaskList: () => <div /> }));
vi.mock("../ProjectMembers", () => ({ ProjectMembers: () => <div /> }));
vi.mock("../ProjectActivity", () => ({ ProjectActivity: () => <div /> }));
vi.mock("../ProjectDecisions", () => ({ ProjectDecisions: () => <div /> }));
vi.mock("../ProjectRoutines", () => ({ ProjectRoutines: () => <div /> }));
vi.mock("@/apps/FilesApp", () => ({ FilesApp: () => <div /> }));
vi.mock("@/apps/MessagesApp", () => ({ MessagesApp: () => <div /> }));
vi.mock("../elements/ElementCreateDialog", () => ({
  ElementCreateDialog: () => <div data-testid="create-el-dialog" />,
}));

const fakeProject: Project = {
  id: "p1",
  slug: "p1",
  name: "T-Shirt Business",
  description: "",
  status: "active",
  created_by: "u1",
  created_at: 0,
  updated_at: 0,
};

const elements: ProjectElement[] = [
  {
    id: "e1", project_id: "p1", name: "Designs", slug: "designs", type: "design",
    description: "", assignee_id: null, settings: {}, created_at: 0, updated_at: 0, archived_at: null,
    open_tasks: 2, total_tasks: 4,
  },
  {
    id: "e2", project_id: "p1", name: "Website", slug: "website", type: "website",
    description: "", assignee_id: null, settings: {}, created_at: 0, updated_at: 0, archived_at: null,
    open_tasks: 1, total_tasks: 3,
  },
];

describe("ProjectWorkspace element overview (slice 3)", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    // The element id rides the URL; jsdom persists location across tests, so
    // clear any query string left by a prior test's drill-in.
    window.history.replaceState({}, "", "/");
    originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn((url: unknown) => {
      if (String(url).includes("/auth/me")) {
        return Promise.resolve({ ok: true, json: async () => ({ user: { id: "u1" } }) });
      }
      if (String(url).includes("/api/agents")) {
        return Promise.resolve({ ok: true, json: async () => [] });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    }) as unknown as typeof fetch;
    h.current = elements;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.clearAllMocks();
  });

  it("shows the element overview grid when the project has elements", async () => {
    await act(async () => {
      render(<ProjectWorkspace project={fakeProject} onChanged={() => {}} />);
    });
    expect(await screen.findByText("Elements")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Open element Designs/ })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Open element Website/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open project-level view" })).toBeInTheDocument();
    expect(screen.queryByTestId("workspace-pane")).not.toBeInTheDocument();
  });

  it("regresses to today's workspace pane when there are no elements", async () => {
    h.current = [];
    await act(async () => {
      render(<ProjectWorkspace project={fakeProject} onChanged={() => {}} />);
    });
    expect(await screen.findByTestId("workspace-pane")).toBeInTheDocument();
    expect(screen.queryByText("Elements")).not.toBeInTheDocument();
  });

  it("drills into an element: scopes the board and shows a breadcrumb", async () => {
    await act(async () => {
      render(<ProjectWorkspace project={fakeProject} onChanged={() => {}} />);
    });
    const card = await screen.findByRole("button", { name: /Open element Website/ });
    await act(async () => {
      fireEvent.click(card);
    });
    expect(screen.getByTestId("board").getAttribute("data-element")).toBe("e2");
    expect(screen.getByRole("button", { name: "T-Shirt Business" })).toBeInTheDocument();
    expect(screen.getByText("Website")).toBeInTheDocument();
  });

  it("returns to the grid via the breadcrumb project link", async () => {
    await act(async () => {
      render(<ProjectWorkspace project={fakeProject} onChanged={() => {}} />);
    });
    const card = await screen.findByRole("button", { name: /Open element Website/ });
    await act(async () => {
      fireEvent.click(card);
    });
    const crumb = await screen.findByRole("button", { name: "T-Shirt Business" });
    await act(async () => {
      fireEvent.click(crumb);
    });
    expect(await screen.findByText("Elements")).toBeInTheDocument();
    expect(screen.queryByTestId("board")).not.toBeInTheDocument();
  });

  it("opens the element create dialog from the grid", async () => {
    await act(async () => {
      render(<ProjectWorkspace project={fakeProject} onChanged={() => {}} />);
    });
    const add = await screen.findByRole("button", { name: "+ Add element" });
    await act(async () => {
      fireEvent.click(add);
    });
    expect(await screen.findByTestId("create-el-dialog")).toBeInTheDocument();
  });
});
