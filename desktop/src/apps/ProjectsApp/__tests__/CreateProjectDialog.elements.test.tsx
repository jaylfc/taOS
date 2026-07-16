import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CreateProjectDialog } from "../CreateProjectDialog";

vi.mock("@/lib/projects", () => ({
  projectsApi: {
    create: vi.fn().mockResolvedValue({ id: "p1", slug: "p1" }),
    elements: { create: vi.fn().mockResolvedValue({ id: "e1" }) },
  },
}));

import { projectsApi } from "@/lib/projects";

describe("CreateProjectDialog step two (elements)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("skips elements when the primary Create button is used (today's behaviour)", async () => {
    const onCreated = vi.fn();
    render(<CreateProjectDialog onClose={() => {}} onCreated={onCreated} />);

    fireEvent.change(screen.getByRole("textbox", { name: /name/i }), {
      target: { value: "T-Shirt Business" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(projectsApi.create).toHaveBeenCalledTimes(1);
    expect(projectsApi.elements.create).not.toHaveBeenCalled();
  });

  it("creates the project and each named element on the second step", async () => {
    const onCreated = vi.fn();
    render(<CreateProjectDialog onClose={() => {}} onCreated={onCreated} />);

    fireEvent.change(screen.getByRole("textbox", { name: /name/i }), {
      target: { value: "T-Shirt Business" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add elements" }));

    // Step two renders an element row.
    fireEvent.change(screen.getByRole("textbox", { name: /Element 1 name/i }), {
      target: { value: "Designs" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: /Element 1 type/i }), {
      target: { value: "design" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create project & elements" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(projectsApi.create).toHaveBeenCalledTimes(1);
    expect(projectsApi.elements.create).toHaveBeenCalledWith("p1", {
      name: "Designs",
      slug: "designs",
      type: "design",
    });
  });

  it("only creates elements whose name is non-blank", async () => {
    const onCreated = vi.fn();
    render(<CreateProjectDialog onClose={() => {}} onCreated={onCreated} />);

    fireEvent.change(screen.getByRole("textbox", { name: /name/i }), {
      target: { value: "T-Shirt Business" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add elements" }));
    // Leave the first row blank, add a second row with a name.
    fireEvent.click(screen.getByRole("button", { name: /Add another element/ }));
    const nameInputs = screen.getAllByRole("textbox", { name: /Element .* name/ });
    fireEvent.change(nameInputs[1]!, { target: { value: "Website" } });
    fireEvent.click(screen.getByRole("button", { name: "Create project & elements" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(projectsApi.elements.create).toHaveBeenCalledTimes(1);
    expect(projectsApi.elements.create).toHaveBeenCalledWith("p1", {
      name: "Website",
      slug: "website",
      type: "generic",
    });
  });
});
