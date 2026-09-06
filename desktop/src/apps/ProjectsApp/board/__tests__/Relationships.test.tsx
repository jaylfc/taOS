import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { Relationships } from "../modal/Relationships";
import { projectsApi } from "../../../../lib/projects";
import type { ProjectRelationship, TaskContext } from "../../../../lib/projects";

const emptyContext: TaskContext = {
  project: { id: "p1", name: null, description: null },
  ancestry: [],
  blockers: [],
  is_blocked: false,
};

beforeEach(() => {
  vi.spyOn(projectsApi.tasks, "listRelationships").mockResolvedValue([]);
  vi.spyOn(projectsApi.tasks, "getContext").mockResolvedValue(emptyContext);
});

describe("Relationships", () => {
  it("renders nothing when there is no context, no ancestry, and no relationships", async () => {
    const { container } = render(<Relationships projectId="p1" taskId="t1" />);
    await waitFor(() => expect(projectsApi.tasks.getContext).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it("renders the ancestry breadcrumb when ancestors exist", async () => {
    vi.spyOn(projectsApi.tasks, "getContext").mockResolvedValue({
      project: { id: "p1", name: "Launch", description: "" },
      ancestry: [
        { id: "a1", title: "Epic", status: "open" },
        { id: "a2", title: "Story", status: "open" },
      ],
      blockers: [],
      is_blocked: false,
    });
    render(<Relationships projectId="p1" taskId="t1" />);
    await waitFor(() => expect(screen.getByText("Launch › Epic › Story")).toBeInTheDocument());
  });

  it("renders blockers and flags is_blocked", async () => {
    vi.spyOn(projectsApi.tasks, "getContext").mockResolvedValue({
      project: { id: "p1", name: "Launch", description: "" },
      ancestry: [],
      blockers: [{ id: "b1", title: "Blocking task", status: "open" }],
      is_blocked: true,
    });
    render(<Relationships projectId="p1" taskId="t1" />);
    await waitFor(() => expect(screen.getByText(/Blocking task/)).toBeInTheDocument());
    expect(screen.getByRole("heading", { name: /Blockers/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Blocking task — blocking")).toBeInTheDocument();
  });

  it("renders the plain relationships list as before", async () => {
    const rel: ProjectRelationship = {
      id: "rel1", project_id: "p1", from_task_id: "t1", to_task_id: "t2",
      kind: "relates_to", created_by: "u", created_at: 0,
    };
    vi.spyOn(projectsApi.tasks, "listRelationships").mockImplementation(async (_pid, _tid, direction) =>
      direction === "from" ? [rel] : [],
    );
    render(<Relationships projectId="p1" taskId="t1" />);
    await waitFor(() => expect(screen.getByText("relates_to")).toBeInTheDocument());
  });
});
