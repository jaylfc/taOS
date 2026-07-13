import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TaskCard } from "../TaskCard";
import type { Task } from "../types";

const t: Task = {
  id: "t1", project_id: "p1", parent_task_id: null, title: "Wire up auth",
  body: "", status: "claimed", priority: 1, labels: ["feature"], assignee_id: "alice",
  claimed_by: "alice", claimed_at: "2026-04-26T00:00:00Z", closed_at: null, closed_by: null,
  element_id: null,
  created_by: "u", created_at: "2026-04-26T00:00:00Z", updated_at: "2026-04-26T00:00:00Z",
};

describe("TaskCard", () => {
  it("renders title, id, labels, priority bar", () => {
    render(<TaskCard task={t} onOpen={() => {}} />);
    expect(screen.getByText("Wire up auth")).toBeInTheDocument();
    expect(screen.getByText("t1")).toBeInTheDocument();
    expect(screen.getByText("feature")).toBeInTheDocument();
  });

  it("calls onOpen when clicked", () => {
    const open = vi.fn();
    render(<TaskCard task={t} onOpen={open} />);
    fireEvent.click(screen.getByRole("button", { name: /Wire up auth/ }));
    expect(open).toHaveBeenCalledWith("t1");
  });

  it("shows just-claimed marker when justClaimed is true", () => {
    render(<TaskCard task={t} onOpen={() => {}} justClaimed />);
    expect(screen.getByTestId("task-card")).toHaveClass(/just-?claimed/i);
  });

  it("calls onMove when M is pressed while focused", () => {
    const move = vi.fn();
    render(<TaskCard task={t} onOpen={() => {}} onMove={move} />);
    const card = screen.getByRole("button");
    card.focus();
    fireEvent.keyDown(card, { key: "M" });
    expect(move).toHaveBeenCalledWith("t1");
  });
});
