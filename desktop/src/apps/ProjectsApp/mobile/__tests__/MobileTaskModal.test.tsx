import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MobileTaskModal } from "../MobileTaskModal";

const baseTask = {
  id: "t1",
  title: "Ship it",
  description: "Test description",
  status: "open",
  priority: 2,
  assignee_id: null,
  labels: [],
  parent_task_id: null,
};

const noopHandlers = {
  onClose: vi.fn(),
  onPrev: vi.fn(),
  onNext: vi.fn(),
  onChangeStatus: vi.fn(),
  hasPrev: false,
  hasNext: false,
};

describe("MobileTaskModal", () => {
  it("renders all five sections in order", () => {
    render(<MobileTaskModal task={baseTask} {...noopHandlers} />);
    const sections = screen.getAllByRole("group");
    const labels = sections.map((s) => s.getAttribute("aria-label"));
    expect(labels).toEqual(["Hero", "Metadata", "SubTasks", "Relationships", "Activity"]);
  });

  it("calls onClose when the close button is tapped", () => {
    const onClose = vi.fn();
    render(<MobileTaskModal task={baseTask} {...noopHandlers} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("shows a sticky action button for status change", () => {
    render(<MobileTaskModal task={baseTask} {...noopHandlers} />);
    expect(screen.getByRole("button", { name: /claim/i })).toBeInTheDocument();
  });

  it("the Activity section is collapsed by default", () => {
    render(<MobileTaskModal task={baseTask} {...noopHandlers} />);
    const activity = screen.getByRole("group", { name: "Activity" });
    expect(activity.querySelector("details")).not.toHaveAttribute("open");
  });
});
