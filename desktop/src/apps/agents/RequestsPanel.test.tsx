import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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

describe("RequestsPanel", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
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
    // Scopes rendered as chips
    expect(screen.getByText("a2a_send")).toBeInTheDocument();
    expect(screen.getByText("project_tasks")).toBeInTheDocument();
  });

  it("Approve button calls the existing approve route", async () => {
    render(<RequestsPanel />);
    await waitFor(() => {
      expect(screen.getByText("Agent Alpha")).toBeInTheDocument();
    });
    const approveButtons = screen.getAllByRole("button", { name: /approve/i });
    approveButtons[0].click();
    // The mock for approve should have been called; verify via the global fetch mock
    const fetchCalls = (globalThis.fetch as any).mock.calls;
    const approveCall = fetchCalls.find((call: any[]) =>
      call[0].includes("/approve")
    );
    expect(approveCall).toBeDefined();
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
});
