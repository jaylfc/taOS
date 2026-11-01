import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { OnboardingScreen } from "./OnboardingScreen";
import { useProcessStore } from "@/stores/process-store";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

type Call = { url: string; method: string; body: unknown; headers: Record<string, string> };

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
    const headers = Object.fromEntries(new Headers(init?.headers).entries());
    calls.push({ url, method, body, headers });
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

/** The model step with nothing in the catalog that fits: Continue sets it up later. */
async function passModelStep() {
  await screen.findByRole("heading", { name: "A model on this device" });
  fireEvent.click(await screen.findByRole("button", { name: "Continue" }));
}

/** The taOS Agent step: Set Up Later. */
async function passAgentStep() {
  await screen.findByRole("heading", { name: "Choose the taOS Agent's model" });
  fireEvent.click(screen.getByRole("button", { name: "Set Up Later" }));
}

/** Privacy: leave both switches as they load. */
async function passPrivacyStep() {
  await screen.findByRole("heading", { name: "Privacy" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
}

/** First chat: Set Up Later (offered only when the assistant has a model). */
async function passFirstChatStep() {
  await screen.findByRole("heading", { name: "Meet your assistant" });
  fireEvent.click(screen.getByRole("button", { name: "Set Up Later" }));
}

/** About you: pick a topic, then accept the default style. */
async function answerAboutYou(topic = "Research") {
  await screen.findByRole("heading", { name: "What would you like help with?" });
  fireEvent.click(screen.getByRole("button", { name: topic }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { name: "How should it work with you?" });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
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
    await answerAboutYou();
    await passModelStep();
    await passAgentStep();
    await passPrivacyStep();
    await waitFor(() =>
      expect(progressPuts(calls).at(-1)?.body).toMatchObject({
        in_progress: false,
        skipped: ["taos-account", "device", "model", "agent", "first-chat"],
      }),
    );
    // Nothing to run an assistant on: the first chat is never offered.
    expect(calls.some((c) => c.url === "/api/agents/deploy" || c.url === "/api/hardware")).toBe(false);
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
    await answerAboutYou();
    await passModelStep();
    await passAgentStep();
    await passPrivacyStep();
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(progressPuts(calls).at(-1)?.body).toMatchObject({
      in_progress: false,
      completed: expect.arrayContaining(["taos-account", "device", "about"]),
      skipped: ["model", "agent", "first-chat"],
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
    await answerAboutYou();
    await passModelStep();
    await passAgentStep();
    await passPrivacyStep();
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
    await answerAboutYou();
    await passModelStep();
    await passAgentStep();
    await passPrivacyStep();
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(progressPuts(calls).at(-1)?.body).toMatchObject({
      in_progress: false,
      skipped: ["taos-account", "device", "model", "agent", "first-chat"],
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

  it("about you: picked topics and style are saved with progress", async () => {
    const calls = routeFetch();
    render(<OnboardingScreen onDone={() => {}} />);
    await createLocalAccount();
    fireEvent.click(screen.getByRole("button", { name: /Use offline only/ }));
    await screen.findByRole("heading", { name: "What would you like help with?" });
    const writing = screen.getByRole("button", { name: "Writing" });
    fireEvent.click(writing);
    fireEvent.click(screen.getByRole("button", { name: "Email and calendar" }));
    expect(writing).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(writing);
    expect(writing).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(await screen.findByRole("radio", { name: /Brief/ }));
    fireEvent.click(screen.getByRole("radio", { name: /Just do it/ }));
    expect(screen.getByRole("radio", { name: /Friendly/ })).toHaveAttribute("aria-checked", "false");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() =>
      expect(progressPuts(calls).at(-1)?.body).toMatchObject({
        completed: expect.arrayContaining(["about"]),
        about: { help: ["email"], tone: "brief", autonomy: "act" },
      }),
    );
  });

  it("about you: Back from the first page skips the first-device screen when offline", async () => {
    routeFetch();
    render(<OnboardingScreen onDone={() => {}} />);
    await createLocalAccount();
    fireEvent.click(screen.getByRole("button", { name: /Use offline only/ }));
    await screen.findByRole("heading", { name: "What would you like help with?" });
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(await screen.findByRole("heading", { name: "Your taOS account" })).toBeInTheDocument();
  });

  it("about you: Set Up Later records the step as skipped with default answers", async () => {
    const calls = routeFetch();
    render(<OnboardingScreen onDone={() => {}} />);
    await createLocalAccount();
    fireEvent.click(screen.getByRole("button", { name: /Use offline only/ }));
    await screen.findByRole("heading", { name: "What would you like help with?" });
    fireEvent.click(screen.getByRole("button", { name: "Research" }));
    fireEvent.click(screen.getByRole("button", { name: "Set Up Later" }));
    await waitFor(() =>
      expect(progressPuts(calls).at(-1)?.body).toMatchObject({
        skipped: expect.arrayContaining(["about"]),
        about: { help: [], tone: "warm", autonomy: "ask" },
      }),
    );
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

  it("resumes about you with the answers saved before the reload", async () => {
    routeFetch();
    render(
      <OnboardingScreen
        onDone={() => {}}
        resume={{
          step: "about",
          completed: ["hello", "name", "password"],
          skipped: ["taos-account", "device"],
          about: { help: ["coding"], tone: "formal", autonomy: "ask" },
        }}
      />,
    );
    expect(await screen.findByRole("button", { name: "Coding" })).toHaveAttribute("aria-pressed", "true");
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

describe("OnboardingScreen on-device model", () => {
  afterEach(() => { vi.restoreAllMocks(); });

  const catalog = (downloaded = false) =>
    json({
      hardware_profile_id: "arm-8gb",
      models: [
        {
          id: "qwen3-1.7b",
          name: "Qwen3 1.7B",
          capabilities: ["chat"],
          hardware_tiers: { "arm-8gb": { recommended: "q4" } },
          variants: [
            { id: "q8", format: "gguf", size_mb: 1900, compatibility: "green", downloaded: false },
            { id: "q4", format: "gguf", size_mb: 1100, compatibility: "green", downloaded },
          ],
        },
      ],
    });

  const atModelStep = { step: "model", completed: ["hello", "name", "password", "about"], skipped: ["taos-account", "device"] };

  it("offers the model sized to this device and downloads it with progress", async () => {
    let polls = 0;
    const calls = routeFetch({
      "GET /api/models": () => catalog(),
      "POST /api/models/download": () => json({ status: "started", download_id: "qwen3-1.7b-q4" }),
      "GET /api/models/downloads/qwen3-1.7b-q4": () =>
        json(++polls < 2 ? { status: "downloading", percent: 40 } : { status: "complete", percent: 100 }),
    });
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} resume={atModelStep} />);
    expect(await screen.findByText("Qwen3 1.7B")).toBeInTheDocument();
    expect(screen.getByText("1.1 GB download")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Download" }));
    expect(await screen.findByRole("progressbar")).toHaveAttribute("aria-valuenow", "40");
    expect(calls.find((c) => c.url === "/api/models/download")?.body).toEqual({
      app_id: "qwen3-1.7b",
      variant_id: "q4",
    });
    expect(await screen.findByText("Downloaded and ready", {}, { timeout: 3000 })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await passAgentStep();
    await passPrivacyStep();
    await passFirstChatStep();
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(progressPuts(calls).at(-1)?.body).toMatchObject({
      in_progress: false,
      completed: expect.arrayContaining(["model"]),
      model: { app_id: "qwen3-1.7b", variant_id: "q4", downloaded: true },
    });
  });

  it("continuing while it downloads records the model as not yet downloaded", async () => {
    const calls = routeFetch({
      "GET /api/models": () => catalog(),
      "POST /api/models/download": () => json({ download_id: "qwen3-1.7b-q4" }),
      "GET /api/models/downloads/qwen3-1.7b-q4": () => json({ status: "downloading", percent: 5 }),
    });
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} resume={atModelStep} />);
    fireEvent.click(await screen.findByRole("button", { name: "Download" }));
    await screen.findByRole("progressbar");
    expect(screen.getByText("It keeps downloading if you continue.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await passAgentStep();
    await passPrivacyStep();
    await passFirstChatStep();
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(progressPuts(calls).at(-1)?.body).toMatchObject({
      model: { app_id: "qwen3-1.7b", downloaded: false },
    });
  });

  it("a model already on the device needs no download", async () => {
    const calls = routeFetch({ "GET /api/models": () => catalog(true) });
    render(<OnboardingScreen onDone={() => {}} resume={atModelStep} />);
    expect(await screen.findByText("Downloaded and ready")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() =>
      expect(progressPuts(calls).at(-1)?.body).toMatchObject({ model: { variant_id: "q4", downloaded: true } }),
    );
    expect(calls.some((c) => c.url === "/api/models/download")).toBe(false);
  });

  it("a download that fails says why and offers to try again or set up later", async () => {
    routeFetch({
      "GET /api/models": () => catalog(),
      "POST /api/models/download": () => json({ download_id: "qwen3-1.7b-q4" }),
      "GET /api/models/downloads/qwen3-1.7b-q4": () => json({ status: "error", error: "Checksum mismatch" }),
    });
    render(<OnboardingScreen onDone={() => {}} resume={atModelStep} />);
    fireEvent.click(await screen.findByRole("button", { name: "Download" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Checksum mismatch");
    expect(screen.getByRole("button", { name: "Try Again" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set Up Later" })).toBeInTheDocument();
  });

  it("a download the server refuses shows its reason", async () => {
    routeFetch({
      "GET /api/models": () => catalog(),
      "POST /api/models/download": () => json({ error: "No download URL for variant" }, 400),
    });
    render(<OnboardingScreen onDone={() => {}} resume={atModelStep} />);
    fireEvent.click(await screen.findByRole("button", { name: "Download" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No download URL for variant");
  });

  it("an unreadable model list says so", async () => {
    routeFetch({ "GET /api/models": () => json({ error: "boom" }, 500) });
    render(<OnboardingScreen onDone={() => {}} resume={atModelStep} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't read the model list");
  });

  it("Set Up Later records the step as skipped with no model", async () => {
    const calls = routeFetch({ "GET /api/models": () => catalog() });
    const onDone = vi.fn();
    render(
      <OnboardingScreen
        onDone={onDone}
        resume={{ ...atModelStep, model: { app_id: "x", variant_id: "y", name: "X", size_mb: 1, downloaded: false } }}
      />,
    );
    await screen.findByText("Qwen3 1.7B");
    fireEvent.click(screen.getByRole("button", { name: "Set Up Later" }));
    await passAgentStep();
    await passPrivacyStep();
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    const last = progressPuts(calls).at(-1)?.body as Record<string, unknown>;
    expect(last).toMatchObject({ skipped: expect.arrayContaining(["model"]) });
    expect(last.model).toBeUndefined();
    expect(calls.some((c) => c.url === "/api/models/download")).toBe(false);
  });

  it("Back from the model step lands on the last about-you page", async () => {
    routeFetch({ "GET /api/models": () => catalog() });
    render(<OnboardingScreen onDone={() => {}} resume={atModelStep} />);
    await screen.findByText("Qwen3 1.7B");
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(await screen.findByRole("heading", { name: "How should it work with you?" })).toBeInTheDocument();
  });
});

describe("OnboardingScreen taOS Agent model", () => {
  afterEach(() => { vi.restoreAllMocks(); });

  const atAgentStep = {
    step: "agent",
    completed: ["hello", "name", "password", "about"],
    skipped: ["taos-account", "device", "model"],
  };
  const onDevice = { app_id: "qwen3-1.7b", variant_id: "q4", name: "Qwen3 1.7B", size_mb: 1100, downloaded: true };
  const providers = (models: unknown[]) => () => json([{ name: "openai", type: "openai", models }]);
  const chainUrls = ["/api/secrets", "/api/secrets/provider-openai-key", "/api/providers", "/api/taos-agent/settings"];
  const chain = (calls: Call[]) =>
    calls.filter((c) => chainUrls.includes(c.url)).map((c) => `${c.method} ${c.url}`);

  async function connect(key = "sk-test") {
    fireEvent.click(await screen.findByRole("button", { name: /Use a cloud model/ }));
    fireEvent.change(await screen.findByLabelText("API key"), { target: { value: key } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
  }

  it("saves the key, adds the provider, reads its models, then sets the one picked", async () => {
    const calls = routeFetch({ "GET /api/providers": providers(["gpt-4o", { id: "gpt-4o-mini" }]) });
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} resume={atAgentStep} />);
    await connect("  sk-test  ");
    await screen.findByRole("heading", { name: "Pick a model" });
    fireEvent.click(screen.getByRole("radio", { name: "gpt-4o-mini" }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await passPrivacyStep();
    await passFirstChatStep();
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(chain(calls)).toEqual([
      "POST /api/secrets",
      "POST /api/providers",
      "GET /api/providers",
      "PATCH /api/taos-agent/settings",
      // The privacy summary names the providers that are set up.
      "GET /api/providers",
    ]);
    const body = (method: string, url: string) => calls.find((c) => c.method === method && c.url === url)?.body;
    expect(body("POST", "/api/secrets")).toMatchObject({ name: "provider-openai-key", value: "sk-test", category: "api-key" });
    expect(body("POST", "/api/providers")).toEqual({ name: "openai", type: "openai", api_key_secret: "provider-openai-key" });
    expect(body("PATCH", "/api/taos-agent/settings")).toEqual({ model: "gpt-4o-mini" });
    expect(progressPuts(calls).at(-1)?.body).toMatchObject({
      in_progress: false,
      completed: expect.arrayContaining(["agent"]),
      agent_model: "gpt-4o-mini",
    });
  });

  it("a key saved on an earlier try is replaced, and an existing provider is used as it is", async () => {
    const calls = routeFetch({
      "POST /api/secrets": () => json({ error: "exists" }, 409),
      "POST /api/providers": () => json({ error: "exists" }, 409),
      "GET /api/providers": providers(["gpt-4o"]),
    });
    render(<OnboardingScreen onDone={() => {}} resume={atAgentStep} />);
    await connect("sk-new");
    await screen.findByRole("heading", { name: "Pick a model" });
    expect(chain(calls)).toEqual([
      "POST /api/secrets",
      "PUT /api/secrets/provider-openai-key",
      "POST /api/providers",
      "GET /api/providers",
    ]);
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ value: "sk-new" });
  });

  it.each([
    ["saving the key", { "POST /api/secrets": () => json({ error: "vault locked" }, 500) }, "Couldn't save the API key: vault locked."],
    ["adding the provider", { "POST /api/providers": () => json({ detail: "bad type" }, 400) }, "Couldn't add OpenAI: bad type."],
    ["reading its models", { "GET /api/providers": () => json({}, 503) }, "Couldn't read OpenAI's models: error 503."],
    ["a provider with no models", { "GET /api/providers": providers([]) }, "OpenAI didn't list any models."],
  ])("a failure %s names that hop and stays on the key screen", async (_hop, routes, message) => {
    routeFetch(routes as Record<string, () => Response>);
    render(<OnboardingScreen onDone={() => {}} resume={atAgentStep} />);
    await connect();
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("heading", { name: "Add a cloud model" })).toBeInTheDocument();
  });

  it("a failure setting the model names that hop and stays on the list", async () => {
    const calls = routeFetch({
      "GET /api/providers": providers(["gpt-4o"]),
      "PATCH /api/taos-agent/settings": () => json({ error: "agent offline" }, 502),
    });
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} resume={atAgentStep} />);
    await connect();
    await screen.findByRole("heading", { name: "Pick a model" });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't give the taOS Agent this model: agent offline.");
    expect(onDone).not.toHaveBeenCalled();
    expect(progressPuts(calls)).toHaveLength(0);
  });

  it("Back from the model list returns to the key screen with the key cleared", async () => {
    routeFetch({ "GET /api/providers": providers(["gpt-4o"]) });
    render(<OnboardingScreen onDone={() => {}} resume={atAgentStep} />);
    await connect("sk-wrong");
    await screen.findByRole("heading", { name: "Pick a model" });
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await screen.findByRole("heading", { name: "Add a cloud model" });
    expect(screen.getByLabelText("API key")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Connect" })).toBeDisabled();
  });

  it("a long model list can be filtered and says how much it shows", async () => {
    const ids = Array.from({ length: 50 }, (_, i) => `model-${String(i).padStart(2, "0")}`);
    routeFetch({ "GET /api/providers": providers(ids) });
    render(<OnboardingScreen onDone={() => {}} resume={atAgentStep} />);
    await connect();
    await screen.findByRole("heading", { name: "Pick a model" });
    expect(screen.getAllByRole("radio")).toHaveLength(40);
    expect(screen.getByText("Showing 40 of 50. Type to narrow the list.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Filter models"), { target: { value: "model-4" } });
    expect(screen.getAllByRole("radio").map((r) => r.textContent)).toEqual(ids.slice(40));
    fireEvent.change(screen.getByLabelText("Filter models"), { target: { value: "nope" } });
    expect(screen.getByText('No model matches "nope".')).toBeInTheDocument();
  });

  it("a pick the filter hides cannot be sent, until one in view is picked", async () => {
    const ids = Array.from({ length: 12 }, (_, i) => `model-${String(i).padStart(2, "0")}`);
    const calls = routeFetch({ "GET /api/providers": providers(ids) });
    render(<OnboardingScreen onDone={() => {}} resume={atAgentStep} />);
    await connect();
    await screen.findByRole("heading", { name: "Pick a model" });
    expect(screen.getByRole("radio", { name: "model-00" })).toBeChecked();
    fireEvent.change(screen.getByLabelText("Filter models"), { target: { value: "model-1" } });
    const cont = screen.getByRole("button", { name: "Continue" });
    expect(cont).toBeDisabled();
    fireEvent.submit(cont.closest("form")!);
    expect(calls.some((c) => c.url === "/api/taos-agent/settings")).toBe(false);
    fireEvent.click(screen.getByRole("radio", { name: "model-11" }));
    expect(cont).toBeEnabled();
  });

  it("Set Up Later gives the agent the on-device model, still recorded as skipped", async () => {
    const calls = routeFetch();
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} resume={{ ...atAgentStep, model: onDevice }} />);
    await screen.findByText("Until then it uses the model on this device, which can do less.");
    await passAgentStep();
    await passPrivacyStep();
    await passFirstChatStep();
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(calls.find((c) => c.url === "/api/taos-agent/settings")?.body).toEqual({ model: "qwen3-1.7b" });
    expect(progressPuts(calls).at(-1)?.body).toMatchObject({
      skipped: expect.arrayContaining(["agent"]),
      agent_model: "qwen3-1.7b",
    });
  });

  it("Set Up Later with no on-device model sets nothing", async () => {
    const calls = routeFetch();
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} resume={atAgentStep} />);
    await screen.findByText("Until then the taOS Agent has no model. Add one any time in Settings.");
    await passAgentStep();
    await passPrivacyStep();
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(calls.some((c) => c.url === "/api/taos-agent/settings")).toBe(false);
    const last = progressPuts(calls).at(-1)?.body as Record<string, unknown>;
    expect(last).toMatchObject({ skipped: expect.arrayContaining(["agent"]) });
    expect(last.agent_model).toBeUndefined();
  });

  it("a model on another taOS device is offered once the cluster answers", async () => {
    const calls = routeFetch({
      "GET /api/cluster/workers": () =>
        json([{ name: "studio", status: "online", models: ["llama-3.1-8b"], backends: [] }]),
    });
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} resume={atAgentStep} />);
    fireEvent.click(await screen.findByRole("button", { name: /Use a model on another taOS device/ }));
    await screen.findByRole("heading", { name: "Pick a model" });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await passPrivacyStep();
    await passFirstChatStep();
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(calls.find((c) => c.url === "/api/taos-agent/settings")?.body).toEqual({ model: "llama-3.1-8b" });
  });

  it("resumes at the agent step", async () => {
    routeFetch();
    render(<OnboardingScreen onDone={() => {}} resume={atAgentStep} />);
    expect(await screen.findByRole("heading", { name: "Choose the taOS Agent's model" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Free taOS models/ })).toBeDisabled();
  });
});

describe("OnboardingScreen privacy", () => {
  afterEach(() => { vi.restoreAllMocks(); });

  const atPrivacy = {
    step: "privacy",
    completed: ["hello", "name", "password", "about"],
    skipped: ["taos-account", "device", "model", "agent"],
  };
  const PING = "/api/preferences/auto-update";
  const SHARING = "/api/torrent/settings";
  const savedPing = () => json({ check_enabled: true, update_ping_enabled: true, last_notified_commit: "abc1234" });
  const savedSharing = (available = true) => () =>
    json({ libtorrent_available: available, seed_enabled: true, upload_rate_limit_kbps: 2500, max_active_seeds: 7 });
  const puts = (calls: Call[], url: string) => calls.filter((c) => c.method === "PUT" && c.url === url);

  async function open(routes: Parameters<typeof routeFetch>[0]) {
    const calls = routeFetch(routes);
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} resume={atPrivacy} />);
    await screen.findByRole("heading", { name: "Privacy" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled());
    return { calls, onDone };
  }

  it("shows both settings as the server has them and writes nothing when neither moves", async () => {
    const { calls, onDone } = await open({ [`GET ${PING}`]: savedPing, [`GET ${SHARING}`]: savedSharing() });
    expect(screen.getByRole("switch", { name: "Anonymous update ping" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Share models you download" })).toBeChecked();
    expect(screen.getByText(/up to 2\.5 MB\/s/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(puts(calls, PING)).toEqual([]);
    expect(puts(calls, SHARING)).toEqual([]);
    expect(progressPuts(calls).at(-1)?.body).toMatchObject({
      in_progress: false,
      completed: expect.arrayContaining(["privacy"]),
    });
  });

  it("a setting already off on the server loads off", async () => {
    await open({
      [`GET ${PING}`]: () => json({ update_ping_enabled: false }),
      [`GET ${SHARING}`]: () => json({ libtorrent_available: true, seed_enabled: false, upload_rate_limit_kbps: 0, max_active_seeds: 20 }),
    });
    expect(screen.getByRole("switch", { name: "Anonymous update ping" })).not.toBeChecked();
    expect(screen.getByRole("switch", { name: "Share models you download" })).not.toBeChecked();
    expect(screen.queryByText(/up to/)).not.toBeInTheDocument();
  });

  it("turning the ping off sends back every saved field with only the switch changed", async () => {
    const { calls, onDone } = await open({ [`GET ${PING}`]: savedPing, [`GET ${SHARING}`]: savedSharing() });
    fireEvent.click(screen.getByRole("switch", { name: "Anonymous update ping" }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(puts(calls, PING).map((c) => c.body)).toEqual([
      { check_enabled: true, update_ping_enabled: false, last_notified_commit: "abc1234" },
    ]);
    expect(puts(calls, SHARING)).toEqual([]);
  });

  it("turning sharing off keeps the upload cap and seed count", async () => {
    const { calls, onDone } = await open({ [`GET ${PING}`]: savedPing, [`GET ${SHARING}`]: savedSharing() });
    fireEvent.click(screen.getByRole("switch", { name: "Share models you download" }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(puts(calls, SHARING).map((c) => c.body)).toEqual([
      { seed_enabled: false, upload_rate_limit_kbps: 2500, max_active_seeds: 7 },
    ]);
    expect(puts(calls, PING)).toEqual([]);
  });

  it("a fresh install with nothing saved reads as on, and turning it off writes just the switch", async () => {
    const { calls, onDone } = await open({ [`GET ${PING}`]: () => json(null), [`GET ${SHARING}`]: savedSharing() });
    const ping = screen.getByRole("switch", { name: "Anonymous update ping" });
    expect(ping).toBeChecked();
    expect(ping).toBeEnabled();
    fireEvent.click(ping);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(puts(calls, PING).map((c) => c.body)).toEqual([{ update_ping_enabled: false }]);
  });

  it("a ping setting that cannot be read is left to Settings, and setup still goes on", async () => {
    const { calls, onDone } = await open({ [`GET ${PING}`]: () => json({ error: "boom" }, 500), [`GET ${SHARING}`]: savedSharing() });
    // No switch at all: one drawn on or off would claim a state nobody read.
    expect(screen.queryByRole("switch", { name: "Anonymous update ping" })).not.toBeInTheDocument();
    expect(screen.getByText("Anonymous update ping")).toBeInTheDocument();
    expect(screen.getByText(/Couldn't read this setting/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(puts(calls, PING)).toEqual([]);
  });

  it("draws no switch until the settings are read", async () => {
    routeFetch({ [`GET ${PING}`]: () => new Promise<Response>(() => {}) });
    render(<OnboardingScreen onDone={() => {}} resume={atPrivacy} />);
    await screen.findByRole("heading", { name: "Privacy" });
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("no sharing switch when this install cannot share models", async () => {
    await open({ [`GET ${PING}`]: savedPing, [`GET ${SHARING}`]: savedSharing(false) });
    expect(screen.queryByRole("switch", { name: "Share models you download" })).not.toBeInTheDocument();
  });

  it("no sharing switch when the torrent settings store is missing", async () => {
    await open({ [`GET ${PING}`]: savedPing, [`GET ${SHARING}`]: () => json({ error: "not initialised" }, 503) });
    expect(screen.queryByRole("switch", { name: "Share models you download" })).not.toBeInTheDocument();
  });

  it("a switch that fails to save stops setup with an error, and Continue tries again", async () => {
    let fail = true;
    const { calls, onDone } = await open({
      [`GET ${PING}`]: savedPing,
      [`GET ${SHARING}`]: savedSharing(),
      [`PUT ${SHARING}`]: () => (fail ? json({ error: "disk" }, 500) : json({ status: "saved" })),
    });
    fireEvent.click(screen.getByRole("switch", { name: "Share models you download" }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't save the model sharing setting");
    expect(onDone).not.toHaveBeenCalled();
    expect(progressPuts(calls).some((c) => (c.body as { in_progress: boolean }).in_progress === false)).toBe(false);
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(puts(calls, SHARING)).toHaveLength(2);
  });

  it("names the cloud providers that are set up, and only cloud ones", async () => {
    await open({
      [`GET ${PING}`]: savedPing,
      "GET /api/providers": () => json([{ name: "openai", type: "openai" }, { name: "local", type: "llama-cpp" }]),
    });
    expect(screen.getByText("Cloud models get what you ask them. You've added OpenAI.")).toBeInTheDocument();
  });

  it("Back goes to the taOS Agent step", async () => {
    await open({ [`GET ${PING}`]: savedPing });
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(await screen.findByRole("heading", { name: "Choose the taOS Agent's model" })).toBeInTheDocument();
  });
});

describe("OnboardingScreen first chat", () => {
  beforeEach(() => { useProcessStore.setState({ windows: [] }); });
  afterEach(() => { vi.restoreAllMocks(); });

  const about = { help: ["coding"], tone: "warm" as const, autonomy: "ask" as const };
  const atFirstChat = {
    step: "first-chat",
    completed: ["hello", "name", "password", "about", "agent"],
    skipped: ["taos-account", "device", "model"],
    about,
    agent_model: "gpt-4o-mini",
  };
  const onDevice = { app_id: "qwen3-1.7b", variant_id: "q4", name: "Qwen3 1.7B", size_mb: 1100, downloaded: true };
  const PROMPTS = [
    "Explain what a piece of code does.",
    "Help me plan my week.",
    "Find the latest news on a topic I care about.",
  ];
  const hardware = () => json({ recommended_framework: "picoclaw" });
  const me = () => json({ user: { full_name: "Ada Lovelace" } });
  const deploys = (calls: Call[]) => calls.filter((c) => c.url === "/api/agents/deploy");
  const messagesWindows = () => useProcessStore.getState().windows.filter((w) => w.appId === "messages");

  it("deploys the assistant for this device, records it, then opens Messages on it", async () => {
    const calls = routeFetch({
      "GET /api/hardware": hardware,
      "GET /auth/me": me,
      "POST /api/agents/deploy": () => json({ name: "assistant-2", status: "deploying" }),
    });
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} resume={atFirstChat} />);
    expect(await screen.findByRole("heading", { name: "Meet your assistant" })).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Assistant");
    for (const p of PROMPTS) expect(screen.getByText(p)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "  Sage  " } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());

    const hw = calls.findIndex((c) => c.url === "/api/hardware");
    const dep = calls.findIndex((c) => c.url === "/api/agents/deploy");
    expect(hw).toBeGreaterThanOrEqual(0);
    expect(dep).toBeGreaterThan(hw);
    expect(calls[dep]!.body).toEqual({
      name: "Sage",
      framework: "picoclaw",
      model: "gpt-4o-mini",
      soul_md: expect.stringMatching(/^You are Ada Lovelace's personal assistant[^]*help with: coding\./),
    });
    expect(calls[dep]!.headers["idempotency-key"]).toMatch(/\S{8,}/);
    // Setup never makes the DM itself: the deploy does.
    expect(calls.some((c) => c.method === "POST" && c.url === "/api/chat/channels")).toBe(false);
    expect(progressPuts(calls).at(-1)?.body).toMatchObject({
      in_progress: false,
      completed: expect.arrayContaining(["first-chat"]),
      first_chat: { agent: "assistant-2" },
    });
    expect(messagesWindows()).toHaveLength(1);
    expect(messagesWindows()[0]!.props).toEqual({ firstChatAgent: "assistant-2", suggestedPrompts: PROMPTS });
  });

  it("a failed deploy says why and stays, and Continue retries with the same key", async () => {
    let tries = 0;
    const calls = routeFetch({
      "GET /api/hardware": hardware,
      "GET /auth/me": me,
      "POST /api/agents/deploy": () =>
        ++tries === 1 ? json({ error: "No room for another agent" }, 409) : json({ name: "assistant" }),
    });
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} resume={atFirstChat} />);
    fireEvent.click(await screen.findByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't set up your assistant: No room for another agent.",
    );
    expect(screen.getByRole("heading", { name: "Meet your assistant" })).toBeInTheDocument();
    expect(onDone).not.toHaveBeenCalled();
    expect(messagesWindows()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    const [first, second] = deploys(calls);
    expect(second).toBeDefined();
    expect(second!.headers["idempotency-key"]).toBe(first!.headers["idempotency-key"]);
  });

  it("an unreadable hardware profile names that hop and deploys nothing", async () => {
    const calls = routeFetch({ "GET /api/hardware": () => json({ error: "boom" }, 500) });
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} resume={atFirstChat} />);
    fireEvent.click(await screen.findByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't read this device's hardware: boom.");
    expect(deploys(calls)).toHaveLength(0);
    expect(onDone).not.toHaveBeenCalled();
  });

  it("with only the on-device model, the assistant runs on that", async () => {
    const calls = routeFetch({
      "GET /api/hardware": hardware,
      "POST /api/agents/deploy": () => json({ name: "assistant" }),
    });
    const onDone = vi.fn();
    const { agent_model: _unused, ...rest } = atFirstChat;
    render(<OnboardingScreen onDone={onDone} resume={{ ...rest, model: onDevice }} />);
    fireEvent.click(await screen.findByRole("button", { name: "Continue" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(deploys(calls)[0]!.body).toMatchObject({ model: "qwen3-1.7b" });
  });

  it("asks for a usable name before it will continue", async () => {
    routeFetch();
    render(<OnboardingScreen onDone={() => {}} resume={atFirstChat} />);
    const name = await screen.findByLabelText("Name");
    const cont = screen.getByRole("button", { name: "Continue" });
    fireEvent.change(name, { target: { value: "   " } });
    expect(cont).toBeDisabled();
    expect(screen.queryByText("Give your assistant a name.")).not.toBeInTheDocument();
    fireEvent.change(name, { target: { value: "!!!" } });
    expect(cont).toBeDisabled();
    expect(screen.getByText("Use at least one letter or number.")).toBeInTheDocument();
    fireEvent.change(name, { target: { value: "R2" } });
    expect(cont).toBeEnabled();
  });

  it("Set Up Later finishes with the first chat skipped and deploys nothing", async () => {
    const calls = routeFetch();
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} resume={atFirstChat} />);
    await passFirstChatStep();
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(progressPuts(calls).at(-1)?.body).toMatchObject({
      in_progress: false,
      skipped: expect.arrayContaining(["first-chat"]),
    });
    expect(deploys(calls)).toHaveLength(0);
    expect(messagesWindows()).toHaveLength(0);
  });

  it("with no model at all after the agent step, setup ends without offering it", async () => {
    const calls = routeFetch();
    const onDone = vi.fn();
    render(
      <OnboardingScreen
        onDone={onDone}
        resume={{ step: "privacy", completed: ["hello", "name", "password"], skipped: ["taos-account", "device", "model", "agent"] }}
      />,
    );
    await passPrivacyStep();
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(screen.queryByRole("heading", { name: "Meet your assistant" })).not.toBeInTheDocument();
    // Never landed on, not landed on and left: no progress ever points at it.
    expect(progressPuts(calls).map((c) => (c.body as { step: string }).step)).not.toContain("first-chat");
    expect(progressPuts(calls).at(-1)?.body).toMatchObject({ skipped: expect.arrayContaining(["first-chat"]) });
    expect(calls.some((c) => c.url === "/api/hardware" || c.url === "/api/agents/deploy")).toBe(false);
  });

  it("a resume on the first chat with its model gone finishes instead", async () => {
    const calls = routeFetch();
    const onDone = vi.fn();
    const { agent_model: _unused, ...noModel } = atFirstChat;
    render(<OnboardingScreen onDone={onDone} resume={noModel} />);
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(progressPuts(calls).at(-1)?.body).toMatchObject({
      in_progress: false,
      skipped: expect.arrayContaining(["first-chat"]),
    });
    expect(deploys(calls)).toHaveLength(0);
  });
});
