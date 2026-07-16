import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ProjectBoard } from "../ProjectBoard";
import { projectsApi } from "../../../../lib/projects";

beforeEach(() => {
  vi.spyOn(projectsApi.tasks, "list").mockResolvedValue([]);
  vi.spyOn(projectsApi.elements, "list").mockResolvedValue([]);
  vi.spyOn(projectsApi, "subscribeEvents").mockReturnValue(() => {});
});

describe("ProjectBoard", () => {
  it("renders three columns (Ready / Claimed / Closed) in Kanban mode", async () => {
    render(<ProjectBoard projectId="p1" currentUserId="u1" />);
    fireEvent.click(await screen.findByRole("tab", { name: /Kanban/ }));
    await waitFor(() => {
      expect(screen.getByRole("region", { name: /Ready/ })).toBeInTheDocument();
      expect(screen.getByRole("region", { name: /Claimed/ })).toBeInTheDocument();
      expect(screen.getByRole("region", { name: /Closed/ })).toBeInTheDocument();
    });
  });

  it("toggles between Lanes and Kanban modes", async () => {
    render(<ProjectBoard projectId="p1" currentUserId="u1" />);
    fireEvent.click(await screen.findByRole("tab", { name: /Kanban/ }));
    expect(screen.getByRole("tab", { name: /Kanban/ })).toHaveAttribute("aria-selected", "true");
  });
});
