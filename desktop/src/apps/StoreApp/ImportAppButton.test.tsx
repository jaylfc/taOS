import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ImportAppButton } from "./ImportAppButton";

function mockFetchSequence(responses: Array<{ url: string; body: unknown; ok?: boolean }>) {
  return vi.fn((url: string) => {
    // Exact match first so a specific per-id URL (e.g. "/api/userspace-apps/todo")
    // isn't shadowed by a broader substring match (e.g. the "/api/userspace-apps"
    // list endpoint, which is itself a substring of the per-id one).
    const match =
      responses.find((r) => url === r.url) ?? responses.find((r) => url.includes(r.url));
    if (!match) throw new Error(`unexpected fetch: ${url}`);
    return Promise.resolve({ ok: match.ok ?? true, json: async () => match.body });
  });
}

describe("ImportAppButton", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("installing a package requesting a gated capability shows the consent dialog", async () => {
    const mockFetch = mockFetchSequence([
      {
        url: "/api/userspace-apps/install",
        body: { app_id: "todo", permissions_requested: ["app.net", "app.kv"], needs_consent: true, new_permissions: ["app.net"] },
      },
      {
        url: "/api/userspace-apps/todo",
        body: { app_id: "todo", name: "Todo", icon: "", app_type: "web", version: "1", enabled: 1, permissions_requested: ["app.net", "app.kv"], permissions_granted: [] },
      },
    ]);
    vi.stubGlobal("fetch", mockFetch);

    render(<ImportAppButton />);
    const file = new File(["data"], "todo.taosapp");
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    const dialog = await screen.findByRole("dialog", { name: /permissions for todo/i });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText(/connect to the internet/i)).toBeInTheDocument();
  });

  it("installing a package with no new permissions never shows the dialog and skips the row fetch", async () => {
    const mockFetch = mockFetchSequence([
      {
        url: "/api/userspace-apps/install",
        body: { app_id: "todo", permissions_requested: ["app.kv"], needs_consent: false, new_permissions: [] },
      },
      { url: "/api/userspace-apps/todo", body: { app_id: "todo" } },
    ]);
    vi.stubGlobal("fetch", mockFetch);

    render(<ImportAppButton />);
    const file = new File(["data"], "todo.taosapp");
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    expect(screen.queryByRole("dialog")).toBeNull();
    // Only the install call should happen -- no reason to fetch the row when
    // the install didn't need consent.
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/userspace-apps/install",
      expect.anything(),
    );
  });

  it("Deny in the dialog dismisses it without granting", async () => {
    const mockFetch = mockFetchSequence([
      {
        url: "/api/userspace-apps/install",
        body: { app_id: "todo", permissions_requested: ["app.net"], needs_consent: true, new_permissions: ["app.net"] },
      },
      {
        url: "/api/userspace-apps/todo",
        body: { app_id: "todo", name: "Todo", icon: "", app_type: "web", version: "1", enabled: 1, permissions_requested: ["app.net"], permissions_granted: [] },
      },
    ]);
    vi.stubGlobal("fetch", mockFetch);

    render(<ImportAppButton />);
    const file = new File(["data"], "todo.taosapp");
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await screen.findByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: /deny/i }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mockFetch).not.toHaveBeenCalledWith(expect.stringContaining("/permissions"), expect.anything());
  });
});
