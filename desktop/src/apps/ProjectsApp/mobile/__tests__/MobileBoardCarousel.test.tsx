import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MobileBoardCarousel } from "../MobileBoardCarousel";

const columns = [
  { id: "open", label: "Open" },
  { id: "claimed", label: "Claimed" },
  { id: "closed", label: "Closed" },
];

const tasksByColumn = {
  open: [
    { id: "t1", title: "First", status: "open", assignee: "alice" },
    { id: "t2", title: "Second", status: "open", assignee: "alice" },
  ],
  claimed: [
    { id: "t3", title: "Claimed", status: "claimed", assignee: "bob" },
  ],
  closed: [],
};

describe("MobileBoardCarousel — shell", () => {
  it("renders one pill per column with task counts", () => {
    render(
      <MobileBoardCarousel
        columns={columns}
        tasksByColumn={tasksByColumn}
        groupBy={null}
        onOpenTask={() => {}}
      />
    );
    expect(screen.getByRole("tab", { name: /open \(2\)/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /claimed \(1\)/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /closed \(0\)/i })).toBeInTheDocument();
  });

  it("renders an empty-state pane for empty columns", () => {
    render(
      <MobileBoardCarousel
        columns={columns}
        tasksByColumn={tasksByColumn}
        groupBy={null}
        onOpenTask={() => {}}
      />
    );
    expect(screen.getByText(/no closed tasks/i)).toBeInTheDocument();
  });

  it("calls onOpenTask when a card is tapped", () => {
    const onOpenTask = vi.fn();
    render(
      <MobileBoardCarousel
        columns={columns}
        tasksByColumn={tasksByColumn}
        groupBy={null}
        onOpenTask={onOpenTask}
      />
    );
    fireEvent.click(screen.getByText("First"));
    expect(onOpenTask).toHaveBeenCalledWith("t1");
  });

  it("scrolls to a column when its pill is tapped", () => {
    const original = Element.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    try {
      render(
        <MobileBoardCarousel
          columns={columns}
          tasksByColumn={tasksByColumn}
          groupBy={null}
          onOpenTask={() => {}}
        />
      );
      fireEvent.click(screen.getByRole("tab", { name: /claimed/i }));
      expect(scrollIntoView).toHaveBeenCalled();
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });
});
