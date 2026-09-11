import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { ShareView } from "./ShareView";

const SITE = {
  id: "share-site",
  title: "Share Site",
  content: '{"sections": []}',
  index_html: "<!doctype html><p>Hi</p>",
  created_at: 1,
  updated_at: 2,
};

function makeFetchMock(opts?: {
  installStatus?: number;
  installBody?: unknown;
  findings?: unknown[];
  accountStatus?: number;
  accountBody?: unknown;
  meshJoined?: boolean;
}) {
  const installCalls: FormData[] = [];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();

    if (url === "/api/account/me") {
      const status = opts?.accountStatus ?? 401;
      const body = opts?.accountBody ?? {};
      return Promise.resolve({
        ok: status < 400,
        status,
        json: () => Promise.resolve(body),
      } as Response);
    }
    if (url === "/api/account/mesh/status") {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ joined: opts?.meshJoined ?? false }),
      } as Response);
    }
    if (url === "/api/web/sites/share-site" && method === "GET") {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(SITE) } as Response);
    }
    if (url === "/api/userspace-apps/analyze" && method === "POST") {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ findings: opts?.findings ?? [], blocked: false }),
      } as Response);
    }
    if (url === "/api/web/sites/share-site/package" && method === "GET") {
      return Promise.resolve({ ok: true, blob: () => Promise.resolve(new Blob(["zip-bytes"])) } as Response);
    }
    if (url === "/api/userspace-apps/install" && method === "POST") {
      installCalls.push(init?.body as FormData);
      const status = opts?.installStatus ?? 200;
      return Promise.resolve({
        ok: status < 400,
        status,
        json: () =>
          Promise.resolve(
            opts?.installBody ?? { app_id: "share-site", permissions_requested: [], needs_consent: false, new_permissions: [] },
          ),
      } as Response);
    }
    if (url === "/api/web/sites/share-site/publish" && method === "POST") {
      const body = (init?.body ? JSON.parse(init.body as string) : {}) as { subdomain?: string; label?: string };
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ fqdn: `${body.subdomain}.taos.my` }),
      } as Response);
    }
    if (url === "/api/web/sites/share-site/publish" && method === "DELETE") {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
  });
  return { fetchMock, installCalls };
}

