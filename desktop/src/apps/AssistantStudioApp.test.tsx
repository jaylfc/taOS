import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import type { ReactNode } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AssistantStudioApp } from "./AssistantStudioApp";

const mockAgents = [{ name: "hermes" }, { name: "other" }];

const PA_LABEL = "Select the agent to act as your personal assistant";

vi.mock("lucide-react", () => ({
  LayoutDashboard: () => <span />,
  NotebookPen: () => <span />,
  CalendarDays: () => <span />,
  ListTodo: () => <span />,
  MessagesSquare: () => <span />,
  PenTool: () => <span />,
  FolderKanban: () => <span />,
  UserRound: () => <span />,
  Plus: () => <span />,
  Check: () => <span />,
  Trash2: () => <span />,
  ExternalLink: () => <span />,
}));

vi.mock("@/components/ui", () => ({
  Button: ({
    children,
    onClick,
    "aria-label": ariaLabel,
  }: {
    children: ReactNode;
    onClick?: () => void;
    "aria-label"?: string;
  }) => (
    <button onClick={onClick} aria-label={ariaLabel}>
      {children}
    </button>
  ),
}));

// The component fetches /api/agents on mount in EVERY test, so every test needs
// the stub -- without it the others hit jsdom's real fetch and update state
// outside act(). Stubbing per-test also leaked the mock into whatever ran next,
// so it is installed and torn down here instead.
beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(mockAgents) }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Wait until the mount fetch has resolved and defaulted the PA.
 *
 * The selector exists on the first render, so waiting for it proves nothing:
 * the journal and task controls are `disabled={!pa}`, and fireEvent on a
 * disabled control silently does nothing. Waiting for the PA to actually be
 * set is what makes those tests deterministic rather than timing-dependent.
 */
async function waitForPa() {
  const select = await screen.findByLabelText(PA_LABEL);
  await waitFor(() => expect((select as HTMLSelectElement).value).toBe("hermes"));
  return select;
}

describe("AssistantStudioApp", () => {
  it("renders all 7 rail sections", () => {
    render(<AssistantStudioApp windowId="win-1" />);
    expect(screen.getByRole("button", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Journal" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Calendar" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tasks" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Comms" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Canvas" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Deliverables" })).toBeInTheDocument();
  });

  it("clicking a rail button switches the visible panel", async () => {
    render(<AssistantStudioApp windowId="win-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Journal" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Journal" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Calendar" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Calendar and time" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Tasks" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Tasks" })).toBeInTheDocument();
    });
  });

  it("the PA selector renders with an accessible label", async () => {
    render(<AssistantStudioApp windowId="win-1" />);
    expect(await screen.findByLabelText(PA_LABEL)).toBeInTheDocument();
  });

  it("defaults the PA to hermes once the agent list loads", async () => {
    render(<AssistantStudioApp windowId="win-1" />);
    const select = await waitForPa();
    expect((select as HTMLSelectElement).value).toBe("hermes");
  });

  it("adding a journal entry shows the text in the list", async () => {
    render(<AssistantStudioApp windowId="win-1" />);
    await waitForPa();
    fireEvent.click(screen.getByRole("button", { name: "Journal" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Journal" })).toBeInTheDocument();
    });

    // Assert the controls are live before driving them. Without this the test
    // can pass on a disabled textarea by accident and stops testing anything.
    const textarea = screen.getByLabelText("New journal entry") as HTMLTextAreaElement;
    expect(textarea).not.toBeDisabled();

    fireEvent.change(textarea, { target: { value: "Test journal entry text" } });
    const addButton = screen.getByRole("button", { name: "Add entry" });
    expect(addButton).not.toBeDisabled();
    fireEvent.click(addButton);

    await waitFor(() => {
      expect(screen.getByText("Test journal entry text")).toBeInTheDocument();
    });
  });
});