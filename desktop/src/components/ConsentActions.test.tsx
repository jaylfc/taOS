import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ConsentActions } from "./ConsentActions";

function okFetch() {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ status: "ok" }),
  });
}

describe("ConsentActions", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders Allow and Deny buttons", () => {
    vi.stubGlobal("fetch", okFetch());
    render(<ConsentActions requestId="req-1" scopes={["memory_read"]} />);
    expect(screen.getByRole("button", { name: /allow/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /deny/i })).toBeInTheDocument();
  });

  it("posts the requested scopes to the approve endpoint and calls onResolved", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    const onResolved = vi.fn();
    render(
      <ConsentActions
        requestId="req-1"
        scopes={["memory_read", "a2a_send"]}
        onResolved={onResolved}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /allow/i }));

    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agents/auth-requests/req-1/approve",
      expect.objectContaining({ method: "POST" }),
    );
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({
      granted_scopes: ["memory_read", "a2a_send"],
    });
  });

  it("posts to the deny endpoint with no body", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    const onResolved = vi.fn();
    render(<ConsentActions requestId="req-2" scopes={["files_read"]} onResolved={onResolved} />);

    fireEvent.click(screen.getByRole("button", { name: /deny/i }));

    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agents/auth-requests/req-2/deny",
      expect.objectContaining({ method: "POST" }),
    );
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.body).toBeUndefined();
  });

  it("surfaces an error and does not call onResolved on a failed request", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: () => Promise.resolve({ detail: "forbidden" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const onResolved = vi.fn();
    render(<ConsentActions requestId="req-3" scopes={[]} onResolved={onResolved} />);

    fireEvent.click(screen.getByRole("button", { name: /allow/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("forbidden"));
    expect(onResolved).not.toHaveBeenCalled();
  });

  it("shows a project picker for a project_tasks request and sends the chosen project_id", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (String(url).startsWith("/api/projects")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ items: [{ id: "p1", name: "taOS Core" }] }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: "ok" }) });
    });
    vi.stubGlobal("fetch", fetchMock);
    const onResolved = vi.fn();
    render(
      <ConsentActions requestId="req-p" scopes={["project_tasks", "a2a_send"]} onResolved={onResolved} />,
    );
    // The picker appears and the single project is auto-selected.
    await screen.findByLabelText(/Grant project_tasks for project/i);

    fireEvent.click(screen.getByRole("button", { name: /allow/i }));
    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1));

    const approveCall = fetchMock.mock.calls.find((c) => String(c[0]).includes("/approve"));
    expect(approveCall).toBeTruthy();
    expect(JSON.parse((approveCall![1] as RequestInit).body as string)).toEqual({
      granted_scopes: ["project_tasks", "a2a_send"],
      project_id: "p1",
    });
  });

  it("gates Allow until a project is chosen when several projects exist", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (String(url).startsWith("/api/projects")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({ items: [{ id: "p1", name: "Alpha" }, { id: "p2", name: "Bravo" }] }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: "ok" }) });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ConsentActions requestId="req-m" scopes={["project_tasks"]} />);

    const select = await screen.findByLabelText(/Grant project_tasks for project/i);
    expect(screen.getByRole("button", { name: /allow/i })).toBeDisabled();

    fireEvent.change(select, { target: { value: "p2" } });
    expect(screen.getByRole("button", { name: /allow/i })).not.toBeDisabled();
  });

  it("creates a project inline and grants project_tasks against the new project", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const u = String(url);
      if (u === "/api/projects?status=active") {
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ items: [] }) });
      }
      if (u === "/api/projects" && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ id: "pnew", name: "Fresh" }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: "ok" }) });
    });
    vi.stubGlobal("fetch", fetchMock);
    const onResolved = vi.fn();
    render(<ConsentActions requestId="req-c" scopes={["project_tasks"]} onResolved={onResolved} />);

    await screen.findByLabelText(/Grant project_tasks for project/i);
    expect(screen.getByRole("button", { name: /allow/i })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /new/i }));
    fireEvent.change(screen.getByLabelText(/New project name/i), { target: { value: "Fresh" } });
    fireEvent.click(screen.getByRole("button", { name: /allow/i }));
    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1));

    const approve = fetchMock.mock.calls.find((c) => String(c[0]).includes("/approve"));
    expect(approve).toBeTruthy();
    expect(JSON.parse((approve![1] as RequestInit).body as string)).toEqual({
      granted_scopes: ["project_tasks"],
      project_id: "pnew",
    });
  });
});
