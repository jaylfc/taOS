import { render, screen, act, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const listMock = vi.fn();

// Keep the real projectsApi surface — the workspace pane calls several of its
// sub-clients on mount — and override only the project listing under test.
vi.mock("@/lib/projects", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/projects")>();
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      list: (...args: unknown[]) => listMock(...args),
    },
  };
});

import { ProjectsApp } from "./index";

const projects = [
  { id: "p1", name: "First", slug: "first", status: "active" },
  { id: "p2", name: "Second", slug: "second", status: "active" },
];

/**
 * Selecting a project must not re-list every project. Deriving `refresh` from
 * `selectedId` and then using it as the mount effect's dependency turns each
 * click in the rail into a full projectsApi.list() round trip.
 */
describe("ProjectsApp project list refetch", () => {
  beforeEach(() => {
    listMock.mockReset();
    listMock.mockResolvedValue(projects);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve([]),
        text: () => Promise.resolve(""),
      }),
    );
    vi.stubGlobal(
      "EventSource",
      class {
        onmessage: unknown = null;
        onerror: unknown = null;
        close() {}
        addEventListener() {}
        removeEventListener() {}
      },
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not re-list projects when the selection changes", async () => {
    render(<ProjectsApp windowId="w1" />);
    await act(async () => {
      await Promise.resolve();
    });

    const afterMount = listMock.mock.calls.length;
    expect(afterMount).toBe(1);

    fireEvent.click(screen.getByText("Second"));
    await act(async () => {
      await Promise.resolve();
    });

    expect(listMock.mock.calls.length).toBe(afterMount);
  });
});
