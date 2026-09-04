import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ConsentActions, computeScopeDiff, consentPayload } from "./ConsentActions";

/** The server's project-scope vocabulary, as GET /api/agents/scope-vocabulary
 *  reports it. Tests serve this rather than relying on any list baked into the
 *  component -- the component must not have one. */
const SERVER_PROJECT_SCOPES = [
  "canvas_read",
  "canvas_write",
  "files_read",
  "files_write",
  "project_lists",
  "project_notes",
  "project_tasks",
  "project_tasks_create",
  "project_tasks_update",
];

function okJson(body: unknown, status = 200) {
  return Promise.resolve({ ok: true, status, json: () => Promise.resolve(body) });
}

/** Answer the scope-vocabulary request, or null when `url` is something else.
 *  Every fetch mock in this file delegates to it first so the component under
 *  test always has a server to read the vocabulary from. */
function vocabHit(url: unknown, projectScopes: string[] = SERVER_PROJECT_SCOPES) {
  if (!String(url).startsWith("/api/agents/scope-vocabulary")) return null;
  return okJson({
    valid_scopes: [...projectScopes, "memory_read", "memory_write", "a2a_send"].sort(),
    project_scopes: projectScopes,
  });
}

/** Resolve once the project list has landed: the picker shows "Loading..." as
 *  its placeholder option until then, and asserting on the selection or on
 *  Allow before that is a race. */
async function projectListLoaded() {
  await waitFor(() =>
    expect(screen.queryByRole("option", { name: /Loading/i })).toBeNull(),
  );
}

function okFetch() {
  return vi.fn((url: string) => vocabHit(url) ?? okJson({ status: "ok" }));
}

