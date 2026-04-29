import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import type { Project } from "@/lib/projects";
import { ProjectWorkspace } from "../ProjectWorkspace";

vi.mock("../../../hooks/use-is-mobile", () => ({
  useIsMobile: vi.fn(),
}));
import { useIsMobile } from "../../../hooks/use-is-mobile";

// Mock heavy children — we only care about the tab strip switch here.
vi.mock("../board/ProjectBoard", () => ({ ProjectBoard: () => <div /> }));
vi.mock("../board/TaskModal", () => ({ TaskModal: () => <div /> }));
vi.mock("../canvas/CanvasView", () => ({ CanvasView: () => <div /> }));
vi.mock("../ProjectTaskList", () => ({ ProjectTaskList: () => <div /> }));
vi.mock("../ProjectMembers", () => ({ ProjectMembers: () => <div /> }));
vi.mock("../ProjectActivity", () => ({ ProjectActivity: () => <div /> }));
vi.mock("@/apps/FilesApp", () => ({ FilesApp: () => <div /> }));
vi.mock("@/apps/MessagesApp", () => ({ MessagesApp: () => <div /> }));

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

describe("ProjectWorkspace tab strip", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    // ProjectWorkspace fires `fetch("/api/auth/me")` on mount. Stub it so the
    // test doesn't depend on jsdom's network or auth state.
    originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ user: { id: "u1" } }),
    }) as unknown as typeof fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("renders WorkspaceTabPills on mobile", async () => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(true);
    await act(async () => {
      render(<ProjectWorkspace project={fakeProject} onChanged={() => {}} />);
    });
    expect(screen.getByTestId("workspace-tab-pills-scroller")).toBeInTheDocument();
  });

  it("renders the desktop button strip on desktop", async () => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(false);
    await act(async () => {
      render(<ProjectWorkspace project={fakeProject} onChanged={() => {}} />);
    });
    expect(screen.queryByTestId("workspace-tab-pills-scroller")).not.toBeInTheDocument();
  });
});
