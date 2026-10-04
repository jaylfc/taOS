import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import React from "react";

// Stub heavy deps
vi.mock("@/lib/framework-api", () => ({ fetchLatestFrameworks: async () => ({}) }));
vi.mock("@/lib/taos-agent-api", () => ({ fetchTaosAgentConfig: async () => ({}) }));
vi.mock("@/components/ui", () => ({
  Button: ({ children, onClick, className, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & { children?: React.ReactNode }) => (
    <button onClick={onClick} className={className} {...rest}>{children}</button>
  ),
  Card: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
  Input: (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
  Tabs: ({ children, value, onValueChange }: { children: React.ReactNode; value?: string; onValueChange?: (v: string) => void }) => {
    const [active, setActive] = React.useState(value || "requests");
    React.useEffect(() => {
      if (value) setActive(value);
    }, [value]);
    const handleClick = (v: string) => {
      setActive(v);
      onValueChange?.(v);
    };
    return (
      <div>
        {React.Children.map(children, (child: any) => {
          if (!child || typeof child !== "object") return child;
          const childValue = child.props?.value;
          if (childValue) {
            return React.cloneElement(child, {
              onClick: () => handleClick(childValue),
              "data-active": active === childValue ? "true" : "false",
            });
          }
          return child;
        })}
        {React.Children.map(children, (child: any) => {
          if (!child || typeof child !== "object") return child;
          if (child.props?.value === active) return child;
          return null;
        })}
      </div>
    );
  },
  TabsContent: ({ children, value }: { children: React.ReactNode; value?: string }) => (
    <div data-tab-content={value}>{children}</div>
  ),
  TabsList: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsTrigger: ({ children, value, onClick }: { children: React.ReactNode; value?: string; onClick?: () => void }) => (
    <button data-tab-trigger={value} onClick={onClick}>{children}</button>
  ),
}));

import { RequestsPanel } from "../agents/RequestsPanel";

const MOCK_REQUESTS = [
  {
    id: "req-1",
    canonical_id: "agent-alpha",
    requested_scopes: ["a2a_send", "a2a_receive"],
    project_id: null,
    reason: "Need messaging",
    status: "pending",
    created_ts: new Date(Date.now() - 60_000).toISOString(),
    agent_display_name: "Agent Alpha",
  },
  {
    id: "req-2",
    canonical_id: "agent-beta",
    requested_scopes: ["project_tasks"],
    project_id: "proj-1",
    reason: "Project access",
    status: "pending",
    created_ts: new Date(Date.now() - 120_000).toISOString(),
    agent_display_name: "Agent Beta",
  },
];

/** Answer GET /api/agents/scope-vocabulary (ConsentActions reads it before it
 *  will enable Allow), or null for any other URL. */
function vocab(url: string) {
  if (!url.startsWith("/api/agents/scope-vocabulary")) return null;
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () =>
      Promise.resolve({
        valid_scopes: ["a2a_receive", "a2a_send", "project_tasks"],
        project_scopes: ["project_tasks"],
      }),
  } as unknown as Response);
}

/** Every Allow button, once the first one (Agent Alpha's, no project scope)
 *  is enabled, i.e. the scope vocabulary has been read. */
async function allowEnabled() {
  await waitFor(() =>
    expect(screen.getAllByRole("button", { name: /allow/i })[0]).not.toBeDisabled(),
  );
  return screen.getAllByRole("button", { name: /allow/i });
}

