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

describe("CreateProjectDialog stacking (#1605)", () => {
  it("portals straight to document.body, not nested under a window's DOM subtree", () => {
    render(<CreateProjectDialog onClose={vi.fn()} onCreated={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: /create project/i });
    expect(dialog.parentElement).toBe(document.body);
  });

  it("uses the app-wide top overlay layer so it always renders above windows", () => {
    // Window.tsx applies an ever-increasing zIndex (see stores/process-store.ts
    // nextZIndex, which starts at 1 and climbs by 1 on every open/focus/restore/
    // maximize with no ceiling). A hardcoded low z-index like Tailwind's "z-50"
    // gets outrun by any long-lived window session, which is exactly what made
    // the dialog render behind the Projects window in #1605. The fix reuses the
    // same "always on top" layer already used by ContextMenu, NotificationToast,
    // and NotificationCentre for the same reason.
    render(<CreateProjectDialog onClose={vi.fn()} onCreated={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: /create project/i });
    expect(dialog).toHaveClass("z-[10001]");
  });
});
