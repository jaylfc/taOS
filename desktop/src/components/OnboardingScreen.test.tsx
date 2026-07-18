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

describe("OnboardingScreen username step (slice 5)", () => {
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

// The free-username-claim step is gated off in OnboardingScreen until its
// backend route (/api/account/username) lands (#141). The UsernameStep code and
// these tests are kept as-is so the flow can be re-enabled by restoring
// setStep("username"); they are skipped meanwhile so CI stays green.
describe.skip("OnboardingScreen username step (gated off, #141)", () => {
  afterEach(() => { vi.restoreAllMocks(); });
  beforeEach(() => { vi.restoreAllMocks(); });

  it("labels the username step as free and defers the paid subdomain upsell to Settings", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(okJson({ ok: true }));
    render(<OnboardingScreen onDone={() => {}} />);
    await completeAccountStep();
    expect(await screen.findByText("Claim your free taOS username")).toBeInTheDocument();
    // Free, no cost, no taOSgo gating in this step.
    expect(screen.getByText(/Your username is free/i)).toBeInTheDocument();
    expect(screen.queryByText(/taOSgo/i)).not.toBeInTheDocument();
    // Subdomain publishing is deferred to Settings rather than upsold here.
    expect(screen.getByText(/later in Settings/i)).toBeInTheDocument();
    expect(screen.queryByText(/taos\.my/i)).not.toBeInTheDocument();
  });

  it("claims the username via POST /api/account/username and then finishes", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      calls.push(url);
      if (url.includes("/api/account/username")) {
        return Promise.resolve(okJson({ username: "myname" }));
      }
      return Promise.resolve(okJson({ ok: true }));
    });
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} />);
    await completeAccountStep();
    const input = await screen.findByLabelText("taOS username");
    fireEvent.change(input, { target: { value: "myname" } });
    fireEvent.click(screen.getByRole("button", { name: "Claim username" }));
    expect(await screen.findByText(/You're @myname on taOS/i)).toBeInTheDocument();
    const claimCall = calls.find((c) => c.includes("/api/account/username"));
    expect(claimCall).toBeDefined();
    const claimInit = (globalThis.fetch as unknown as vi.Mock).mock.calls.find((c) =>
      String(c[0]).includes("/api/account/username"),
    )?.[1] as RequestInit | undefined;
    expect(claimInit?.method).toBe("POST");
    expect(JSON.parse(String(claimInit?.body))).toEqual({ username: "myname" });
    fireEvent.click(screen.getByRole("button", { name: "Finish" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
  });

  it("never dead-ends: Finish proceeds even when the claim fails", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/api/account/username")) {
        // Service unreachable -> the step must still let the user finish.
        return Promise.reject(new Error("network"));
      }
      return Promise.resolve(okJson({ ok: true }));
    });
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} />);
    await completeAccountStep();
    fireEvent.click(await screen.findByRole("button", { name: "Claim username" }));
    expect(await screen.findByText(/later in Settings/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Finish" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
  });

  it("finishes without claiming when the user skips", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(okJson({ ok: true }));
    const onDone = vi.fn();
    render(<OnboardingScreen onDone={onDone} />);
    await completeAccountStep();
    await screen.findByText("Claim your free taOS username");
    fireEvent.click(screen.getByRole("button", { name: "Finish" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
  });
});
