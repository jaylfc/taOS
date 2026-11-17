import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CreateProjectDialog } from "./CreateProjectDialog";

vi.mock("@/lib/projects", () => ({
  projectsApi: { create: vi.fn() },
}));

describe("CreateProjectDialog viewport-aware sizing (#mobile-keyboard)", () => {
  it("bounds the dialog to the visual viewport height and scrolls inside", () => {
    const { container } = render(
      <CreateProjectDialog onClose={vi.fn()} onCreated={vi.fn()} />,
    );
    const dialog = screen.getByRole("dialog", { name: /create project/i });
    const form = document.querySelector("form")!;

    // jsdom has no visualViewport, so the hook falls back to innerHeight (768).
    expect(dialog.style.maxHeight).toBe("768px");
    // The card itself is capped to the visible area and scrolls, so a focused
    // field and the Create button stay in view when the keyboard is up.
    expect(form.style.maxHeight).toContain("736px");
    expect(form.style.maxHeight).toContain("92vh");
    expect(form.className).toContain("overflow-y-auto");
  });

  it("tracks the visual viewport when the keyboard shrinks it", () => {
    const vv = {
      height: 420,
      offsetTop: 0,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
    Object.defineProperty(window, "visualViewport", {
      value: vv,
      configurable: true,
      writable: true,
    });

    render(<CreateProjectDialog onClose={vi.fn()} onCreated={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: /create project/i });
    expect(dialog.style.maxHeight).toBe("420px");

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (window as any).visualViewport;
  });
});