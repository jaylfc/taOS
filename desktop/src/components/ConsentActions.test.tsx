import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ConsentActions, computeScopeDiff, consentPayload } from "./ConsentActions";

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
    await screen.findByLabelText(/Grant project access for/i);

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

    const select = await screen.findByLabelText(/Grant project access for/i);
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

    await screen.findByLabelText(/Grant project access for/i);
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

  it("preselects and labels the requested project when it resolves", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (String(url).startsWith("/api/projects")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              items: [
                { id: "prj-other", name: "Other" },
                { id: "prj-btrdrl", name: "BTRDRL" },
              ],
            }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ status: "ok" }),
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const onResolved = vi.fn();
    render(
      <ConsentActions
        requestId="req-real"
        scopes={["project_tasks"]}
        requestedProjectId="prj-btrdrl"
        onResolved={onResolved}
      />,
    );

    await screen.findByLabelText(/Grant project access for/i);
    // The resolved requested project is shown by NAME, not just id.
    const requestLine = screen.getByText(/Requesting access for/i);
    expect(requestLine).toHaveTextContent("BTRDRL");
    expect(requestLine).toHaveTextContent("prj-btrdrl");
    // The picker is preselected to the requested project.
    const select = screen.getByLabelText(/Grant project access for/i) as HTMLSelectElement;
    expect(select.value).toBe("prj-btrdrl");
    // Allow is enabled because a valid target is resolved.
    expect(
      screen.getByRole("button", { name: /allow/i }),
    ).not.toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /allow/i }));
    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1));
    expect(
      JSON.parse(
        (
          fetchMock.mock.calls.find((c) =>
            String(c[0]).includes("/approve"),
          )![1] as RequestInit
        ).body as string,
      ),
    ).toEqual({
      granted_scopes: ["project_tasks"],
      project_id: "prj-btrdrl",
    });
  });

  it("shows an explicit red not-found message and disables Allow when the requested project does not resolve", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (String(url).startsWith("/api/projects")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              items: [{ id: "p1", name: "Alpha" }],
            }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ status: "ok" }),
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <ConsentActions
        requestId="req-nf"
        scopes={["project_tasks"]}
        requestedProjectId="prj-btrdrl"
      />,
    );

    await screen.findByLabelText(/Grant project access for/i);
    // Explicit not-found message naming the unresolved project id.
    const msg = screen.getByText(/Requested project prj-btrdrl not found/i);
    expect(msg).toBeInTheDocument();
    // The message is red (role="alert" + text-red-300).
    expect(msg).toHaveClass("text-red-300");
    expect(msg.closest('[role="alert"]')).toBeInTheDocument();
    // The select is marked invalid.
    const select = screen.getByLabelText(/Grant project access for/i);
    expect(select).toHaveAttribute("aria-invalid", "true");
    // Allow cannot be clicked until a resolved, valid target is chosen.
    expect(
      screen.getByRole("button", { name: /allow/i }),
    ).toBeDisabled();

    // Approving would be blocked even if clicked programmatically.
    fireEvent.click(screen.getByRole("button", { name: /allow/i }));
    await waitFor(() =>
      expect(fetchMock).not.toHaveBeenCalledWith(
        expect.stringContaining("/approve"),
        expect.anything(),
      ),
    );
  });

  it("renders Requested vs Granted scopes and highlights a dropped scope in red", () => {
    render(
      <ConsentActions
        requestId="req-diff"
        scopes={["memory_read", "memory_write"]}
        grantedScopes={["memory_read"]}
      />,
    );

    // Both lists are labelled.
    expect(screen.getByText("Requested")).toBeInTheDocument();
    expect(screen.getByText("Granted")).toBeInTheDocument();

    // The dropped scope is flagged.
    const droppedBadge = screen.getByText("memory_write");
    expect(droppedBadge).toHaveClass("text-red-200");
    expect(droppedBadge).toHaveAttribute(
      "aria-label",
      "memory_write (dropped from request)",
    );
    expect(droppedBadge).toHaveAttribute("data-state", "dropped");

    // A visible alert explains the narrowing.
    expect(screen.getByRole("alert")).toHaveTextContent(
      /Dropping 1 requested scope/i,
    );
  });

  it("renders Granted scopes highlighted yellow when a scope is added beyond the request", () => {
    render(
      <ConsentActions
        requestId="req-widen"
        scopes={["memory_read"]}
        grantedScopes={["memory_read", "files_read"]}
      />,
    );

    expect(screen.getByText("Granted")).toBeInTheDocument();
    const addedBadge = screen.getByText("files_read");
    expect(addedBadge).toHaveClass("text-amber-200");
    expect(addedBadge).toHaveAttribute(
      "aria-label",
      "files_read (granted beyond request)",
    );
    expect(addedBadge).toHaveAttribute("data-state", "added");
  });

  it("renders no scope-diff alert when requested equals granted", () => {
    const { container } = render(
      <ConsentActions
        requestId="req-same"
        scopes={["memory_read", "a2a_send"]}
      />,
    );
    // Default: granted == requested, so no diff alert.
    expect(screen.queryByRole("alert")).toBeNull();
    // No dropped or added badges.
    expect(container.querySelectorAll('[data-state="dropped"]')).toHaveLength(0);
    expect(container.querySelectorAll('[data-state="added"]')).toHaveLength(0);
  });

  const missingProjectScopes = [
    "project_notes",
    "files_read",
    "files_write",
    "project_lists",
    "project_tasks_create",
    "project_tasks_update",
  ];

  it.each(missingProjectScopes)(
    "renders the project picker for scope %s and sends project_id on approve",
    async (scope) => {
      const fetchMock = vi.fn((url: string) => {
        if (String(url).startsWith("/api/projects")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({ items: [{ id: "p1", name: "Alpha" }] }),
          });
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ status: "ok" }),
        });
      });
      vi.stubGlobal("fetch", fetchMock);
      const onResolved = vi.fn();
      render(
        <ConsentActions
          requestId="req-missing"
          scopes={[scope]}
          onResolved={onResolved}
        />,
      );

      await screen.findByLabelText(/Grant project access for/i);
      fireEvent.click(screen.getByRole("button", { name: /allow/i }));
      await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1));

      const approveCall = fetchMock.mock.calls.find((c) =>
        String(c[0]).includes("/approve"),
      );
      expect(approveCall).toBeTruthy();
      expect(JSON.parse((approveCall![1] as RequestInit).body as string)).toEqual({
        granted_scopes: [scope],
        project_id: "p1",
      });
    },
  );

  it("allows recovery when the requested project does not resolve", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (String(url).startsWith("/api/projects")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              items: [{ id: "p1", name: "Alpha" }],
            }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ status: "ok" }),
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <ConsentActions
        requestId="req-nf"
        scopes={["project_notes"]}
        requestedProjectId="prj-btrdrl"
      />,
    );

    await screen.findByLabelText(/Grant project access for/i);
    // Initially, Allow is disabled and the select is marked invalid.
    expect(screen.getByRole("button", { name: /allow/i })).toBeDisabled();
    const select = screen.getByLabelText(/Grant project access for/i);
    expect(select).toHaveAttribute("aria-invalid", "true");

    // Picking a valid project clears the not-found flag and re-enables Allow.
    fireEvent.change(select, { target: { value: "p1" } });
    expect(screen.getByRole("button", { name: /allow/i })).not.toBeDisabled();
    expect(select).not.toHaveAttribute("aria-invalid", "true");
  });
});

