import { StrictMode } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

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
import { ModelStep } from "./ModelStep";
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

describe("ModelStep", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("stops polling and shows Try Again when the download status returns 404", async () => {
    const models = {
      models: [
        {
          id: "qwen3-1.7b",
          name: "Qwen3 1.7B",
          capabilities: ["chat"],
          variants: [
            {
              id: "q4",
              format: "gguf",
              size_mb: 1200,
              compatibility: "green",
              downloaded: false,
            },
          ],
        },
      ],
      hardware_profile_id: "",
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/models")) {
        return new Response(JSON.stringify(models), { status: 200 });
      }
      if (url.endsWith("/api/models/download")) {
        return new Response(JSON.stringify({ download_id: "dl-1" }), { status: 200 });
      }
      if (url.includes("/api/models/downloads/dl-1")) {
        return new Response("", { status: 404 });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ModelStep onBack={() => {}} onDone={() => {}} onSkip={() => {}} />);
    await act(async () => {});

    vi.useFakeTimers();
    const download = screen.getByRole("button", { name: /download/i });
    await act(async () => {
      fireEvent.click(download);
    });

    expect(screen.getByText(/lost track of the download/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /try again/i })).toBeTruthy();

    const dl1Calls = () =>
      fetchMock.mock.calls.filter((c) => String(c[0]).includes("/api/models/downloads/dl-1")).length;
    const before = dl1Calls();
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    expect(dl1Calls()).toBe(before);
  });
});
