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

// Stub MobileSplitView so it renders without needing window.matchMedia
vi.mock("../../../components/mobile/MobileSplitView", () => ({
  MobileSplitView: ({ list }: { list: React.ReactNode }) => (
    <div data-testid="mobile-split-view">{list}</div>
  ),
}));

describe("ProjectsApp mobile shell", () => {
  it("renders MobileSplitView when useIsMobile is true", () => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(true);
    render(<ProjectsApp windowId="test-window" />);
    expect(screen.getByTestId("mobile-split-view")).toBeInTheDocument();
  });

  it("renders side-by-side flex layout when useIsMobile is false", () => {
    (useIsMobile as ReturnType<typeof vi.fn>).mockReturnValue(false);
    render(<ProjectsApp windowId="test-window" />);
    expect(screen.queryByTestId("mobile-split-view")).not.toBeInTheDocument();
    expect(document.querySelector("aside.w-72")).toBeInTheDocument();
  });
});