describe("RequestsPanel", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      const v = vocab(url);
      if (v) return v;
      if (url.startsWith("/api/agents/scope-requests")) {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ requests: MOCK_REQUESTS }),
        } as unknown as Response);
      }
      if (url.includes("/scope-requests/") && url.includes("/approve")) {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ status: "accepted" }),
        } as unknown as Response);
      }
      return Promise.resolve({
        ok: false,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
      } as unknown as Response);
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders rows from a mocked list", async () => {
    render(<RequestsPanel />);
    await waitFor(() => {
      expect(screen.getByText("Agent Alpha")).toBeInTheDocument();
    });
    expect(screen.getByText("Agent Beta")).toBeInTheDocument();
    // Scopes rendered by the consent surface (Requested and Granted rows)
    expect(screen.getAllByText("a2a_send").length).toBeGreaterThan(0);
    expect(screen.getAllByText("project_tasks").length).toBeGreaterThan(0);
  });

  it("Allow calls the existing approve route", async () => {
    render(<RequestsPanel />);
    await waitFor(() => {
      expect(screen.getByText("Agent Alpha")).toBeInTheDocument();
    });
    const allowButtons = await allowEnabled();
    fireEvent.click(allowButtons[0]);
    await waitFor(() => {
      const fetchCalls = (globalThis.fetch as any).mock.calls;
      expect(fetchCalls.some((call: any[]) => call[0].includes("/approve"))).toBe(true);
    });
    const approveCall = (globalThis.fetch as any).mock.calls.find((call: any[]) =>
      call[0].includes("/approve"),
    );
    expect(approveCall[0]).toBe(
      "/api/agents/registry/agent-alpha/scope-requests/req-1/approve",
    );
    // No project-bound scope requested, so no project_id is sent.
    expect(JSON.parse(approveCall[1].body)).toEqual({
      granted_scopes: ["a2a_send", "a2a_receive"],
    });
  });

  it("failed fetch renders the error, not the empty state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() =>
      Promise.resolve({
        ok: false,
        status: 500,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
      } as unknown as Response)
    ));
    render(<RequestsPanel />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("500");
    });
    expect(screen.queryByText("No pending requests")).not.toBeInTheDocument();
  });

  it("failed approve keeps the rows rendered and shows the action error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      const v = vocab(url);
      if (v) return v;
      if (url.startsWith("/api/agents/scope-requests")) {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ requests: MOCK_REQUESTS }),
        } as unknown as Response);
      }
      if (url.includes("/scope-requests/") && url.includes("/approve")) {
        return Promise.resolve({
          ok: false,
          status: 400,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ detail: "bad request" }),
        } as unknown as Response);
      }
      return Promise.resolve({
        ok: false,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
      } as unknown as Response);
    }));
    render(<RequestsPanel />);
    await waitFor(() => {
      expect(screen.getByText("Agent Alpha")).toBeInTheDocument();
    });
    const allowButtons = await allowEnabled();
    fireEvent.click(allowButtons[0]);
    await waitFor(() => {
      expect(screen.getByText(/bad request/i)).toBeInTheDocument();
    });
    expect(screen.getByText("Agent Alpha")).toBeInTheDocument();
  });

  it("successful retry clears the previous action error", async () => {
    let approveAttempt = 0;
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      const v = vocab(url);
      if (v) return v;
      if (url.startsWith("/api/agents/scope-requests")) {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ requests: MOCK_REQUESTS }),
        } as unknown as Response);
      }
      if (url.includes("/scope-requests/") && url.includes("/approve")) {
        approveAttempt += 1;
        if (approveAttempt === 1) {
          return Promise.resolve({
            ok: false,
            status: 400,
            headers: { get: () => "application/json" },
            json: () => Promise.resolve({ detail: "transient error" }),
          } as unknown as Response);
        }
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ status: "accepted" }),
        } as unknown as Response);
      }
      return Promise.resolve({
        ok: false,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
      } as unknown as Response);
    }));
    render(<RequestsPanel />);
    await waitFor(() => {
      expect(screen.getByText("Agent Alpha")).toBeInTheDocument();
    });
    const allowButtons = await allowEnabled();
    fireEvent.click(allowButtons[0]);
    await waitFor(() => {
      expect(screen.getByText(/transient error/i)).toBeInTheDocument();
    });
    await waitFor(() => expect(allowButtons[0]).not.toBeDisabled());
    fireEvent.click(allowButtons[0]);
    await waitFor(() => {
      expect(screen.queryByText(/transient error/i)).not.toBeInTheDocument();
    });
  });

  // tsk-ce2stw: the Requests tab must not reopen the unfixable-400 path that
  // ConsentActions closed. A project_tasks_update-ONLY request is the defect's
  // exact shape: project_tasks present is the case that already worked, so it
  // cannot catch this.
  describe("project-bound scope request (tsk-ce2stw)", () => {
    const UPDATE_ONLY = {
      id: "req-upd",
      canonical_id: "taos-dev",
      requested_scopes: ["project_tasks_update"],
      project_id: null as string | null,
      reason: "Edit my board",
      status: "pending",
      created_ts: new Date(Date.now() - 60_000).toISOString(),
      agent_display_name: "taos-dev",
    };

    function stubServer(row: typeof UPDATE_ONLY) {
      const fetchMock = vi.fn().mockImplementation((url: string) => {
        if (url.startsWith("/api/agents/scope-vocabulary")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                valid_scopes: ["a2a_send", "project_tasks", "project_tasks_update"],
                project_scopes: ["project_tasks", "project_tasks_update"],
              }),
          } as unknown as Response);
        }
        if (url.startsWith("/api/agents/scope-requests")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ requests: [row] }),
          } as unknown as Response);
        }
        if (url.startsWith("/api/projects")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                items: [
                  { id: "prj-web", name: "taOS Website" },
                  { id: "prj-utbsh7", name: "Lead Board" },
                ],
              }),
          } as unknown as Response);
        }
        if (url.includes("/approve")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ status: "accepted" }),
          } as unknown as Response);
        }
        return Promise.resolve({
          ok: false,
          status: 404,
          json: () => Promise.resolve({}),
        } as unknown as Response);
      });
      vi.stubGlobal("fetch", fetchMock);
      return fetchMock;
    }

    const approveCalls = (m: ReturnType<typeof vi.fn>) =>
      m.mock.calls.filter((c: any[]) => String(c[0]).includes("/approve"));

    it("renders the project picker for project_tasks_update alone and blocks approve until a project is chosen", async () => {
      const fetchMock = stubServer({ ...UPDATE_ONLY, project_id: null });
      render(<RequestsPanel />);
      const picker = (await screen.findByLabelText(
        /Grant project access for/i,
      )) as HTMLSelectElement;
      await waitFor(() =>
        expect(screen.getByRole("option", { name: "taOS Website" })).toBeInTheDocument(),
      );
      const allow = screen.getByRole("button", { name: /allow/i });
      expect(allow).toBeDisabled();
      fireEvent.click(allow);
      expect(approveCalls(fetchMock)).toHaveLength(0);

      fireEvent.change(picker, { target: { value: "prj-web" } });
      await waitFor(() => expect(allow).not.toBeDisabled());
      fireEvent.click(allow);
      await waitFor(() => expect(approveCalls(fetchMock)).toHaveLength(1));
      const call = approveCalls(fetchMock)[0];
      expect(String(call[0])).toBe(
        "/api/agents/registry/taos-dev/scope-requests/req-upd/approve",
      );
      expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({
        granted_scopes: ["project_tasks_update"],
        project_id: "prj-web",
      });
    });

    it("names the requested project by its human-readable name, not the raw id", async () => {
      stubServer({ ...UPDATE_ONLY, project_id: "prj-utbsh7" });
      render(<RequestsPanel />);
      const line = await screen.findByText(/Requesting access for/i);
      expect(line).toHaveTextContent("Lead Board");
    });

    it("blocks approve with a visible reason when the scope vocabulary is unavailable", async () => {
      const fetchMock = vi.fn().mockImplementation((url: string) => {
        if (url.startsWith("/api/agents/scope-requests")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ requests: [UPDATE_ONLY] }),
          } as unknown as Response);
        }
        if (url.includes("/approve")) {
          return Promise.resolve({
            ok: false,
            status: 400,
            json: () =>
              Promise.resolve({
                detail: "project_id is required when granting ['project_tasks_update']",
              }),
          } as unknown as Response);
        }
        return Promise.resolve({
          ok: false,
          status: 503,
          json: () => Promise.resolve({}),
        } as unknown as Response);
      });
      vi.stubGlobal("fetch", fetchMock);
      render(<RequestsPanel />);
      await waitFor(() =>
        expect(
          screen.getByText(/Could not confirm which scopes need a project/i),
        ).toBeInTheDocument(),
      );
      const allow = screen.getByRole("button", { name: /allow/i });
      expect(allow).toBeDisabled();
      fireEvent.click(allow);
      expect(approveCalls(fetchMock)).toHaveLength(0);
    });
  });

  it("filter=all with zero rows does not render 'No pending requests'", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.startsWith("/api/agents/scope-requests")) {
        return Promise.resolve({
          ok: true,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ requests: [] }),
        } as unknown as Response);
      }
      return Promise.resolve({
        ok: false,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve({}),
      } as unknown as Response);
    }));
    render(<RequestsPanel />);
    await waitFor(() => {
      expect(screen.queryByText("Agent Alpha")).not.toBeInTheDocument();
    });
    const allTab = screen.getByRole("tab", { name: /all/i });
    fireEvent.click(allTab);
    await waitFor(() => {
      expect(screen.getByText("No scope requests have been submitted yet.")).toBeInTheDocument();
    });
    expect(screen.queryByText("No pending requests")).not.toBeInTheDocument();
  });
});
