import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { AppErrorBoundary } from "../AppErrorBoundary";
import { BackendUnavailableError } from "@/lib/taos-fetch";

function Thrower({ err }: { err: Error }): JSX.Element {
  throw err;
}

describe("<AppErrorBoundary />", () => {
  let consoleErr: ReturnType<typeof vi.spyOn>;
  let originalLocation: Location;
  beforeEach(() => {
    // React logs caught errors loudly in tests; silence to keep output clean.
    consoleErr = vi.spyOn(console, "error").mockImplementation(() => {});
    originalLocation = window.location;
  });
  afterEach(() => {
    consoleErr.mockRestore();
    vi.useRealTimers();
    Object.defineProperty(window, "location", {
      value: originalLocation, writable: true, configurable: true,
    });
  });

  it("renders children when there's no error", () => {
    render(
      <AppErrorBoundary>
        <p>hello</p>
      </AppErrorBoundary>
    );
    expect(screen.getByText("hello")).toBeInTheDocument();
  });

  it("catches BackendUnavailableError and renders the waiting skeleton", () => {
    render(
      <AppErrorBoundary>
        <Thrower err={new BackendUnavailableError()} />
      </AppErrorBoundary>
    );
    expect(screen.getByText(/waiting for taOS to come back/i)).toBeInTheDocument();
  });

  it("catches ChunkLoadError and renders the friendly reload page", () => {
    const err = new Error("Loading chunk 5 failed.");
    err.name = "ChunkLoadError";
    render(
      <AppErrorBoundary>
        <Thrower err={err} />
      </AppErrorBoundary>
    );
    expect(screen.getByText(/taOS was updated/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reload/i })).toBeInTheDocument();
  });

  it("auto-reloads after 5s when ChunkLoadError is caught", () => {
    vi.useFakeTimers();
    const reload = vi.fn();
    Object.defineProperty(window, "location", {
      value: { reload }, writable: true, configurable: true,
    });
    const err = new Error("Loading chunk 5 failed.");
    err.name = "ChunkLoadError";
    render(
      <AppErrorBoundary>
        <Thrower err={err} />
      </AppErrorBoundary>
    );
    expect(reload).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(5_000); });
    expect(reload).toHaveBeenCalled();
  });

  it("rethrows other errors (does not swallow generic exceptions)", () => {
    render(
      <AppErrorBoundary>
        <Thrower err={new ReferenceError("boom")} />
      </AppErrorBoundary>
    );
    // For unknown errors the boundary still has to render *something*
    // (React requires it). It renders a minimal "Something went wrong"
    // line that does NOT match the friendly fallbacks above.
    expect(screen.queryByText(/waiting for taOS/i)).toBeNull();
    expect(screen.queryByText(/taOS was updated/i)).toBeNull();
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
  });

  it("logs the real error + component stack for a generic crash (diagnosable)", () => {
    render(
      <AppErrorBoundary>
        <Thrower err={new ReferenceError("boom")} />
      </AppErrorBoundary>
    );
    // The boundary surfaces the underlying error rather than swallowing it.
    const logged = consoleErr.mock.calls.some(
      (args) =>
        args[0] === "[AppErrorBoundary]" &&
        args[1] instanceof Error &&
        (args[1] as Error).message === "boom",
    );
    expect(logged).toBe(true);
  });

  it("does not log for the expected waiting state", () => {
    render(
      <AppErrorBoundary>
        <Thrower err={new BackendUnavailableError()} />
      </AppErrorBoundary>
    );
    const loggedOurs = consoleErr.mock.calls.some((args) => args[0] === "[AppErrorBoundary]");
    expect(loggedOurs).toBe(false);
  });
});
