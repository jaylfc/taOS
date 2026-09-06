import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TaskCreateSheet } from "../TaskCreateSheet";

describe("TaskCreateSheet", () => {
  it("renders title input and submit button", () => {
    render(<TaskCreateSheet open onClose={() => {}} onSubmit={() => Promise.resolve()} />);
    expect(screen.getByPlaceholderText(/task title/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /create/i })).toBeInTheDocument();
  });

  it("does not render when closed", () => {
    render(<TaskCreateSheet open={false} onClose={() => {}} onSubmit={() => Promise.resolve()} />);
    expect(screen.queryByPlaceholderText(/task title/i)).not.toBeInTheDocument();
  });

  it("calls onSubmit with the entered title and then onClose", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(<TaskCreateSheet open onSubmit={onSubmit} onClose={onClose} />);
    fireEvent.change(screen.getByPlaceholderText(/task title/i), { target: { value: "ship it" } });
    fireEvent.click(screen.getByRole("button", { name: /create/i }));
    await new Promise((r) => setTimeout(r, 0));
    expect(onSubmit).toHaveBeenCalledWith({ title: "ship it" });
    expect(onClose).toHaveBeenCalled();
  });

  it("disables submit when title is empty", () => {
    render(<TaskCreateSheet open onClose={() => {}} onSubmit={() => Promise.resolve()} />);
    expect(screen.getByRole("button", { name: /create/i })).toBeDisabled();
  });
});
