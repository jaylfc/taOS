import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Project } from "@/lib/projects";
import { ProjectRoutines } from "../ProjectRoutines";

const fakeProject: Project = {
  id: "p1",
  slug: "p1",
  name: "P1",
  description: "",
  status: "active",
  created_by: "u1",
  created_at: 0,
  updated_at: 0,
};

function ok(data: unknown) {
  return { ok: true, status: 200, json: async () => data };
}

describe("ProjectRoutines", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn((url: string) => {
      if (url.includes("/routines") && !url.includes("/trigger")) {
        return Promise.resolve(ok({ items: [] }));
      }
      if (url.includes("/members")) {
        return Promise.resolve(ok({ items: [] }));
      }
      return Promise.resolve(ok({}));
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  it("shows an empty state when the project has no routines", async () => {
    await act(async () => {
      render(<ProjectRoutines project={fakeProject} />);
    });
    expect(await screen.findByText(/no routines for this project yet/i)).toBeInTheDocument();
  });

  it("renders an existing cron routine with its schedule", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/routines") && !url.includes("/trigger")) {
        return Promise.resolve(
          ok({
            items: [
              {
                id: "rtn-1",
                project_id: "p1",
                title: "Nightly sweep",
                body_template: "",
                assignee_id: null,
                trigger_kind: "cron",
                cron_expr: "0 3 * * *",
                webhook_token: null,
                enabled: 1,
                last_fired: null,
                next_fire: Date.now() / 1000 + 3600,
                created_by: "u1",
                created_at: 0,
                updated_at: 0,
              },
            ],
          }),
        );
      }
      return Promise.resolve(ok({ items: [] }));
    });

    await act(async () => {
      render(<ProjectRoutines project={fakeProject} />);
    });
    expect(await screen.findByText("Nightly sweep")).toBeInTheDocument();
    expect(screen.getByText("0 3 * * *")).toBeInTheDocument();
  });

  it("submits the create form and refreshes the list", async () => {
    let created = false;
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/routines") && init?.method === "POST") {
        created = true;
        return Promise.resolve(ok({ id: "rtn-new", trigger_kind: "cron" }));
      }
      if (url.includes("/routines")) {
        return Promise.resolve(
          ok({ items: created ? [{ id: "rtn-new", title: "New routine", trigger_kind: "cron", cron_expr: "0 3 * * *", enabled: 1 }] : [] }),
        );
      }
      return Promise.resolve(ok({ items: [] }));
    });

    await act(async () => {
      render(<ProjectRoutines project={fakeProject} />);
    });

    fireEvent.change(screen.getByLabelText(/routine title/i), { target: { value: "New routine" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /create routine/i }));
    });

    await waitFor(() => expect(created).toBe(true));
    expect(await screen.findByText("New routine")).toBeInTheDocument();
  });

  it("applies a cron preset when clicked", async () => {
    await act(async () => {
      render(<ProjectRoutines project={fakeProject} />);
    });
    fireEvent.click(await screen.findByRole("button", { name: /daily at 3am/i }));
    expect(screen.getByLabelText(/cron expression/i)).toHaveValue("0 3 * * *");
  });

  it("calls the trigger endpoint when Run now is clicked", async () => {
    let triggered = false;
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/trigger")) {
        triggered = true;
        return Promise.resolve(ok({ ok: true, task: { id: "tsk-1" } }));
      }
      if (url.includes("/routines")) {
        return Promise.resolve(
          ok({
            items: [
              {
                id: "rtn-1",
                title: "Nightly sweep",
                trigger_kind: "api",
                cron_expr: null,
                webhook_token: null,
                enabled: 1,
                last_fired: null,
                next_fire: null,
                assignee_id: null,
              },
            ],
          }),
        );
      }
      return Promise.resolve(ok({ items: [] }));
    });

    await act(async () => {
      render(<ProjectRoutines project={fakeProject} />);
    });
    await screen.findByText("Nightly sweep");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /run now/i }));
    });
    await waitFor(() => expect(triggered).toBe(true));
  });
});
