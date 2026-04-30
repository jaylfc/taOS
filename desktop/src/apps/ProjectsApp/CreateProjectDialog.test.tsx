import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { CreateProjectDialog } from "./CreateProjectDialog";

vi.mock("@/lib/projects", () => ({
  projectsApi: { create: vi.fn() },
}));

describe("CreateProjectDialog slug auto-tracking", () => {
  it("auto-fills slug from name on every keystroke until user edits slug", () => {
    render(<CreateProjectDialog onClose={vi.fn()} onCreated={vi.fn()} />);
    const nameInput = screen.getByRole("textbox", { name: /name/i });
    const slugInput = screen.getByRole("textbox", { name: /slug/i });

    fireEvent.change(nameInput, { target: { value: "H" } });
    expect((slugInput as HTMLInputElement).value).toBe("h");

    fireEvent.change(nameInput, { target: { value: "Hello" } });
    expect((slugInput as HTMLInputElement).value).toBe("hello");

    fireEvent.change(nameInput, { target: { value: "Hello World" } });
    expect((slugInput as HTMLInputElement).value).toBe("hello-world");
  });

  it("stops auto-tracking once user manually edits the slug", () => {
    render(<CreateProjectDialog onClose={vi.fn()} onCreated={vi.fn()} />);
    const nameInput = screen.getByRole("textbox", { name: /name/i });
    const slugInput = screen.getByRole("textbox", { name: /slug/i });

    fireEvent.change(nameInput, { target: { value: "Hello" } });
    expect((slugInput as HTMLInputElement).value).toBe("hello");

    // User edits slug manually
    fireEvent.change(slugInput, { target: { value: "my-project" } });
    expect((slugInput as HTMLInputElement).value).toBe("my-project");

    // Further name edits should NOT overwrite the user's slug
    fireEvent.change(nameInput, { target: { value: "Hello World" } });
    expect((slugInput as HTMLInputElement).value).toBe("my-project");
  });
});