describe("ConsentActions", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    // Baseline: a server that publishes the scope vocabulary. Tests needing
    // other endpoints stub their own fetch over this one.
    vi.stubGlobal("fetch", okFetch());
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

    // Allow stays disabled until the server's scope vocabulary is in hand.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /allow/i })).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: /allow/i }));

    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agents/auth-requests/req-1/approve",
      expect.objectContaining({ method: "POST" }),
    );
    const approve = fetchMock.mock.calls.find((c) => String(c[0]).includes("/approve"));
    const init = approve![1] as RequestInit;
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
    const fetchMock = vi.fn((url: string) => {
      const vocab = vocabHit(url);
      if (vocab) return vocab;
      return Promise.resolve({
        ok: false,
        status: 403,
        json: () => Promise.resolve({ detail: "forbidden" }),
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const onResolved = vi.fn();
    render(<ConsentActions requestId="req-3" scopes={[]} onResolved={onResolved} />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /allow/i })).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: /allow/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("forbidden"));
    expect(onResolved).not.toHaveBeenCalled();
  });

  it("shows a project picker for a project_tasks request and sends the chosen project_id", async () => {
    const fetchMock = vi.fn((url: string) => {
      const vocab = vocabHit(url);
      if (vocab) return vocab;
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
    await projectListLoaded();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /allow/i })).not.toBeDisabled(),
    );

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
      const vocab = vocabHit(url);
      if (vocab) return vocab;
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
    await projectListLoaded();
    // With two projects none is auto-selected, so Allow is still gated.
    expect(screen.getByRole("option", { name: "Bravo" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /allow/i })).toBeDisabled();

    fireEvent.change(select, { target: { value: "p2" } });
    expect(screen.getByRole("button", { name: /allow/i })).not.toBeDisabled();
  });

  it("creates a project inline and grants project_tasks against the new project", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const vocab = vocabHit(url);
      if (vocab) return vocab;
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
    await projectListLoaded();
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
      const vocab = vocabHit(url);
      if (vocab) return vocab;
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
    await projectListLoaded();
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
      const vocab = vocabHit(url);
      if (vocab) return vocab;
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
    await projectListLoaded();
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
      const vocab = vocabHit(url);
      if (vocab) return vocab;
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
      await projectListLoaded();
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /allow/i })).not.toBeDisabled(),
      );
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
      const vocab = vocabHit(url);
      if (vocab) return vocab;
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
    await projectListLoaded();
    // Initially, Allow is disabled and the select is marked invalid.
    expect(screen.getByRole("button", { name: /allow/i })).toBeDisabled();
    const select = screen.getByLabelText(/Grant project access for/i);
    expect(select).toHaveAttribute("aria-invalid", "true");

    // Picking a valid project clears the not-found flag and re-enables Allow.
    fireEvent.change(select, { target: { value: "p1" } });
    expect(screen.getByRole("button", { name: /allow/i })).not.toBeDisabled();
    expect(select).not.toHaveAttribute("aria-invalid", "true");
  });

  // ---------------------------------------------------------------------
  // The picker's gate is the SERVER's vocabulary, not a list kept here.
  //
  // Mirroring the server list in this component is what made Approve 400 with
  // no remedy: the copy fell behind, the picker never rendered, and approve was
  // POSTed without the project_id the server demands. Re-listing today's nine
  // scopes only postpones that -- the tenth breaks it again. These tests pin
  // the property that actually prevents it: the component must render from
  // whatever the server says, including scopes it has never heard of, and must
  // not render for scopes the server does not classify as project-bound.
  // ---------------------------------------------------------------------

  function projectsAnd(projectScopes: string[]) {
    return vi.fn((url: string) => {
      const vocab = vocabHit(url, projectScopes);
      if (vocab) return vocab;
      if (String(url).startsWith("/api/projects")) {
        return okJson({ items: [{ id: "p1", name: "Alpha" }] });
      }
      return okJson({ status: "ok" });
    });
  }

  it("renders the project picker for a project scope the client has never heard of", async () => {
    // A scope added to the server's _PROJECT_SCOPES after this build shipped.
    const fetchMock = projectsAnd([...SERVER_PROJECT_SCOPES, "project_ledger_write"]);
    vi.stubGlobal("fetch", fetchMock);
    const onResolved = vi.fn();
    render(
      <ConsentActions
        requestId="req-future"
        scopes={["project_ledger_write"]}
        onResolved={onResolved}
      />,
    );

    await screen.findByLabelText(/Grant project access for/i);
    await projectListLoaded();
    // The picker renders before the project list lands; Allow unlocks only once
    // a project is actually selected.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /allow/i })).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: /allow/i }));
    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1));

    const approve = fetchMock.mock.calls.find((c) => String(c[0]).includes("/approve"));
    expect(JSON.parse((approve![1] as RequestInit).body as string)).toEqual({
      granted_scopes: ["project_ledger_write"],
      project_id: "p1",
    });
  });

  it("renders the project picker for files_write and sends the chosen project_id", async () => {
    const fetchMock = projectsAnd(SERVER_PROJECT_SCOPES);
    vi.stubGlobal("fetch", fetchMock);
    const onResolved = vi.fn();
    render(
      <ConsentActions
        requestId="req-fw"
        scopes={["files_read", "files_write"]}
        onResolved={onResolved}
      />,
    );

    await screen.findByLabelText(/Grant project access for/i);
    await projectListLoaded();
    // The picker renders before the project list lands; Allow unlocks only once
    // a project is actually selected.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /allow/i })).not.toBeDisabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: /allow/i }));
    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1));

    const approve = fetchMock.mock.calls.find((c) => String(c[0]).includes("/approve"));
    expect(JSON.parse((approve![1] as RequestInit).body as string)).toEqual({
      granted_scopes: ["files_read", "files_write"],
      project_id: "p1",
    });
  });

  it("does not render the picker for a scope the server no longer treats as project-bound", async () => {
    // The refusing direction: a scope this component used to hardcode, which
    // the server has since dropped from _PROJECT_SCOPES. A local copy would
    // still demand a project and block Allow on a grant that needs none.
    const fetchMock = projectsAnd(
      SERVER_PROJECT_SCOPES.filter((s) => s !== "canvas_write"),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onResolved = vi.fn();
    render(
      <ConsentActions requestId="req-dropped" scopes={["canvas_write"]} onResolved={onResolved} />,
    );

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /allow/i })).not.toBeDisabled(),
    );
    expect(screen.queryByLabelText(/Grant project access for/i)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /allow/i }));
    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1));

    const approve = fetchMock.mock.calls.find((c) => String(c[0]).includes("/approve"));
    expect(JSON.parse((approve![1] as RequestInit).body as string)).toEqual({
      granted_scopes: ["canvas_write"],
    });
  });

  it("treats a vocabulary array with a non-string entry as a failure, not a shorter list", async () => {
    // Filtering the bad entry out would yield a list that still looks valid
    // while missing a project scope -- the picker-never-renders bug again, now
    // with no error to point at.
    const fetchMock = vi.fn((url: string) => {
      if (String(url).startsWith("/api/agents/scope-vocabulary")) {
        return okJson({ project_scopes: ["files_write", null, "project_tasks"] });
      }
      return okJson({ status: "ok" });
    });
    vi.stubGlobal("fetch", fetchMock);
    const onResolved = vi.fn();
    render(
      <ConsentActions requestId="req-bad-vocab" scopes={["files_write"]} onResolved={onResolved} />,
    );

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/malformed/i),
    );
    expect(screen.getByRole("button", { name: /allow/i })).toBeDisabled();
    expect(screen.queryByLabelText(/Grant project access for/i)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /allow/i }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/approve"))).toBe(false),
    );
    expect(onResolved).not.toHaveBeenCalled();
  });

  it("times the vocabulary request out rather than leaving Allow disabled with no reason", async () => {
    vi.useFakeTimers();
    try {
      // A request that never settles unless aborted -- the hang case.
      const fetchMock = vi.fn((url: string, init?: RequestInit) => {
        if (String(url).startsWith("/api/agents/scope-vocabulary")) {
          return new Promise((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError")),
            );
          });
        }
        return okJson({ status: "ok" });
      });
      vi.stubGlobal("fetch", fetchMock);
      render(<ConsentActions requestId="req-hang" scopes={["files_write"]} />);

      // Before the deadline: disabled, but nothing is claimed either way.
      expect(screen.getByRole("button", { name: /allow/i })).toBeDisabled();
      expect(screen.queryByRole("alert")).toBeNull();

      await act(async () => {
        vi.advanceTimersByTime(10_000);
      });

      expect(screen.getByRole("alert")).toHaveTextContent(/no answer within 10s/i);
      expect(screen.getByRole("button", { name: /allow/i })).toBeDisabled();
      // The signal was actually passed, so the socket is released too.
      const vocabCall = fetchMock.mock.calls.find((c) =>
        String(c[0]).startsWith("/api/agents/scope-vocabulary"),
      );
      expect((vocabCall![1] as RequestInit).signal).toBeInstanceOf(AbortSignal);
    } finally {
      vi.useRealTimers();
    }
  });

  it("blocks Allow and says so when the scope vocabulary cannot be loaded", async () => {
    // Fail closed. Falling back to a built-in list here is exactly the state
    // this card removes, and a silent fallback would be indistinguishable from
    // a correct answer.
    const fetchMock = vi.fn((url: string) => {
      if (String(url).startsWith("/api/agents/scope-vocabulary")) {
        return Promise.resolve({
          ok: false,
          status: 503,
          json: () => Promise.resolve({}),
        });
      }
      return okJson({ status: "ok" });
    });
    vi.stubGlobal("fetch", fetchMock);
    const onResolved = vi.fn();
    render(
      <ConsentActions requestId="req-vocab-down" scopes={["files_write"]} onResolved={onResolved} />,
    );

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        /Could not confirm which scopes need a project \(the server answered 503\)/i,
      ),
    );
    expect(screen.getByRole("button", { name: /allow/i })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /allow/i }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/approve"))).toBe(false),
    );
    expect(onResolved).not.toHaveBeenCalled();
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
