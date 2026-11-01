import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { OnboardingScreen } from "./OnboardingScreen";

const okJson = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

/** Fill and submit the first-run account setup form. */
async function completeAccountStep() {
  fireEvent.change(screen.getByLabelText(/^Username/), {
    target: { value: "jay" },
  });
  fireEvent.change(screen.getByLabelText(/^Full name/), {
    target: { value: "Jay Doe" },
  });
  fireEvent.change(screen.getByLabelText(/^Email/), {
    target: { value: "jay@example.com" },
  });
  fireEvent.change(screen.getByLabelText(/^Password/), {
    target: { value: "1234" },
  });
  fireEvent.change(screen.getByLabelText(/Confirm password/), {
    target: { value: "1234" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Get started" }));
}

describe("OnboardingScreen", () => {
  afterEach(() => { vi.restoreAllMocks(); });
  beforeEach(() => { vi.restoreAllMocks(); });

  it("renders the first-run account setup form", () => {
    render(<OnboardingScreen onDone={() => {}} />);
    expect(screen.getByText("Welcome to taOS")).toBeInTheDocument();
    expect(screen.getByLabelText(/^Username/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Full name/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Email/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Password/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Get started" })).toBeInTheDocument();
  });

  it("advances to the optional taOS account step after account creation", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(okJson({ ok: true }));
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} />);
    await completeAccountStep();
    await waitFor(() => expect(screen.getByText("Your taOS account")).toBeInTheDocument());
    expect(onDone).not.toHaveBeenCalled();
    // The email from the local step is carried over.
    expect(screen.getByLabelText(/^Email/)).toHaveValue("jay@example.com");
  });

  it("skipping the taOS account step finishes onboarding with no account call", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(okJson({ ok: true }));
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} />);
    await completeAccountStep();
    fireEvent.click(await screen.findByRole("button", { name: "Skip for now" }));
    expect(onDone).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls.map((c) => String(c[0]))).toEqual(["/auth/setup"]);
  });

  it("signing in to taos.my finishes onboarding", async () => {
    const account = { user_id: "u-1", email: "jay@example.com", taosgo: { status: "none" } };
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(okJson({ ok: true }))          // /auth/setup
      .mockResolvedValueOnce(okJson({ user_id: "u-1" }))    // /api/account/login
      .mockResolvedValueOnce(okJson(account));              // /api/account/me
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} />);
    await completeAccountStep();
    fireEvent.change(await screen.findByLabelText(/taOS account password/), {
      target: { value: "longenough" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in to taOS" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
  });

  it("a wrong taos.my password shows an error and stays on the step", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(okJson({ ok: true }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "invalid_credentials" }), { status: 401 }),
      );
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} />);
    await completeAccountStep();
    fireEvent.change(await screen.findByLabelText(/taOS account password/), {
      target: { value: "longenough" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in to taOS" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That email and password don't match a taOS account.",
    );
    expect(onDone).not.toHaveBeenCalled();
  });

  it("closed sign-ups show a plain notice, not an error, and Skip still works", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(okJson({ ok: true }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "signups_closed" }), { status: 403 }),
      );
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} />);
    await completeAccountStep();
    fireEvent.click(await screen.findByRole("button", { name: "Create account" }));
    fireEvent.change(screen.getByLabelText(/taOS account password/), {
      target: { value: "longenough" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create taOS account" }));
    expect(await screen.findByRole("status")).toHaveTextContent("aren't open yet");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
    expect(onDone).toHaveBeenCalledOnce();
  });

  it("taos.my unreachable shows an error and Skip still works", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(okJson({ ok: true }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: "account service unreachable" }), { status: 503 }),
      );
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} />);
    await completeAccountStep();
    fireEvent.change(await screen.findByLabelText(/taOS account password/), {
      target: { value: "longenough" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in to taOS" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not available yet");
    fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
    expect(onDone).toHaveBeenCalledOnce();
  });
});
