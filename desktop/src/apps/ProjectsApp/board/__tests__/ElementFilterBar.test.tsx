import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ElementFilterBar } from "../ElementFilterBar";
import type { ProjectElement } from "../../../../lib/projects";
import type { ElementFilter } from "../types";

const els: ProjectElement[] = [
  { id: "e1", project_id: "p1", name: "Website", slug: "website", type: "website", description: "", assignee_id: null, settings: {}, created_at: 0, updated_at: 0, archived_at: null },
  { id: "e2", project_id: "p1", name: "Designs", slug: "designs", type: "design", description: "", assignee_id: null, settings: {}, created_at: 0, updated_at: 0, archived_at: null },
];

describe("ElementFilterBar", () => {
  it("returns nothing when there are no elements (back-compat: no filter bar)", () => {
    const { container } = render(
      <ElementFilterBar elements={[]} value={null} onChange={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders All, element chips, and Project-level", () => {
    render(<ElementFilterBar elements={els} value={null} onChange={() => {}} />);
    expect(screen.getByRole("button", { name: "All" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Website" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Designs" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Project-level" })).toBeInTheDocument();
  });

  it("marks the active chip (All = null)", () => {
    render(<ElementFilterBar elements={els} value={null} onChange={() => {}} />);
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
  });

  it("marks the active chip for a specific element", () => {
    render(<ElementFilterBar elements={els} value="e2" onChange={() => {}} />);
    expect(screen.getByRole("button", { name: "Designs" })).toHaveAttribute("aria-pressed", "true");
  });

  it("marks the active chip for project-level (none)", () => {
    render(<ElementFilterBar elements={els} value="none" onChange={() => {}} />);
    expect(screen.getByRole("button", { name: "Project-level" })).toHaveAttribute("aria-pressed", "true");
  });

  it("emits null when All is clicked", () => {
    const fn = vi.fn();
    render(<ElementFilterBar elements={els} value="e1" onChange={fn} />);
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expect(fn).toHaveBeenCalledWith(null);
  });

  it("emits the element id when an element chip is clicked", () => {
    const fn = vi.fn();
    render(<ElementFilterBar elements={els} value={null} onChange={fn} />);
    fireEvent.click(screen.getByRole("button", { name: "Website" }));
    expect(fn).toHaveBeenCalledWith("e1");
  });

  it("emits 'none' when Project-level is clicked", () => {
    const fn = vi.fn();
    render(<ElementFilterBar elements={els} value={null} onChange={fn} />);
    fireEvent.click(screen.getByRole("button", { name: "Project-level" }));
    expect(fn).toHaveBeenCalledWith("none" as ElementFilter);
  });
});
