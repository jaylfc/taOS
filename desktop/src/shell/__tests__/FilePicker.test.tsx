import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { FilePicker } from "../FilePicker";

describe("FilePicker", () => {
  it("shows the three tabs when all sources are requested", () => {
    render(
      <FilePicker
        sources={["disk", "workspace", "agent-workspace"]}
        multi
        onPick={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole("tab", { name: /Disk/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /My workspace/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Agent workspaces/i })).toBeInTheDocument();
  });

  it("cancel calls onCancel and nothing else", () => {
    const onPick = vi.fn();
    const onCancel = vi.fn();
    render(<FilePicker sources={["disk"]} onPick={onPick} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: /Cancel/i }));
    expect(onCancel).toHaveBeenCalled();
    expect(onPick).not.toHaveBeenCalled();
  });

  it("Esc closes", () => {
    const onCancel = vi.fn();
    render(<FilePicker sources={["disk"]} onPick={vi.fn()} onCancel={onCancel} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });
});
