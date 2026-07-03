import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PermissionConsent, GATED_CAPS } from "./PermissionConsent";

describe("PermissionConsent", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders as a labelled dialog listing each requested capability", () => {
    render(
      <PermissionConsent
        appId="todo"
        appName="Todo"
        requested={["app.net", "app.kv"]}
        onAllow={() => {}}
        onDeny={() => {}}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: /permissions for todo/i });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText(/connect to the internet/i)).toBeInTheDocument();
    expect(screen.getByText(/store and read its own app data/i)).toBeInTheDocument();
  });

  it("visually flags gated/sensitive capabilities but not free ones", () => {
    expect(GATED_CAPS.has("app.net")).toBe(true);
    expect(GATED_CAPS.has("app.kv")).toBe(false);

    render(
      <PermissionConsent
        appId="todo"
        appName="Todo"
        requested={["app.net", "app.kv"]}
        onAllow={() => {}}
        onDeny={() => {}}
      />,
    );
    const netRow = screen.getByText(/connect to the internet/i).closest("li")!;
    const kvRow = screen.getByText(/store and read its own app data/i).closest("li")!;
    expect(netRow.textContent).toContain("⚠");
    expect(kvRow.textContent).not.toContain("⚠");
  });

  it("Allow POSTs the granted permissions (merged with any already granted) and calls onAllow", async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", mockFetch);
    const onAllow = vi.fn();

    render(
      <PermissionConsent
        appId="todo"
        appName="Todo"
        requested={["app.net"]}
        alreadyGranted={["app.memory"]}
        onAllow={onAllow}
        onDeny={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /allow/i }));

    await waitFor(() => expect(onAllow).toHaveBeenCalled());
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/userspace-apps/todo/permissions",
      expect.objectContaining({ method: "POST" }),
    );
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(new Set(body.granted)).toEqual(new Set(["app.net", "app.memory"]));
    expect(onAllow).toHaveBeenCalledWith(expect.arrayContaining(["app.net", "app.memory"]));
  });

  it("Deny cancels without granting anything", () => {
    const mockFetch = vi.fn();
    vi.stubGlobal("fetch", mockFetch);
    const onDeny = vi.fn();

    render(
      <PermissionConsent
        appId="todo"
        appName="Todo"
        requested={["app.net"]}
        onAllow={() => {}}
        onDeny={onDeny}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /deny/i }));

    expect(onDeny).toHaveBeenCalledTimes(1);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("Escape key cancels", () => {
    const onDeny = vi.fn();
    render(
      <PermissionConsent
        appId="todo"
        appName="Todo"
        requested={["app.net"]}
        onAllow={() => {}}
        onDeny={onDeny}
      />,
    );
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onDeny).toHaveBeenCalledTimes(1);
  });
});
