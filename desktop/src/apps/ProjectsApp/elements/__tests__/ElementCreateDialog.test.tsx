import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ElementCreateDialog } from "../ElementCreateDialog";

vi.mock("@/lib/projects", () => ({
  projectsApi: {
    elements: { create: vi.fn().mockResolvedValue({ id: "e1" }) },
  },
}));

import { projectsApi } from "@/lib/projects";

describe("ElementCreateDialog", () => {
  beforeEach(() => vi.clearAllMocks());

  it("creates an element with the typed name and default generic type", async () => {
    const onCreated = vi.fn();
    render(
      <ElementCreateDialog
        projectId="p1"
        memberOptions={[]}
        onClose={() => {}}
        onCreated={onCreated}
      />,
    );
    fireEvent.change(screen.getByRole("textbox", { name: /name/i }), {
      target: { value: "Designs" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(projectsApi.elements.create).toHaveBeenCalledWith("p1", {
      name: "Designs",
      slug: "designs",
      type: "generic",
      assignee_id: null,
      description: "",
    });
  });

  it("includes the chosen type and owner", async () => {
    const onCreated = vi.fn();
    render(
      <ElementCreateDialog
        projectId="p1"
        memberOptions={[{ id: "a1", label: "Web Agent" }]}
        onClose={() => {}}
        onCreated={onCreated}
      />,
    );
    fireEvent.change(screen.getByRole("textbox", { name: /name/i }), {
      target: { value: "Site" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: /type/i }), {
      target: { value: "website" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: /owner/i }), {
      target: { value: "a1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(projectsApi.elements.create).toHaveBeenCalledWith("p1", {
      name: "Site",
      slug: "site",
      type: "website",
      assignee_id: "a1",
      description: "",
    });
  });

  it("shows an error and does not create when the name is blank", async () => {
    const onCreated = vi.fn();
    render(
      <ElementCreateDialog
        projectId="p1"
        memberOptions={[]}
        onClose={() => {}}
        onCreated={onCreated}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Name is required.");
    expect(projectsApi.elements.create).not.toHaveBeenCalled();
    expect(onCreated).not.toHaveBeenCalled();
  });
});
