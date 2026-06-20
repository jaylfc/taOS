import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ConsentNotification } from "./ConsentNotification";

const mockRequest = {
  id: "req-1",
  identity_claim: "agent-alpha",
  framework: "openai",
  requested_scopes: ["memory_read", "files_read"],
  requested_skills: ["search"],
  reason: "Need access for task",
  duration_secs: 3600,
  project_id: "proj-42",
  status: "pending" as const,
  created_ts: "2025-01-01T00:00:00Z",
};

function mockFetch(opts: { admin?: boolean; requests?: object[] } = {}) {
  const { admin = true, requests = [mockRequest] } = opts;
  return vi.fn((url: string) => {
    if (url === "/auth/status") {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ user: { is_admin: admin } }),
      });
    }
    if (url.includes("/api/agents/auth-requests")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ requests }),
      });
    }
    return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
  }) as unknown as typeof fetch;
}

describe("ConsentNotification", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the access request modal with agent details", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<ConsentNotification />);

    expect(await screen.findByText("Access request")).toBeInTheDocument();
    expect(screen.getByText("agent-alpha")).toBeInTheDocument();
    expect(screen.getByText("openai")).toBeInTheDocument();
    expect(screen.getByText('"Need access for task"')).toBeInTheDocument();
    expect(screen.getByText("1 h")).toBeInTheDocument();
  });

  it("renders requested scopes and skills", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<ConsentNotification />);

    expect(await screen.findByText("memory_read")).toBeInTheDocument();
    expect(screen.getByText("files_read")).toBeInTheDocument();
    expect(screen.getByText("search")).toBeInTheDocument();
  });

  it("calls the deny endpoint when Deny is clicked", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<ConsentNotification />);

    await screen.findByText("Access request");
    fireEvent.click(screen.getByRole("button", { name: /deny access request/i }));

    await waitFor(() => {
      const denyCall = fetchMock.mock.calls.find(
        ([url, opts]) =>
          typeof url === "string" &&
          url.includes("/deny") &&
          (opts as RequestInit)?.method === "POST"
      );
      expect(denyCall).toBeDefined();
    });
  });

  it("does not render when user is not admin", async () => {
    vi.stubGlobal("fetch", mockFetch({ admin: false }));
    const { container } = render(<ConsentNotification />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });
});
