import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BoardColumn } from "../BoardColumn";

describe("BoardColumn", () => {
  it("renders header with name + count", () => {
    render(<BoardColumn status="ready" count={3} onDropTask={() => {}}>{null}</BoardColumn>);
    expect(screen.getByText(/Ready/)).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("calls onDropTask when a task is dropped", () => {
    const onDrop = vi.fn();
    render(<BoardColumn status="claimed" count={0} onDropTask={onDrop}>{null}</BoardColumn>);
    const region = screen.getByRole("region", { name: /Claimed/ });
    fireEvent.dragOver(region);
    fireEvent.drop(region, { dataTransfer: { getData: () => "t1" } });
    expect(onDrop).toHaveBeenCalledWith("t1");
  });
});
