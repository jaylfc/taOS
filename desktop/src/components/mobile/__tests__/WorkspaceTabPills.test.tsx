import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { WorkspaceTabPills } from "../WorkspaceTabPills";

const tabs = [
  { id: "tasks", label: "Tasks" },
  { id: "board", label: "Board" },
  { id: "files", label: "Files" },
] as const;

describe("WorkspaceTabPills", () => {
  it("renders one pill per tab", () => {
    render(<WorkspaceTabPills tabs={tabs} active="tasks" onSelect={() => {}} />);
    for (const t of tabs) {
      expect(screen.getByRole("tab", { name: t.label })).toBeInTheDocument();
    }
  });

  it("marks the active tab with aria-selected=true", () => {
    render(<WorkspaceTabPills tabs={tabs} active="board" onSelect={() => {}} />);
    expect(screen.getByRole("tab", { name: "Board" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Tasks" })).toHaveAttribute("aria-selected", "false");
  });

  it("calls onSelect with the tab id when clicked", () => {
    const onSelect = vi.fn();
    render(<WorkspaceTabPills tabs={tabs} active="tasks" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("tab", { name: "Files" }));
    expect(onSelect).toHaveBeenCalledWith("files");
  });

  it("renders a horizontal-scroll container", () => {
    const { container } = render(
      <WorkspaceTabPills tabs={tabs} active="tasks" onSelect={() => {}} />
    );
    const scroller = container.querySelector("[data-testid='workspace-tab-pills-scroller']");
    expect(scroller).toBeInTheDocument();
    expect(scroller).toHaveClass("overflow-x-auto");
  });
});
