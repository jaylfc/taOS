import { render, screen, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { TasksApp } from "./TasksApp";

describe("TasksApp refresh-on-focus failure handling", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("keeps the loaded routines when a focus refetch fails", async () => {
    let tasksFail = false;
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url === "/api/tasks" && tasksFail) {
        return Promise.resolve({
          ok: false,
          status: 503,
          headers: { get: () => "text/html" },
          json: () => Promise.reject(new Error("not json")),
          text: () => Promise.resolve("gateway down"),
        });
      }
      if (url === "/api/tasks") {
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: { get: () => "application/json" },
          json: () =>
            Promise.resolve([
              {
                id: 1,
                name: "Nightly backup",
                agent_name: null,
                schedule: "0 3 * * *",
                command: "backup.sh",
                description: "",
                enabled: true,
                last_run: null,
              },
            ]),
          text: () => Promise.resolve(""),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: () => Promise.resolve([]),
        text: () => Promise.resolve(""),
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<TasksApp windowId="w1" />);
    await act(async () => {
      await Promise.resolve();
    });

    // The routine the user is looking at.
    expect(screen.getByText("Nightly backup")).toBeTruthy();

    // Backend blips, then the window regains focus.
    tasksFail = true;
    window.dispatchEvent(new Event("focus"));
    await act(async () => {
      vi.advanceTimersByTime(1100);
      await Promise.resolve();
    });

    // A failed background refetch must not replace real data with the
    // "No scheduled routines" empty state.
    expect(screen.queryByText("No scheduled routines")).toBeNull();
    expect(screen.getByText("Nightly backup")).toBeTruthy();
  });
});
