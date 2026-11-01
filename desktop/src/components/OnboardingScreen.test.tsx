import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { OnboardingScreen } from "./OnboardingScreen";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

type Call = { url: string; method: string; body: unknown };

/**
 * Routes fetch by "METHOD url". Unrouted calls answer 200 {}. Every call is
 * recorded with its parsed body so a test can assert on order and payload.
 */
function routeFetch(routes: Record<string, (body: unknown) => Response | Promise<Response>> = {}) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    const route = routes[`${method} ${url}`];
    return route ? route(body) : json({ ok: true });
  });
  return calls;
}

const progressPuts = (calls: Call[]) =>
  calls.filter((c) => c.method === "PUT" && c.url === "/api/preferences/onboarding");

/** Hello -> name -> password -> create the local account. */
async function createLocalAccount(name = "Ada Lovelace", password = "12345678") {
  fireEvent.click(screen.getByRole("button", { name: "Get started" }));
  fireEvent.change(await screen.findByLabelText("Full name"), { target: { value: name } });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.change(await screen.findByLabelText("Password"), { target: { value: password } });
  fireEvent.change(screen.getByLabelText("Verify password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { name: "Your taOS account" });
}

async function chooseAccount(option: RegExp, password = "longenough") {
  fireEvent.click(screen.getByRole("button", { name: option }));
  fireEvent.change(await screen.findByLabelText("Email"), {
    target: { value: "ada@example.com" },
  });
  fireEvent.change(screen.getByLabelText("taOS account password"), {
    target: { value: password },
  });
}

describe("OnboardingScreen first run", () => {
  beforeEach(() => { vi.restoreAllMocks(); });
  afterEach(() => { vi.restoreAllMocks(); });

  it("opens on the welcome screen", () => {
    routeFetch();
    render(<OnboardingScreen onDone={() => {}} />);
    expect(screen.getByRole("heading", { name: "Welcome to taOS" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Get started" })).toBeInTheDocument();
  });

  it("derives the username from the name until the user edits it", async () => {
    routeFetch();
    render(<OnboardingScreen onDone={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Get started" }));
    const name = await screen.findByLabelText("Full name");
    const username = screen.getByLabelText("Username");
    fireEvent.change(name, { target: { value: "Ada Lovelace" } });
    expect(username).toHaveValue("ada");
    fireEvent.change(username, { target: { value: "Countess A" } });
    expect(username).toHaveValue("countessa");
    fireEvent.change(name, { target: { value: "Ada King" } });
    expect(username).toHaveValue("countessa");
  });

  it("will not continue without a name, a long enough password and a match", async () => {
    routeFetch();
    render(<OnboardingScreen onDone={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Get started" }));
    await screen.findByLabelText("Full name");
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Full name"), { target: { value: "Ada" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    const pw = await screen.findByLabelText("Password");
    const verify = screen.getByLabelText("Verify password");
    fireEvent.change(pw, { target: { value: "1234567" } });
    fireEvent.change(verify, { target: { value: "1234567" } });
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    fireEvent.change(pw, { target: { value: "12345678" } });
    fireEvent.change(verify, { target: { value: "12345679" } });
    expect(screen.getByRole("alert")).toHaveTextContent("Passwords don't match.");
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    fireEvent.change(verify, { target: { value: "12345678" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
  });

  it("creates the account, then records progress only once signed in", async () => {
    const calls = routeFetch();
    render(<OnboardingScreen onDone={() => {}} />);
    await createLocalAccount();

    const setup = calls.find((c) => c.url === "/auth/setup")!;
    expect(setup.method).toBe("POST");
    expect(setup.body).toMatchObject({
      username: "ada",
      full_name: "Ada Lovelace",
      password: "12345678",
      auto_login: true,
    });
    expect(setup.body).not.toHaveProperty("invite_code");
    // Nothing is written before /auth/setup: it would 401 with no session.
    const setupAt = calls.indexOf(setup);
    expect(progressPuts(calls).every((c) => calls.indexOf(c) > setupAt)).toBe(true);
    await waitFor(() =>
      expect(progressPuts(calls).at(-1)?.body).toMatchObject({
        in_progress: true,
        step: "taos-account",
      }),
    );
    // The account exists now: no way back to the screens that create it.
    expect(screen.queryByRole("button", { name: "Back" })).not.toBeInTheDocument();
  });

  it("offers no PIN unless the server says this is the device's own screen", async () => {
    const calls = routeFetch();
    render(<OnboardingScreen onDone={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Get started" }));
    fireEvent.change(await screen.findByLabelText("Full name"), { target: { value: "Ada" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByLabelText("Password");
    expect(screen.queryByLabelText("Screen PIN")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "12345678" } });
    fireEvent.change(screen.getByLabelText("Verify password"), { target: { value: "12345678" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { name: "Your taOS account" });
    expect(calls.find((c) => c.url === "/auth/setup")!.body).not.toHaveProperty("pin");
  });

  it("on the console, an optional PIN is sent with the account and a bad one blocks", async () => {
    const calls = routeFetch();
    render(<OnboardingScreen onDone={() => {}} pinOffered />);
    fireEvent.click(screen.getByRole("button", { name: "Get started" }));
    fireEvent.change(await screen.findByLabelText("Full name"), { target: { value: "Ada" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "12345678" } });
    fireEvent.change(screen.getByLabelText("Verify password"), { target: { value: "12345678" } });
    const pin = screen.getByLabelText("Screen PIN");
    const cont = screen.getByRole("button", { name: "Continue" });
    expect(cont).toBeEnabled();
    fireEvent.change(pin, { target: { value: "12" } });
    expect(cont).toBeDisabled();
    fireEvent.blur(pin);
    expect(screen.getByRole("alert")).toHaveTextContent("A PIN is 4 to 12 digits");
    fireEvent.change(pin, { target: { value: "4913" } });
    expect(cont).toBeEnabled();
    fireEvent.click(cont);
    await screen.findByRole("heading", { name: "Your taOS account" });
    expect(calls.find((c) => c.url === "/auth/setup")!.body).toMatchObject({ pin: "4913" });
  });

  it("on the console, a PIN left blank is not sent", async () => {
    const calls = routeFetch();
    render(<OnboardingScreen onDone={() => {}} pinOffered />);
    await createLocalAccount();
    expect(calls.find((c) => c.url === "/auth/setup")!.body).not.toHaveProperty("pin");
  });

  it("shows the server's reason when account creation fails, and stays put", async () => {
    routeFetch({ "POST /auth/setup": () => json({ error: "already configured" }, 409) });
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} />);
    fireEvent.click(screen.getByRole("button", { name: "Get started" }));
    fireEvent.change(await screen.findByLabelText("Full name"), { target: { value: "Ada" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "12345678" } });
    fireEvent.change(screen.getByLabelText("Verify password"), { target: { value: "12345678" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already configured");
    expect(screen.getByRole("heading", { name: "Create a password" })).toBeInTheDocument();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("offline only finishes setup, and onDone waits for the final progress write", async () => {
    // LoginGate re-reads progress in onDone; it must not see in_progress true.
    let releaseFinal: () => void = () => {};
    const finalHeld = new Promise<void>((r) => { releaseFinal = r; });
    const calls = routeFetch({
      "PUT /api/preferences/onboarding": async (body) => {
        if ((body as { in_progress: boolean }).in_progress === false) await finalHeld;
        return json({ ok: true });
      },
    });
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} />);
    await createLocalAccount();
    fireEvent.click(screen.getByRole("button", { name: /Use offline only/ }));
    await waitFor(() =>
      expect(progressPuts(calls).at(-1)?.body).toMatchObject({
        in_progress: false,
        skipped: ["taos-account", "device"],
      }),
    );
    expect(onDone).not.toHaveBeenCalled();
    releaseFinal();
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
  });

  it("signing in with a taOS account leads to the first-device screen, then on", async () => {
    const account = { user_id: "u-1", email: "ada@example.com", taosgo: { status: "none" } };
    const calls = routeFetch({
      "POST /api/account/login": () => json({ user_id: "u-1" }),
      "GET /api/account/me": () => json(account),
    });
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} />);
    await createLocalAccount();
    await chooseAccount(/Sign in with your taOS account/);
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(
      await screen.findByRole("heading", { name: "This is your first taOS device" }),
    ).toBeInTheDocument();
    expect(progressPuts(calls).at(-1)?.body).toMatchObject({ in_progress: true, step: "device" });
    expect(onDone).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(progressPuts(calls).at(-1)?.body).toMatchObject({
      in_progress: false,
      completed: expect.arrayContaining(["taos-account", "device"]),
      skipped: [],
    });
  });

  it("a wrong taOS account password shows an error and stays on the screen", async () => {
    routeFetch({
      "POST /api/account/login": () => json({ detail: "invalid_credentials" }, 401),
    });
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} />);
    await createLocalAccount();
    await chooseAccount(/Sign in with your taOS account/);
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That email and password don't match a taOS account.",
    );
    expect(onDone).not.toHaveBeenCalled();
  });

  it("closed sign-ups show a notice, and Back leads to offline", async () => {
    routeFetch({
      "POST /api/account/register": () => json({ detail: "signups_closed" }, 403),
    });
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} />);
    await createLocalAccount();
    await chooseAccount(/Create a taOS account/);
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("status")).toHaveTextContent("aren't open yet");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    fireEvent.click(await screen.findByRole("button", { name: /Use offline only/ }));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
  });

  it("closed sign-ups offer Continue offline, which skips the account step", async () => {
    const calls = routeFetch({
      "POST /api/account/register": () => json({ detail: "signups_closed" }, 403),
    });
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} />);
    await createLocalAccount();
    await chooseAccount(/Create a taOS account/);
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    fireEvent.click(await screen.findByRole("button", { name: "Continue offline" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(progressPuts(calls).at(-1)?.body).toMatchObject({
      in_progress: false,
      skipped: ["taos-account", "device"],
    });
  });

  it("an unreachable account service shows an error", async () => {
    routeFetch({
      "POST /api/account/login": () => json({ error: "account service unreachable" }, 503),
    });
    render(<OnboardingScreen onDone={() => {}} />);
    await createLocalAccount();
    await chooseAccount(/Sign in with your taOS account/);
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not available yet");
  });
});

describe("OnboardingScreen resume", () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it("resumes at the saved step with no way back to account creation", async () => {
    routeFetch();
    render(
      <OnboardingScreen
        onDone={() => {}}
        resume={{ step: "taos-account", completed: ["hello", "name", "password"], skipped: [] }}
      />,
    );
    expect(await screen.findByRole("heading", { name: "Your taOS account" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Back" })).not.toBeInTheDocument();
  });

  it("resumes on the first-device screen when the sign-in happened before the reload", async () => {
    routeFetch();
    render(
      <OnboardingScreen
        onDone={() => {}}
        resume={{ step: "device", completed: ["hello", "name", "password", "taos-account"], skipped: [] }}
      />,
    );
    expect(
      await screen.findByRole("heading", { name: "This is your first taOS device" }),
    ).toBeInTheDocument();
  });

  it("an unknown saved step resumes after account creation, never at the start", async () => {
    const calls = routeFetch();
    render(
      <OnboardingScreen onDone={() => {}} resume={{ step: "hello", completed: [], skipped: [] }} />,
    );
    expect(await screen.findByRole("heading", { name: "Your taOS account" })).toBeInTheDocument();
    expect(calls.some((c) => c.url === "/auth/setup")).toBe(false);
  });
});
