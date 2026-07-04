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
}) {
  const installCalls: FormData[] = [];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();

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
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
  });
  return { fetchMock, installCalls };
}

describe("ShareView", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows an empty state with no active site", () => {
    vi.stubGlobal("fetch", vi.fn() as unknown as typeof fetch);
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
});
