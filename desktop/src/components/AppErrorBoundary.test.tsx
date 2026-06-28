import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { AppErrorBoundary } from "./AppErrorBoundary";
import { BackendUnavailableError } from "@/lib/taos-fetch";

function ThrowOnRender({ error }: { error: Error }) {
  throw error;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AppErrorBoundary", () => {
  it("renders children when no error has occurred", () => {
    render(
      <AppErrorBoundary>
        <div>app content</div>
      </AppErrorBoundary>,
    );
    expect(screen.getByText("app content")).toBeInTheDocument();
  });

  it("shows the waiting skeleton when BackendUnavailableError is caught", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <AppErrorBoundary>
        <ThrowOnRender error={new BackendUnavailableError("Backend is unavailable")} />
      </AppErrorBoundary>,
    );
    expect(screen.getByText("Waiting for taOS to come back\u2026")).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows the chunk reload page when ChunkLoadError is caught", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const err = new Error("Loading chunk 42 failed");
    err.name = "ChunkLoadError";
    render(
      <AppErrorBoundary>
        <ThrowOnRender error={err} />
      </AppErrorBoundary>,
    );
    expect(screen.getByText("taOS was updated")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
    expect(screen.getByText(/load the new version/i)).toBeInTheDocument();
  });

  it("shows the generic error message for unknown errors", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <AppErrorBoundary>
        <ThrowOnRender error={new Error("something unexpected")} />
      </AppErrorBoundary>,
    );
    expect(screen.getByText("Something went wrong.")).toBeInTheDocument();
  });

  it("reports a generic crash to the server (#106)", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 201 }));
    render(
      <AppErrorBoundary>
        <ThrowOnRender error={new Error("crash-report-unique-xyz")} />
      </AppErrorBoundary>,
    );
    const posted = fetchSpy.mock.calls.find(([url]) => url === "/api/client-logs");
    expect(posted).toBeTruthy();
    const body = JSON.parse(String((posted![1] as RequestInit)?.body));
    expect(body.level).toBe("fatal");
    expect(body.message).toBe("crash-report-unique-xyz");
    expect(body.source).toBe("AppErrorBoundary");
  });

  it("does not render children after an error is caught", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <AppErrorBoundary>
        <ThrowOnRender error={new Error("fail")} />
        <div>visible child</div>
      </AppErrorBoundary>,
    );
    expect(screen.queryByText("visible child")).not.toBeInTheDocument();
    expect(screen.getByText("Something went wrong.")).toBeInTheDocument();
  });

  it("classifies dynamically import failures as chunk errors", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const err = new Error("Failed to fetch dynamically imported module");
    render(
      <AppErrorBoundary>
        <ThrowOnRender error={err} />
      </AppErrorBoundary>,
    );
    expect(screen.getByText("taOS was updated")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
  });
});
