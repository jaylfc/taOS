import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ElementGrid } from "../ElementGrid";
import type { Project, ProjectElement } from "../../../../lib/projects";

const project: Project = {
  id: "p1",
  slug: "p1",
  name: "T-Shirt Business",
  description: "",
  status: "active",
  created_by: "u1",
  created_at: 0,
  updated_at: 0,
};

const els: ProjectElement[] = [
  {
    id: "e1", project_id: "p1", name: "Designs", slug: "designs", type: "design",
    description: "", assignee_id: null, settings: {}, created_at: 0, updated_at: 0, archived_at: null,
    open_tasks: 2, total_tasks: 4,
  },
  {
    id: "e2", project_id: "p1", name: "Website", slug: "website", type: "website",
    description: "", assignee_id: "a1", settings: {}, created_at: 0, updated_at: 0, archived_at: null,
    open_tasks: 1, total_tasks: 3,
  },
];

describe("ElementGrid", () => {
  it("renders the overview header, the Project card, and element cards", () => {
    render(
      <ElementGrid
        project={project}
        elements={els}
        assigneeName={() => "Web Agent"}
        onOpenElement={() => {}}
        onAddElement={() => {}}
        onOpenProject={() => {}}
      />,
    );
    expect(screen.getByText("Elements")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open project-level view" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Open element Designs/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Open element Website/ })).toBeInTheDocument();
  });

  it("resolves the assignee name for the owner chip", () => {
    render(
      <ElementGrid
        project={project}
        elements={els}
        assigneeName={(id) => (id === "a1" ? "Web Agent" : null)}
        onOpenElement={() => {}}
        onAddElement={() => {}}
        onOpenProject={() => {}}
      />,
    );
    expect(screen.getByText("Owner: Web Agent")).toBeInTheDocument();
  });

  it("opens an element when its card is clicked", () => {
    const onOpenElement = vi.fn();
    render(
      <ElementGrid
        project={project}
        elements={els}
        assigneeName={() => null}
        onOpenElement={onOpenElement}
        onAddElement={() => {}}
        onOpenProject={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Open element Website/ }));
    expect(onOpenElement).toHaveBeenCalledWith("e2");
  });

  it("fires onAddElement from both the header button and the add tile", () => {
    const onAddElement = vi.fn();
    render(
      <ElementGrid
        project={project}
        elements={els}
        assigneeName={() => null}
        onOpenElement={() => {}}
        onAddElement={onAddElement}
        onOpenProject={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Add element" }));
    fireEvent.click(screen.getByRole("button", { name: "+ Add element" }));
    expect(onAddElement).toHaveBeenCalledTimes(2);
  });

  it("fires onOpenProject from the Project card", () => {
    const onOpenProject = vi.fn();
    render(
      <ElementGrid
        project={project}
        elements={els}
        assigneeName={() => null}
        onOpenElement={() => {}}
        onAddElement={() => {}}
        onOpenProject={onOpenProject}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open project-level view" }));
    expect(onOpenProject).toHaveBeenCalled();
  });
});
