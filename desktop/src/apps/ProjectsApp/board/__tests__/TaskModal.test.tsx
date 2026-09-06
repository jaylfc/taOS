import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { TaskModal } from "../TaskModal";
import { projectsApi } from "../../../../lib/projects";
import type { ProjectTask } from "../../../../lib/projects";

const sampleTask: ProjectTask = {
  id: "t1", project_id: "p1", parent_task_id: null,
  title: "Spec the kanban", body: "Body text",
  status: "open", priority: 0, labels: [], assignee_id: null,
  claimed_by: null, claimed_at: null, closed_at: null, closed_by: null, close_reason: null,
  element_id: null,
  created_by: "u", created_at: 0, updated_at: 0,
};

beforeEach(() => {
  vi.spyOn(projectsApi.tasks, "list").mockResolvedValue([]);
  vi.spyOn(projectsApi.tasks, "listRelationships").mockResolvedValue([]);
  vi.spyOn(projectsApi.tasks, "listComments").mockResolvedValue([]);
  vi.spyOn(projectsApi.tasks, "getContext").mockResolvedValue({
    project: { id: "p1", name: null, description: null },
    ancestry: [],
    blockers: [],
    is_blocked: false,
  });
});

describe("TaskModal", () => {
  it("renders nothing when taskId is null", () => {
    const { container } = render(<TaskModal projectId="p1" taskId={null} currentUserId="u1" onClose={() => {}} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders title + close button when taskId is set", async () => {
    vi.spyOn(projectsApi.tasks, "list").mockResolvedValue([sampleTask]);
    render(<TaskModal projectId="p1" taskId="t1" currentUserId="u1" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("Spec the kanban")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Close/i })).toBeInTheDocument();
  });

  it("calls onClose when Escape is pressed", async () => {
    const onClose = vi.fn();
    vi.spyOn(projectsApi.tasks, "list").mockResolvedValue([sampleTask]);
    render(<TaskModal projectId="p1" taskId="t1" currentUserId="u1" onClose={onClose} />);
    await waitFor(() => expect(screen.getByText("Spec the kanban")).toBeInTheDocument());
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(onClose).toHaveBeenCalled();
  });
});
