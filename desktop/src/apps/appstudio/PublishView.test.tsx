import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { PublishView } from "./PublishView";
import { setBuildSession } from "./build-state";

const SESSION = {
  name: "Chore Tracker",
  appId: "chore-tracker-ab12",
  files: {
    "index.html": "<!doctype html><html><body>hi</body></html>",
  },
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setBuildSession(null);
});

function makeFetchMock(opts?: { installStatus?: number; installBody?: unknown; findings?: unknown[] }) {
  const installCalls: FormData[] = [];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();

    if (url === "/api/userspace-apps/analyze" && method === "POST") {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ findings: opts?.findings ?? [], blocked: (opts?.findings?.length ?? 0) > 0 }),
      } as Response);
    }
    if (url === "/api/userspace-apps/package" && method === "POST") {
      return Promise.resolve({
        ok: true,
        blob: () => Promise.resolve(new Blob(["zip-bytes"])),
        headers: new Headers({ "content-disposition": 'attachment; filename="chore-tracker-ab12.taosapp"' }),
      } as Response);
    }
    if (url === "/api/userspace-apps/install" && method === "POST") {
      installCalls.push(init?.body as FormData);
      const status = opts?.installStatus ?? 200;
      return Promise.resolve({
        ok: status < 400,
        status,
        json: () =>
          Promise.resolve(
            opts?.installBody ?? {
              app_id: "chore-tracker-ab12",
              permissions_requested: [],
              needs_consent: false,
              new_permissions: [],
            },
          ),
      } as Response);
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
  });
  return { fetchMock, installCalls };
}

describe("PublishView", () => {
  it("shows an empty state when no app has been built yet", () => {
    vi.stubGlobal("fetch", vi.fn() as unknown as typeof fetch);
    render(<PublishView />);
    expect(screen.getByText(/generate an app in the build view first/i)).toBeInTheDocument();
  });

  it("scans the built app's files and shows a clean state", async () => {
    setBuildSession(SESSION);
    const { fetchMock } = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<PublishView />);

    expect(screen.getAllByText("Chore Tracker").length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.getByTestId("security-findings-clean")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/userspace-apps/analyze",
      expect.objectContaining({ method: "POST" }),
    );
    expect(screen.getByRole("button", { name: /publish to my store/i })).not.toBeDisabled();
  });

  it("blocks publish and share when the scan finds a critical issue", async () => {
    setBuildSession(SESSION);
    const { fetchMock } = makeFetchMock({
      findings: [
        { severity: "critical", rule_id: "eval-like-execution", file: "index.html", line: 1, message: "eval()" },
      ],
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<PublishView />);

    await waitFor(() => expect(screen.getByTestId("security-findings-blocked")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /blocked by security scan/i })).toBeDisabled();
  });

  it("packages and installs the built app when Publish to my Store is clicked", async () => {
    setBuildSession(SESSION);
    const { fetchMock, installCalls } = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<PublishView />);
    await waitFor(() => expect(screen.getByTestId("security-findings-clean")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /publish to my store/i }));

    await waitFor(() => expect(installCalls.length).toBe(1));
    const form = installCalls[0]!;
    expect(form.get("package")).toBeInstanceOf(File);
    expect(form.get("provenance")).toBe("ai-generated");
    await waitFor(() => expect(screen.getByText(/Installed\. Find it in Launchpad/)).toBeInTheDocument());
  });

  it("packages and installs the built app when Share with family is clicked", async () => {
    setBuildSession(SESSION);
    const { fetchMock, installCalls } = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<PublishView />);
    await waitFor(() => expect(screen.getByTestId("security-findings-clean")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /share with family/i }));

    await waitFor(() => expect(installCalls.length).toBe(1));
    expect(installCalls[0]!.get("provenance")).toBe("ai-generated");
  });

  it("surfaces an install error instead of faking success", async () => {
    setBuildSession(SESSION);
    const { fetchMock } = makeFetchMock({ installStatus: 422, installBody: { error: "blocked_by_security_analysis" } });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    render(<PublishView />);
    await waitFor(() => expect(screen.getByTestId("security-findings-clean")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /publish to my store/i }));

    await waitFor(() => expect(screen.getByText("blocked_by_security_analysis")).toBeInTheDocument());
  });

  it("exports the built app as a downloadable .taosapp package", async () => {
    setBuildSession(SESSION);
    const { fetchMock } = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
    const clickSpy = vi.fn();
    const createObjectURLSpy = vi.fn(() => "blob:mock-url");
    vi.stubGlobal("URL", { ...URL, createObjectURL: createObjectURLSpy, revokeObjectURL: vi.fn() });
    const originalCreateElement = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = originalCreateElement(tag);
      if (tag === "a") el.click = clickSpy;
      return el;
    });

    render(<PublishView />);
    await waitFor(() => expect(screen.getByTestId("security-findings-clean")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /export package/i }));

    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    expect(createObjectURLSpy).toHaveBeenCalled();
    vi.restoreAllMocks();
  });
});
