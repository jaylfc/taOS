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
