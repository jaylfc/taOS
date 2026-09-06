import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ProjectFab } from "../ProjectFab";

describe("ProjectFab", () => {
  it("renders a single round '+' button", () => {
    render(<ProjectFab onClick={() => {}} />);
    expect(screen.getByRole("button", { name: /create task/i })).toBeInTheDocument();
  });

  it("calls onClick when tapped", () => {
    const onClick = vi.fn();
    render(<ProjectFab onClick={onClick} />);
    fireEvent.click(screen.getByRole("button", { name: /create task/i }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("uses safe-area-inset for bottom positioning", () => {
    render(<ProjectFab onClick={() => {}} />);
    const btn = screen.getByRole("button", { name: /create task/i });
    expect(btn.getAttribute("style") ?? "").toMatch(/safe-area-inset-bottom/);
  });
});
