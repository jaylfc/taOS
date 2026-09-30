import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LockScreenPanel, SWIPE_WARNING } from "./LockScreenPanel";

function json(obj: unknown, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const PIN_USER = { unlock_method: "pin", has_pin: true, swipe_available: true };
const NO_PIN_USER = { unlock_method: "password", has_pin: false, swipe_available: true };

function mockFetch(initial: unknown, handler?: (url: string, init?: RequestInit) => Response) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    calls.push({ url, init });
    if (!init?.method || init.method === "GET") return Promise.resolve(json(initial));
    return Promise.resolve(handler ? handler(url, init) : json({ ok: true }));
  });
  return calls;
}

describe("LockScreenPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the current method checked and pattern disabled as coming soon", async () => {
    mockFetch(PIN_USER);
    render(<LockScreenPanel />);
    const pin = await screen.findByLabelText("PIN");
    expect(pin).toBeChecked();
    expect(screen.getByLabelText("Pattern")).toBeDisabled();
    expect(screen.getByText("Coming soon")).toBeInTheDocument();
    // Nothing to confirm until the choice changes.
    expect(screen.queryByLabelText("Current password")).not.toBeInTheDocument();
  });

  it("warns before choosing swipe and sends the current password", async () => {
    const calls = mockFetch(PIN_USER, () =>
      json({ ok: true, unlock_method: "swipe", has_pin: true, swipe_available: true }),
    );
    render(<LockScreenPanel />);
    fireEvent.click(await screen.findByLabelText("Swipe"));
    expect(screen.getByText(SWIPE_WARNING)).toBeInTheDocument();
    expect(SWIPE_WARNING).toBe("Anyone holding this device can open taOS.");

    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "hunter22 pw" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(screen.getByText("Lock screen updated.")).toBeInTheDocument());

    const put = calls.find((c) => c.init?.method === "PUT")!;
    expect(put.url).toBe("/api/settings/lock");
    expect(JSON.parse(String(put.init!.body))).toEqual({
      unlock_method: "swipe",
      current_password: "hunter22 pw",
    });
  });

  it("refuses to submit without the current password", async () => {
    const calls = mockFetch(PIN_USER);
    render(<LockScreenPanel />);
    fireEvent.click(await screen.findByLabelText("Password"));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("current password");
    expect(calls.some((c) => c.init?.method === "PUT")).toBe(false);
  });

  it("shows the server's refusal of a wrong password", async () => {
    mockFetch(PIN_USER, () => json({ error: "incorrect password" }, 403));
    render(<LockScreenPanel />);
    fireEvent.click(await screen.findByLabelText("Swipe"));
    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("incorrect password");
    expect(screen.getByLabelText("PIN")).not.toBeChecked();
  });

  it("choosing PIN with no PIN set collects one first, then switches", async () => {
    const calls = mockFetch(NO_PIN_USER, (url) =>
      url === "/auth/pin"
        ? json({ ok: true, has_pin: true })
        : json({ ok: true, unlock_method: "pin", has_pin: true, swipe_available: true }),
    );
    render(<LockScreenPanel />);
    fireEvent.click(await screen.findByLabelText("PIN"));
    fireEvent.change(screen.getByLabelText("New PIN"), { target: { value: "4913" } });
    fireEvent.change(screen.getByLabelText("Confirm PIN"), { target: { value: "4913" } });
    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "pw pw pw pw" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(screen.getByText("Lock screen updated.")).toBeInTheDocument());

    const writes = calls.filter((c) => c.init?.method && c.init.method !== "GET");
    expect(writes.map((c) => c.url)).toEqual(["/auth/pin", "/api/settings/lock"]);
    expect(JSON.parse(String(writes[0].init!.body))).toEqual({ pin: "4913", password: "pw pw pw pw" });
  });

  it("does not show the PIN fields when a PIN already exists", async () => {
    mockFetch({ ...PIN_USER, unlock_method: "password" });
    render(<LockScreenPanel />);
    fireEvent.click(await screen.findByLabelText("PIN"));
    expect(screen.queryByLabelText("New PIN")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Current password")).toBeInTheDocument();
  });

  it("disables swipe on a multi-account device", async () => {
    mockFetch({ ...PIN_USER, swipe_available: false });
    render(<LockScreenPanel />);
    expect(await screen.findByLabelText("Swipe")).toBeDisabled();
  });
});
