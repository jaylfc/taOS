import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import { ProjectsApp } from "../index";

vi.mock("../../../hooks/use-is-mobile", () => ({
  useIsMobile: vi.fn(),
}));
import { useIsMobile } from "../../../hooks/use-is-mobile";

vi.mock("@/lib/projects", () => ({
  projectsApi: {
    list: vi.fn().mockResolvedValue([]),
    activity: vi.fn().mockResolvedValue([]),
  },
}));

// Stub out heavy child components so the test only cares about layout structure
vi.mock("../ProjectList", () => ({
  ProjectList: () => <div data-testid="project-list" />,
}));

vi.mock("../ProjectWorkspace", () => ({
  ProjectWorkspace: () => <div data-testid="project-workspace" />,
}));

// Stub MobileSplitView so it renders without needing window.matchMedia.
// Surface listTitle as a data attribute so the test can verify the production
// code actually routed through this component with the expected props,
// rather than a desktop-branch element happening to wear the same testid.
vi.mock("../../../components/mobile/MobileSplitView", () => ({
  MobileSplitView: ({ list, listTitle }: { list: React.ReactNode; listTitle?: string }) => (
    <div data-testid="mobile-split-view" data-list-title={listTitle}>{list}</div>
  ),
}));

describe("ProjectsApp mobile shell", () => {
  it("renders MobileSplitView when useIsMobile is true", () => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(true);
    render(<ProjectsApp windowId="test-window" />);
    expect(screen.getByTestId("mobile-split-view")).toBeInTheDocument();
    expect(screen.getByTestId("mobile-split-view")).toHaveAttribute("data-list-title", "Projects");
  });

  it("renders side-by-side layout (project-list sidebar + main) when useIsMobile is false", () => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(false);
    render(<ProjectsApp windowId="test-window" />);
    expect(screen.queryByTestId("mobile-split-view")).not.toBeInTheDocument();
    // ProjectList (the 248px sidebar) and the main detail column render together.
    expect(screen.getByTestId("project-list")).toBeInTheDocument();
    expect(document.querySelector("main")).toBeInTheDocument();
  });
});
