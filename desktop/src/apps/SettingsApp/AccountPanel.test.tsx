import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AccountSection } from "./AccountPanel";

describe("AccountSection", () => {
  afterEach(() => { vi.restoreAllMocks(); });
  beforeEach(() => { vi.restoreAllMocks(); });

  it("shows the sign-in / create-account form when signed out (401)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 401 }),
    );
    render(<AccountSection />);
    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByText("Create account")).toBeInTheDocument();
  });

  it("shows an unavailable state when the account service cannot be reached", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network"));
    render(<AccountSection />);
    expect(
      await screen.findByText(/account service is not reachable/i),
    ).toBeInTheDocument();
    expect(screen.getByText("Retry")).toBeInTheDocument();
  });

  it("shows the signed-in view with email + taOSgo status", async () => {
    // URL-aware with a fresh Response per call: a shared Response instance has a
    // single-use body, which the local /auth/status probe would consume before
    // the cloud fetch reads it.
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (String(input).includes("/auth/status")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            user_id: "u1",
            email: "jay@example.com",
            taosgo: { status: "none" },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    });
    render(<AccountSection />);
    expect(await screen.findByText("jay@example.com")).toBeInTheDocument();
    expect(screen.getByText("taOSgo")).toBeInTheDocument();
    expect(screen.getByText("Not subscribed")).toBeInTheDocument();
    expect(screen.getByText("Start 7-day free trial")).toBeInTheDocument();
  });

  it("surfaces a backend error message on failed sign-in", async () => {
    // URL-aware so the local /auth/status probe does not consume the cloud
    // account's mock sequence (initial signed-out, then a failed sign-in).
    let accountCalls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (String(input).includes("/auth/status")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      accountCalls += 1;
      if (accountCalls === 1) return Promise.resolve(new Response(null, { status: 401 }));
      return Promise.resolve(
        new Response(JSON.stringify({ error: "Invalid credentials" }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }),
      );
    });
    render(<AccountSection />);
    fireEvent.change(await screen.findByLabelText("Email"), {
      target: { value: "jay@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "password1" },
    });
    // Two controls read "Sign in" (the mode tab and the submit button); the
    // submit is the last one.
    fireEvent.click(screen.getAllByRole("button", { name: "Sign in" }).at(-1)!);
    await waitFor(() =>
      expect(screen.getByText("Invalid credentials")).toBeInTheDocument(),
    );
  });

  it("shows the free-username copy and CTA when no username is claimed", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (String(input).includes("/auth/status")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            user_id: "u1",
            email: "jay@example.com",
            taosgo: { status: "none" },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    });
    render(<AccountSection />);
    expect(await screen.findByText("Claim your free taOS username")).toBeInTheDocument();
    expect(
      screen.getByText(/Your username is your free identity on taOS/i),
    ).toBeInTheDocument();
    expect(screen.getByText("Claim your username")).toBeInTheDocument();
  });

  it("displays the claimed username with free-identity copy (no .taos.my suffix)", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (String(input).includes("/auth/status")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            user_id: "u1",
            email: "jay@example.com",
            taosgo: { status: "none" },
            username: "jay",
            subdomains: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    });
    render(<AccountSection />);
    expect(await screen.findByText("@jay")).toBeInTheDocument();
    expect(
      screen.getByText(/People use @username to find you/i),
    ).toBeInTheDocument();
    expect(screen.queryByText("jay.taos.my")).not.toBeInTheDocument();
  });

  it("renders the claimed subdomain list with active and grace badges", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (String(input).includes("/auth/status")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            user_id: "u1",
            email: "jay@example.com",
            taosgo: { status: "active" },
            username: "jay",
            subdomains: [
              { id: "s1", account_id: "u1", name: "mybiz", status: "active" },
              { id: "s2", account_id: "u1", name: "old", status: "grace" },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    });
    render(<AccountSection />);
    expect(await screen.findByText("mybiz.taos.my")).toBeInTheDocument();
    expect(screen.getByText("old.taos.my")).toBeInTheDocument();
    expect(screen.getAllByText("Active").length).toBeGreaterThan(0);
    expect(screen.getByText("Grace")).toBeInTheDocument();
  });

  it("disables the claim UI when not subscribed", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (String(input).includes("/auth/status")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            user_id: "u1",
            email: "jay@example.com",
            taosgo: { status: "none" },
            username: "jay",
            subdomains: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    });
    render(<AccountSection />);
    const input = await screen.findByLabelText("Subdomain name");
    expect(input).toBeDisabled();
    expect(screen.getByText(/Claiming a subdomain is part of taOSgo/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Claim" })).toBeDisabled();
  });

  it("shows inline availability and claims a subdomain", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/status")) {
        return Promise.resolve(new Response(null, { status: 401 }));
      }
      if (url.includes("/subdomains/check")) {
        return Promise.resolve(
          new Response(JSON.stringify({ available: true }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      if (url.includes("/subdomains/claim")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              id: "s9",
              account_id: "u1",
              name: "mybiz",
              status: "active",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            user_id: "u1",
            email: "jay@example.com",
            taosgo: { status: "active" },
            username: "jay",
            subdomains: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    });
    render(<AccountSection />);
    const input = await screen.findByLabelText("Subdomain name");
    fireEvent.change(input, { target: { value: "mybiz" } });
    expect(await screen.findByText("Available")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Claim" }));
    expect(await screen.findByText("mybiz.taos.my")).toBeInTheDocument();
  });

  it("shows the local 'this device' identity from /auth/status", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (String(input).includes("/auth/status")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              configured: true,
              authenticated: true,
              user: { username: "jay", full_name: "Jay", email: "jay@example.com", is_admin: true },
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      // Cloud account unreachable, mirroring the real screenshot.
      return Promise.reject(new Error("network"));
    });
    render(<AccountSection />);
    // Local identity is shown even though the cloud account is unreachable.
    expect(await screen.findByText("Jay")).toBeInTheDocument();
    expect(screen.getByText(/@jay/)).toBeInTheDocument();
    expect(screen.getByText("Signed in on this device")).toBeInTheDocument();
    expect(screen.getByText(/account service is not reachable/i)).toBeInTheDocument();
  });
});

