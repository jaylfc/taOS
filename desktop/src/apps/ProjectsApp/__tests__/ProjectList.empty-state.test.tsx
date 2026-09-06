import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { ProjectList } from "../ProjectList";

vi.mock("../CreateProjectDialog", () => ({
  CreateProjectDialog: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="create-dialog">
      <button onClick={onClose}>Close</button>
    </div>
  ),
}));

describe("ProjectList — empty state (fix #618)", () => {
  it("renders the empty state when projects is empty", () => {
    render(
      <ProjectList
        projects={[]}
        selectedId={null}
        onSelect={vi.fn()}
        onCreated={vi.fn()}
      />
    );
    expect(screen.getByText("No projects yet")).toBeInTheDocument();
    expect(screen.getByText("Create your first project")).toBeInTheDocument();
  });

  it("does NOT render 'No projects yet' when there are projects", () => {
    render(
      <ProjectList
        projects={[{ id: "p1", name: "My Project", slug: "my-project" }]}
        selectedId={null}
        onSelect={vi.fn()}
        onCreated={vi.fn()}
      />
    );
    expect(screen.queryByText("No projects yet")).not.toBeInTheDocument();
    expect(screen.getByText("My Project")).toBeInTheDocument();
  });

  it("'Create your first project' button opens the create dialog", () => {
    render(
      <ProjectList
        projects={[]}
        selectedId={null}
        onSelect={vi.fn()}
        onCreated={vi.fn()}
      />
    );
    fireEvent.click(screen.getByText("Create your first project"));
    expect(screen.getByTestId("create-dialog")).toBeInTheDocument();
  });
});

describe("ProjectList — open in new window (Task 111)", () => {
  const projects = [{ id: "p1", name: "My Project", slug: "my-project" }];

  it("calls onOpenInNewWindow with the project id when the affordance is clicked", () => {
    const onOpenInNewWindow = vi.fn();
    render(
      <ProjectList
        projects={projects}
        selectedId={null}
        onSelect={vi.fn()}
        onCreated={vi.fn()}
        onOpenInNewWindow={onOpenInNewWindow}
      />
    );
    fireEvent.click(screen.getByLabelText("Open My Project in a new window"));
    expect(onOpenInNewWindow).toHaveBeenCalledWith("p1");
  });

  it("does not render the new-window affordance when no handler is provided", () => {
    render(
      <ProjectList
        projects={projects}
        selectedId={null}
        onSelect={vi.fn()}
        onCreated={vi.fn()}
      />
    );
    expect(screen.queryByLabelText("Open My Project in a new window")).not.toBeInTheDocument();
  });

  it("selecting the row (not the affordance) still calls onSelect", () => {
    const onSelect = vi.fn();
    render(
      <ProjectList
        projects={projects}
        selectedId={null}
        onSelect={onSelect}
        onCreated={vi.fn()}
        onOpenInNewWindow={vi.fn()}
      />
    );
    fireEvent.click(screen.getByText("My Project"));
    expect(onSelect).toHaveBeenCalledWith("p1");
  });
});
