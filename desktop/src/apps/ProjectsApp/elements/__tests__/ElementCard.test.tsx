import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ElementCard } from "../ElementCard";
import type { ProjectElement } from "../../../../lib/projects";

const base: ProjectElement = {
  id: "e1",
  project_id: "p1",
  name: "My Site",
  slug: "my-site",
  type: "website",
  description: "",
  assignee_id: null,
  settings: {},
  created_at: 0,
  updated_at: 0,
  archived_at: null,
  open_tasks: 3,
  total_tasks: 5,
  canvas_items: 2,
};

describe("ElementCard", () => {
  it("renders the name, type label, and task counts", () => {
    render(<ElementCard element={base} onOpen={() => {}} />);
    expect(screen.getByText("My Site")).toBeInTheDocument();
    expect(screen.getByText("Website")).toBeInTheDocument();
    expect(screen.getByText("3 open · 5 total")).toBeInTheDocument();
  });

  it("shows the owner chip when an assignee is given a name", () => {
    render(<ElementCard element={{ ...base, assignee_id: "a1" }} assigneeName="Web Agent" onOpen={() => {}} />);
    expect(screen.getByText("Owner: Web Agent")).toBeInTheDocument();
  });

  it("hides the owner chip when there is no assignee", () => {
    render(<ElementCard element={base} assigneeName="Web Agent" onOpen={() => {}} />);
    expect(screen.queryByText(/Owner:/)).not.toBeInTheDocument();
  });

  it("calls onOpen with the element id when clicked", () => {
    const onOpen = vi.fn();
    render(<ElementCard element={base} onOpen={onOpen} />);
    fireEvent.click(screen.getByRole("button", { name: /Open element My Site/ }));
    expect(onOpen).toHaveBeenCalledWith("e1");
  });
});