describe("ShareView", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows an empty state with no active site", () => {
    const emptyFetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/account/me") {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      if (url === "/api/account/mesh/status") {
        return Promise.resolve(new Response(JSON.stringify({ joined: false }), { headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response(null, { status: 404 }));
    });
    vi.stubGlobal("fetch", emptyFetch as unknown as typeof fetch);
    render(<ShareView siteId={null} provenance="user-uploaded" />);
    expect(screen.getByText(/Save a site in the Edit view first/)).toBeDefined();
  });

  it("builds the site's .taosapp package and installs it via the existing userspace-apps endpoint, tagged with the provenance it was given", async () => {
    const { fetchMock, installCalls } = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<ShareView siteId="share-site" provenance="ai-generated" />);
    await waitFor(() => expect(screen.getByRole("button", { name: /Install on this taOS/ })).toBeDefined());
    await waitFor(() => expect(screen.getByText(/No security issues found/)).toBeDefined());

    fireEvent.click(screen.getByRole("button", { name: /Install on this taOS/ }));

    await waitFor(() => expect(installCalls.length).toBe(1));
    const form = installCalls[0]!;
    expect(form.get("package")).toBeInstanceOf(File);
    expect((form.get("package") as File).name).toBe("share-site.taosapp");
    expect(form.get("provenance")).toBe("ai-generated");

    await waitFor(() => expect(screen.getByText(/Installed\. Find it in Launchpad/)).toBeDefined());
  });

  it("tags a hand-built site as user-uploaded, not ai-generated", async () => {
    const { fetchMock, installCalls } = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<ShareView siteId="share-site" provenance="user-uploaded" />);
    await waitFor(() => expect(screen.getByText(/No security issues found/)).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: /Install on this taOS/ }));

    await waitFor(() => expect(installCalls.length).toBe(1));
    expect(installCalls[0]!.get("provenance")).toBe("user-uploaded");
  });

  it("surfaces an install error instead of faking success", async () => {
    const { fetchMock } = makeFetchMock({ installStatus: 422, installBody: { error: "blocked_by_security_analysis" } });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<ShareView siteId="share-site" provenance="ai-generated" />);
    await waitFor(() => expect(screen.getByText(/No security issues found/)).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: /Install on this taOS/ }));

    await waitFor(() => expect(screen.getByText("blocked_by_security_analysis")).toBeDefined());
  });

  it("blocks install (but not export) when the analyzer finds a critical issue", async () => {
    // Mock analyzer finding used only as inert fixture text (never executed)
    // to exercise the UI's block-on-critical path.
    const { fetchMock, installCalls } = makeFetchMock({
      findings: [{ severity: "critical", rule_id: "eval-use", file: "index.html", line: 3, message: "uses eval()" }],
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<ShareView siteId="share-site" provenance="ai-generated" />);
    const installButton = await screen.findByRole("button", { name: /Blocked by security scan/ });
    expect(installButton).toBeDisabled();

    fireEvent.click(installButton);
    expect(installCalls.length).toBe(0);
  });

  it("prompts to save first when the site has no rendered index_html yet", async () => {
    const unsavedFetch = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/web/sites/draft-site") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ...SITE, id: "draft-site", index_html: "" }) } as Response);
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
    });
    vi.stubGlobal("fetch", unsavedFetch as unknown as typeof fetch);

    render(<ShareView siteId="draft-site" provenance="user-uploaded" />);
    await waitFor(() => expect(screen.getByText(/hasn't been saved with rendered content/)).toBeDefined());
    expect(screen.queryByRole("button", { name: /Install on this taOS/ })).not.toBeInTheDocument();
  });

  it("shows a sign-in prompt when the user is not authenticated", async () => {
    const { fetchMock } = makeFetchMock({ accountStatus: 401 });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<ShareView siteId="share-site" provenance="user-uploaded" />);
    await waitFor(() => expect(screen.getByText(/Sign in to your taOS account to publish/)).toBeDefined());
  });

  it("shows a taOSgo prompt when the account is signed in but not subscribed", async () => {
    const { fetchMock } = makeFetchMock({
      accountStatus: 200,
      accountBody: { user_id: "u1", email: "jay@example.com", taosgo: { status: "none" } },
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<ShareView siteId="share-site" provenance="user-uploaded" />);
    await waitFor(() => expect(screen.getByText(/taOSgo subscription required to publish/)).toBeDefined());
  });

  it("shows a no-subdomains prompt with a Settings link when subscribed but no claims yet", async () => {
    const { fetchMock } = makeFetchMock({
      accountStatus: 200,
      accountBody: { user_id: "u1", email: "jay@example.com", taosgo: { status: "active" }, subdomains: [] },
      meshJoined: true,
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<ShareView siteId="share-site" provenance="user-uploaded" />);
    await waitFor(() => expect(screen.getByText(/No claimed subdomains/)).toBeDefined());
    expect(screen.getByText(/Claim one in Settings to publish/)).toBeDefined();
  });

  it("shows a mesh-join prompt when subscribed with claims but the host is not on the mesh", async () => {
    const { fetchMock } = makeFetchMock({
      accountStatus: 200,
      accountBody: {
        user_id: "u1",
        email: "jay@example.com",
        taosgo: { status: "active" },
        subdomains: [{ id: "c1", account_id: "u1", name: "mybiz", status: "active" }],
      },
      meshJoined: false,
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<ShareView siteId="share-site" provenance="user-uploaded" />);
    await waitFor(() => expect(screen.getByText(/Connect your taOS account to the mesh first/)).toBeDefined());
  });

  it("renders the subdomain picker and sends the chosen subdomain on publish", async () => {
    const { fetchMock } = makeFetchMock({
      accountStatus: 200,
      accountBody: {
        user_id: "u1",
        email: "jay@example.com",
        taosgo: { status: "active" },
        subdomains: [
          { id: "c1", account_id: "u1", name: "mybiz", status: "active" },
          { id: "c2", account_id: "u1", name: "personal", status: "active" },
        ],
      },
      meshJoined: true,
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<ShareView siteId="share-site" provenance="user-uploaded" />);
    const select = await screen.findByRole("combobox");
    expect(select).toBeDefined();

    fireEvent.change(select, { target: { value: "personal" } });
    fireEvent.click(screen.getByRole("button", { name: /Publish/ }));

    await waitFor(() => expect(screen.getByText("Published to personal.taos.my")).toBeDefined());
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/web/sites/share-site/publish",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ subdomain: "personal" }),
      }),
    );
  });

  it("sends an optional label along with the subdomain on publish", async () => {
    const { fetchMock } = makeFetchMock({
      accountStatus: 200,
      accountBody: {
        user_id: "u1",
        email: "jay@example.com",
        taosgo: { status: "active" },
        subdomains: [{ id: "c1", account_id: "u1", name: "mybiz", status: "active" }],
      },
      meshJoined: true,
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<ShareView siteId="share-site" provenance="user-uploaded" />);
    const select = await screen.findByRole("combobox");
    fireEvent.change(select, { target: { value: "mybiz" } });

    const labelInput = screen.getByPlaceholderText("Label (optional)");
    fireEvent.change(labelInput, { target: { value: "blog" } });
    fireEvent.click(screen.getByRole("button", { name: /Publish/ }));

    await waitFor(() => expect(screen.getByText("Published to mybiz.taos.my")).toBeDefined());
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/web/sites/share-site/publish",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ subdomain: "mybiz", label: "blog" }),
      }),
    );
  });

  it("shows an unpublish button and calls DELETE when clicked", async () => {
    const { fetchMock } = makeFetchMock({
      accountStatus: 200,
      accountBody: {
        user_id: "u1",
        email: "jay@example.com",
        taosgo: { status: "active" },
        subdomains: [{ id: "c1", account_id: "u1", name: "mybiz", status: "active" }],
      },
      meshJoined: true,
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<ShareView siteId="share-site" provenance="user-uploaded" />);
    const select = await screen.findByRole("combobox");
    fireEvent.change(select, { target: { value: "mybiz" } });
    fireEvent.click(screen.getByRole("button", { name: /Publish/ }));

    await waitFor(() => expect(screen.getByText("Published to mybiz.taos.my")).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: /Unpublish/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/web/sites/share-site/publish",
      expect.objectContaining({ method: "DELETE" }),
    ));
  });

  it("surfaces a publish error instead of faking success", async () => {
    const errorMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/account/me") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ user_id: "u1", email: "jay@example.com", taosgo: { status: "active" }, subdomains: [{ id: "c1", account_id: "u1", name: "mybiz", status: "active" }] }) } as Response);
      }
      if (url === "/api/account/mesh/status") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ joined: true }) } as Response);
      }
      if (url === "/api/web/sites/share-site" && (init?.method ?? "GET").toUpperCase() === "GET") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(SITE) } as Response);
      }
      if (url === "/api/userspace-apps/analyze" && (init?.method ?? "GET").toUpperCase() === "POST") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ findings: [], blocked: false }) } as Response);
      }
      if (url === "/api/web/sites/share-site/publish" && (init?.method ?? "GET").toUpperCase() === "POST") {
        return Promise.resolve({ ok: false, status: 403, json: () => Promise.resolve({ error: "subdomain_not_active" }) } as Response);
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
    });
    vi.stubGlobal("fetch", errorMock as unknown as typeof fetch);

    render(<ShareView siteId="share-site" provenance="user-uploaded" />);
    const select = await screen.findByRole("combobox");
    fireEvent.change(select, { target: { value: "mybiz" } });
    fireEvent.click(screen.getByRole("button", { name: /Publish/ }));

    await waitFor(() => expect(screen.getByText("subdomain_not_active")).toBeDefined());
  });
});
