import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { LoginGate } from "./LoginGate";

function signedOut() {
  return new Response(
    JSON.stringify({ configured: true, authenticated: false, multi_user: true }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

describe("LoginGate hands sign-in to the server page", () => {
  let assign: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    sessionStorage.clear();
    assign = vi.fn();
    // jsdom's window.location is not writable; replace just the method we assert on.
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, pathname: "/desktop/", search: "", assign },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
  });

  it("redirects an unauthenticated visitor to /auth/login carrying next", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(signedOut());
    render(<LoginGate><div>the desktop shell</div></LoginGate>);

    await waitFor(() => expect(assign).toHaveBeenCalledTimes(1));
    expect(assign).toHaveBeenCalledWith(
      `/auth/login?next=${encodeURIComponent("/desktop/")}`,
    );
    // The shell must not render behind the handoff.
    expect(screen.queryByText("the desktop shell")).not.toBeInTheDocument();
  });

  it("renders no password field of its own when signed out", async () => {
    // The load-bearing assertion. The kiosk boots to /desktop, which is in
    // EXEMPT_PATHS, so the session gate never fires -- and this component's own
    // password-only form was what the touchscreen user got instead of the PIN
    // screen. Asserting only that the redirect fired would still pass if a
    // second sign-in form were rendered alongside it, which is the exact defect.
    vi.spyOn(globalThis, "fetch").mockResolvedValue(signedOut());
    const { container } = render(<LoginGate><div>the desktop shell</div></LoginGate>);

    await waitFor(() => expect(assign).toHaveBeenCalled());
    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(container.querySelector("form")).toBeNull();
  });

  it("stops instead of looping when it comes back still signed out", async () => {
    // A kiosk bouncing /desktop -> /auth/login -> /desktop forever is worse
    // than any login form: unusable, with nothing on screen saying why.
    sessionStorage.setItem("taos.login-redirected", "1");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(signedOut());
    render(<LoginGate><div>the desktop shell</div></LoginGate>);

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
    // A manual way out, and it is a link -- never a second password form.
    const link = screen.getByRole("link", { name: /go to sign in/i });
    // Same destination the automatic handoff would have used -- the two paths
    // must not drift.
    expect(link).toHaveAttribute(
      "href",
      `/auth/login?next=${encodeURIComponent("/desktop/")}`,
    );
  });

  it("clears the loop guard for an invited user mid-onboarding", async () => {
    // refreshStatus only reaches "invite" on authenticated: true, so the bounce
    // that set the guard already succeeded. If the guard survives here, a
    // session expiring part-way through profile completion drops the user on
    // the manual link instead of redirecting -- on a kiosk, a dead end.
    sessionStorage.setItem("taos.login-redirected", "1");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          configured: true,
          authenticated: true,
          needs_onboarding: true,
          multi_user: true,
          user: { username: "invitee" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    render(<LoginGate><div>the desktop shell</div></LoginGate>);

    await waitFor(() =>
      expect(sessionStorage.getItem("taos.login-redirected")).toBeNull(),
    );
  });

  it("renders invite completion for an invited user and posts /auth/complete", async () => {
    // /auth/status never carries the invite code, so the screen must not need
    // one to enter invite mode; the server takes the pending session instead.
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input) === "/auth/complete") {
        return new Response(JSON.stringify({ ok: true }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(
        JSON.stringify({
          configured: true,
          authenticated: true,
          needs_onboarding: true,
          multi_user: true,
          user: { username: "invitee" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });
    render(<LoginGate><div>the desktop shell</div></LoginGate>);

    expect(await screen.findByRole("heading", { name: "Complete your account" })).toBeInTheDocument();
    expect(screen.getByText("invitee")).toHaveAttribute("aria-readonly", "true");

    fireEvent.change(screen.getByLabelText("Full name"), { target: { value: "In Vitee" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "invitepass" } });
    fireEvent.change(screen.getByLabelText("Verify password"), { target: { value: "invitepass" } });
    fireEvent.click(screen.getByRole("button", { name: "Activate account" }));

    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u) === "/auth/complete")).toBe(true),
    );
    const [, init] = fetchMock.mock.calls.find(([u]) => String(u) === "/auth/complete")!;
    const body = JSON.parse(String(init?.body));
    expect(body.username).toBe("invitee");
    expect(body).not.toHaveProperty("invite_code");
    expect(fetchMock.mock.calls.some(([u]) => String(u) === "/auth/setup")).toBe(false);
  });

  it("resumes first-run setup for the owner when progress says it is unfinished", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input) === "/api/preferences/onboarding") {
        return new Response(
          JSON.stringify({ in_progress: true, step: "taos-account", completed: [], skipped: [] }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(
        JSON.stringify({ configured: true, authenticated: true, user: { username: "ada", is_admin: true } }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });
    render(<LoginGate><div>the desktop shell</div></LoginGate>);
    expect(await screen.findByRole("heading", { name: "Your taOS account" })).toBeInTheDocument();
    expect(screen.queryByText("the desktop shell")).not.toBeInTheDocument();
  });

  it.each([
    ["no saved progress", () => new Response("{}", { status: 200 })],
    ["finished progress", () => new Response(JSON.stringify({ in_progress: false, step: "done" }), { status: 200 })],
    ["an unreadable record", () => new Response("nope", { status: 500 })],
  ])("an existing install with %s goes straight to the desktop", async (_label, progress) => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
      String(input) === "/api/preferences/onboarding"
        ? progress()
        : new Response(
            JSON.stringify({ configured: true, authenticated: true, user: { username: "ada", is_admin: true } }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
    );
    render(<LoginGate><div>the desktop shell</div></LoginGate>);
    expect(await screen.findByText("the desktop shell")).toBeInTheDocument();
  });

  it("a non-owner never reads setup progress", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ configured: true, authenticated: true, user: { username: "bo", is_admin: false } }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    render(<LoginGate><div>the desktop shell</div></LoginGate>);
    expect(await screen.findByText("the desktop shell")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([u]) => String(u) === "/api/preferences/onboarding")).toBe(false);
  });

  it("clears the loop guard once a session exists", async () => {
    sessionStorage.setItem("taos.login-redirected", "1");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ configured: true, authenticated: true }), {
        status: 200, headers: { "Content-Type": "application/json" },
      }),
    );
    render(<LoginGate><div>the desktop shell</div></LoginGate>);

    await waitFor(() => expect(screen.getByText("the desktop shell")).toBeInTheDocument());
    expect(sessionStorage.getItem("taos.login-redirected")).toBeNull();
  });
});

describe("LoginGate host reachability", () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it("shows the off-network screen when /auth/status is unreachable", async () => {
    // A thrown fetch is a network failure (host unreachable), not an HTTP error.
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network down"));
    render(
      <LoginGate>
        <div>the desktop shell</div>
      </LoginGate>,
    );
    expect(await screen.findByText("Can't reach your taOS")).toBeInTheDocument();
    // The broken shell must NOT render behind it.
    expect(screen.queryByText("the desktop shell")).not.toBeInTheDocument();
  });

  it("renders the app shell when authenticated and reachable", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ configured: true, authenticated: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(
      <LoginGate>
        <div>the desktop shell</div>
      </LoginGate>,
    );
    await waitFor(() =>
      expect(screen.getByText("the desktop shell")).toBeInTheDocument(),
    );
  });
});
