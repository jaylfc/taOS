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

  it("finishes onboarding after account creation (username step gated off, #141)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(okJson({ ok: true }));
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} />);
    await completeAccountStep();
    // The taos.my username-claim step is gated off until its backend exists, so
    // account creation completes onboarding directly rather than advancing.
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(screen.queryByText("Claim your free taOS username")).not.toBeInTheDocument();
  });
});