/** The PIN card is the Settings half of Jay's "choose your sign-in method"
 *  ask; the install wizard is the other half. These prove the card reflects
 *  the server's state and cannot mint a PIN without the account password. */
describe("PIN sign-in card", () => {
  afterEach(() => { vi.restoreAllMocks(); });

  /** /auth/status answers `authenticated` with the given has_pin; every other
   *  URL answers 401 so the cloud card stays out of the way. */
  const withPin = (hasPin: boolean, onPinCall?: (init?: RequestInit) => Response) =>
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/auth/pin") && onPinCall) {
        return Promise.resolve(onPinCall(init));
      }
      if (url.includes("/auth/status")) {
        return Promise.resolve(new Response(
          JSON.stringify({ authenticated: true, user: { username: "tester", has_pin: hasPin } }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ));
      }
      return Promise.resolve(new Response(null, { status: 401 }));
    });

  it("offers to set a PIN when none is configured", async () => {
    withPin(false);
    render(<AccountSection />);
    expect(await screen.findByText("Set up a PIN")).toBeInTheDocument();
    expect(screen.getByText("Off")).toBeInTheDocument();
    expect(screen.queryByText("Turn off")).not.toBeInTheDocument();
  });

  /** A failed "Turn off" must SAY so. The error paragraph used to live only
   *  inside the expanded form, but "Turn off" only exists while the card is
   *  COLLAPSED -- so the request failed, the spinner returned to the label,
   *  and the screen said nothing at all. */
  it("shows why a failed 'Turn off' did not work", async () => {
    withPin(true, () => new Response(
      JSON.stringify({ error: "incorrect password" }),
      { status: 403, headers: { "Content-Type": "application/json" } },
    ));
    render(<AccountSection />);
    fireEvent.click(await screen.findByText("Turn off"));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("offers to change or turn off a PIN that is configured", async () => {
    withPin(true);
    render(<AccountSection />);
    expect(await screen.findByText("Change PIN")).toBeInTheDocument();
    expect(screen.getByText("Turn off")).toBeInTheDocument();
    expect(screen.getByText("On")).toBeInTheDocument();
  });

  it("says where a PIN works, so nobody expects it to work over the network", async () => {
    withPin(false);
    render(<AccountSection />);
    expect(await screen.findByText(/only on that\s+screen/i)).toBeInTheDocument();
  });

  it("refuses a PIN shorter than four digits without calling the server", async () => {
    const pinCall = vi.fn(() => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    withPin(false, pinCall);
    render(<AccountSection />);
    fireEvent.click(await screen.findByText("Set up a PIN"));
    fireEvent.change(screen.getByLabelText("New PIN"), { target: { value: "12" } });
    fireEvent.change(screen.getByLabelText("Confirm PIN"), { target: { value: "12" } });
    fireEvent.change(screen.getByLabelText("Account password"), { target: { value: "pw" } });
    fireEvent.click(screen.getByText("Save PIN"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/4-12 digits/);
    expect(pinCall).not.toHaveBeenCalled();
  });

  it("refuses a mismatched confirmation", async () => {
    const pinCall = vi.fn(() => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    withPin(false, pinCall);
    render(<AccountSection />);
    fireEvent.click(await screen.findByText("Set up a PIN"));
    fireEvent.change(screen.getByLabelText("New PIN"), { target: { value: "4913" } });
    fireEvent.change(screen.getByLabelText("Confirm PIN"), { target: { value: "4914" } });
    fireEvent.change(screen.getByLabelText("Account password"), { target: { value: "pw" } });
    fireEvent.click(screen.getByText("Save PIN"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/do not match/i);
    expect(pinCall).not.toHaveBeenCalled();
  });

  it("will not mint a PIN without the account password", async () => {
    const pinCall = vi.fn(() => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    withPin(false, pinCall);
    render(<AccountSection />);
    fireEvent.click(await screen.findByText("Set up a PIN"));
    fireEvent.change(screen.getByLabelText("New PIN"), { target: { value: "4913" } });
    fireEvent.change(screen.getByLabelText("Confirm PIN"), { target: { value: "4913" } });
    fireEvent.click(screen.getByText("Save PIN"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/account password/i);
    expect(pinCall).not.toHaveBeenCalled();
  });

  it("saves a valid PIN and reports it as on", async () => {
    const seen: RequestInit[] = [];
    withPin(false, (init) => {
      if (init) seen.push(init);
      return new Response(JSON.stringify({ ok: true, has_pin: true }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    });
    render(<AccountSection />);
    fireEvent.click(await screen.findByText("Set up a PIN"));
    fireEvent.change(screen.getByLabelText("New PIN"), { target: { value: "4913" } });
    fireEvent.change(screen.getByLabelText("Confirm PIN"), { target: { value: "4913" } });
    fireEvent.change(screen.getByLabelText("Account password"), { target: { value: "hunter22" } });
    fireEvent.click(screen.getByText("Save PIN"));
    await waitFor(() => expect(screen.getByText("Change PIN")).toBeInTheDocument());
    expect(screen.getByText("On")).toBeInTheDocument();
    expect(seen[0]?.method).toBe("POST");
    expect(JSON.parse(String(seen[0]?.body))).toEqual({ pin: "4913", password: "hunter22" });
  });

  it("surfaces the server's refusal rather than claiming success", async () => {
    withPin(false, () => new Response(JSON.stringify({ error: "incorrect password" }), {
      status: 403, headers: { "Content-Type": "application/json" },
    }));
    render(<AccountSection />);
    fireEvent.click(await screen.findByText("Set up a PIN"));
    fireEvent.change(screen.getByLabelText("New PIN"), { target: { value: "4913" } });
    fireEvent.change(screen.getByLabelText("Confirm PIN"), { target: { value: "4913" } });
    fireEvent.change(screen.getByLabelText("Account password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByText("Save PIN"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/incorrect password/);
    expect(screen.getByText("Off")).toBeInTheDocument();
  });

  it("turns a PIN off without asking for a password", async () => {
    const seen: RequestInit[] = [];
    withPin(true, (init) => {
      if (init) seen.push(init);
      return new Response(JSON.stringify({ ok: true, removed: true, has_pin: false }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    });
    render(<AccountSection />);
    fireEvent.click(await screen.findByText("Turn off"));
    await waitFor(() => expect(screen.getByText("Set up a PIN")).toBeInTheDocument());
    expect(seen[0]?.method).toBe("DELETE");
    expect(screen.getByText("Off")).toBeInTheDocument();
  });
});