describe("computeScopeDiff", () => {
  it("detects dropped scopes (requested but not granted)", () => {
    expect(computeScopeDiff(["a", "b"], ["a"])).toEqual({
      dropped: ["b"],
      added: [],
    });
  });

  it("detects added scopes (granted but not requested)", () => {
    expect(computeScopeDiff(["a"], ["a", "b"])).toEqual({
      dropped: [],
      added: ["b"],
    });
  });

  it("reports no diff when sets match", () => {
    expect(computeScopeDiff(["a", "b"], ["a", "b"])).toEqual({
      dropped: [],
      added: [],
    });
  });

  it("reports all requested as dropped when granted is empty", () => {
    expect(computeScopeDiff(["a", "b"], [])).toEqual({
      dropped: ["a", "b"],
      added: [],
    });
  });
});

describe("consentPayload", () => {
  it("extracts request_id, scopes, and project_id from notification data", () => {
    expect(
      consentPayload({
        request_id: "req-1",
        requested_scopes: ["memory_read", "project_tasks"],
        project_id: "prj-btrdrl",
      }),
    ).toEqual({
      requestId: "req-1",
      scopes: ["memory_read", "project_tasks"],
      projectId: "prj-btrdrl",
    });
  });

  it("omits project_id when not present", () => {
    const payload = consentPayload({
      request_id: "req-2",
      requested_scopes: ["memory_read"],
    });
    expect(payload).toEqual({
      requestId: "req-2",
      scopes: ["memory_read"],
    });
    expect(payload?.projectId).toBeUndefined();
  });

  it("returns null when data is missing or malformed", () => {
    expect(consentPayload(undefined)).toBeNull();
    expect(consentPayload({})).toBeNull();
    expect(consentPayload({ request_id: 123 })).toBeNull();
    expect(consentPayload({ request_id: "x", requested_scopes: "not-a-list" })).toEqual({
      requestId: "x",
      scopes: [],
    });
  });
});
