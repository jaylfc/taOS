import { StrictMode } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/account-client", () => ({
  isAuthError: () => false,
  login: vi.fn(() => Promise.resolve({ ok: true })),
  register: vi.fn(() => Promise.resolve({ ok: true })),
}));

let resolveSave: () => void = () => {};
vi.mock("../privacy", () => ({
  formatRate: () => null,
  loadPrivacy: () => Promise.resolve({ uploadKbps: 0, providers: [], ping: { enabled: true }, sharing: { enabled: true } }),
  savePrivacy: () => new Promise<void>((r) => { resolveSave = r; }),
}));

import { AgentCloud } from "./AgentStep";
import { PrivacyStep } from "./PrivacyStep";
import { TaosAccountForm } from "./TaosAccountStep";

describe("AgentCloud", () => {
  it("clears the key when the provider changes, so it is never sent to another provider", () => {
    const onKey = vi.fn();
    const onType = vi.fn();
    render(
      <AgentCloud type="openai" apiKey="sk-secret" busy={false} error="" onType={onType} onKey={onKey} onBack={() => {}} onConnect={() => {}} />,
    );
    const other = screen.getAllByRole("radio").find((r) => r.getAttribute("aria-checked") === "false")!;
    fireEvent.click(other);
    expect(onType).toHaveBeenCalled();
    expect(onKey).toHaveBeenCalledWith("");
  });
});

describe("TaosAccountForm", () => {
  it("finishes sign-in under StrictMode, whose extra cleanup must not leave the form unmounted", async () => {
    const onDone = vi.fn();
    render(
      <StrictMode>
        <TaosAccountForm mode="signin" onDone={onDone} onBack={() => {}} onOffline={() => {}} />
      </StrictMode>,
    );
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "a@b.c" } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "longenough" } });
    await act(async () => {
      fireEvent.submit(screen.getByRole("form"));
    });
    expect(onDone).toHaveBeenCalled();
  });
});

describe("PrivacyStep", () => {
  it("offers no Back while the save is pending, so a late save cannot advance past a Back", async () => {
    render(<PrivacyStep onBack={() => {}} onNext={() => {}} />);
    await act(async () => {});
    expect(screen.getByRole("button", { name: /back/i })).toBeTruthy();
    await act(async () => {
      fireEvent.submit(screen.getByRole("form"));
    });
    expect(screen.queryByRole("button", { name: /back/i })).toBeNull();
    await act(async () => {
      resolveSave();
    });
  });
});
