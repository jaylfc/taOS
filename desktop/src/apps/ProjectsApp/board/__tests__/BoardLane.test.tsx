import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BoardLane } from "../BoardLane";

describe("BoardLane", () => {
  it("renders lane header (title + subtitle)", () => {
    render(
      <BoardLane
        header={{ key: "alice", kind: "assignee", title: "alice", subtitle: "2 active" }}
        cells={{ ready: <></>, claimed: <></>, closed: <></>, quarantined: <></>, parked: <></> }}
        onDropTask={() => {}}
      />,
    );
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("2 active")).toBeInTheDocument();
  });

  it("emits onDropTask with cell info on drop", () => {
    const onDrop = vi.fn();
    render(
      <BoardLane
        header={{ key: "alice", kind: "assignee", title: "alice" }}
        cells={{ ready: <div data-testid="r" />, claimed: <></>, closed: <></>, quarantined: <></>, parked: <></> }}
        onDropTask={onDrop}
      />,
    );
    const cell = screen.getByTestId("lane-cell-ready");
    fireEvent.dragOver(cell);
    fireEvent.drop(cell, { dataTransfer: { getData: () => "t1" } });
    expect(onDrop).toHaveBeenCalledWith("t1", "ready", "alice");
  });
});
