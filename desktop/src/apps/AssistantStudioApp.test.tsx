import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AssistantStudioApp } from "./AssistantStudioApp";

const mockAgents = [{ name: "hermes" }, { name: "other" }];

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
    children: React.ReactNode;
    onClick?: () => void;
    "aria-label"?: string;
  }) => (
    <button onClick={onClick} aria-label={ariaLabel}>
      {children}
    </button>
  ),
}));

beforeEach(() => {
  localStorage.clear();
});

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
    await waitFor(() => {
      expect(
        screen.getByLabelText("Select the agent to act as your personal assistant")
      ).toBeInTheDocument();
    });
  });

  it("adding a journal entry shows the text in the list", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(mockAgents) }));
    render(<AssistantStudioApp windowId="win-1" />);
    await waitFor(() => {
      expect(screen.getByLabelText("Select the agent to act as your personal assistant")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Journal" }));
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Journal" })).toBeInTheDocument();
    });
    const textarea = screen.getByLabelText("New journal entry");
    fireEvent.change(textarea, { target: { value: "Test journal entry text" } });
    fireEvent.click(screen.getByRole("button", { name: "Add entry" }));
    await waitFor(() => {
      expect(screen.getByText("Test journal entry text")).toBeInTheDocument();
    });
  });
});