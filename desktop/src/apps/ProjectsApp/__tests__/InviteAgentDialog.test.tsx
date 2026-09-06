import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { InviteAgentDialog } from "../InviteAgentDialog";

function ok(data: unknown, status = 200) {
  return { ok: true, status, json: async () => data };
}

const PID = "prj_test";

describe("InviteAgentDialog", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let mintBody: Record<string, unknown> | null = null;

  beforeEach(() => {
    mintBody = null;
    fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url === `/api/projects/${PID}/invites` && init?.method === "POST") {
        const parsed = JSON.parse(String(init.body));
        mintBody = parsed;
        return Promise.resolve(
          ok({
            invite_id: "482910",
            pin: "4821",
            expires_ts: Date.now() / 1000 + 900,
            scopes: parsed.scopes,
            approval_mode: parsed.approval_mode,
            check_interval_secs: parsed.check_interval_secs,
          }),
        );
      }
      if (url === `/api/projects/${PID}/invites`) {
        return Promise.resolve(ok([]));
      }
      return Promise.resolve(ok({}));
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  it("always includes project_tasks in the mint body even though the checkbox is disabled", async () => {
    await act(async () => {
      render(<InviteAgentDialog projectId={PID} onClose={() => {}} onMinted={() => {}} />);
    });
    fireEvent.click(screen.getByRole("button", { name: /mint invite/i }));

    await waitFor(() => expect(mintBody).not.toBeNull());
    expect(mintBody!.scopes).toContain("project_tasks");
    // project_tasks must be present even if the operator toggled nothing else.
    const calls = fetchMock.mock.calls.filter(
      (c) => c[0] === `/api/projects/${PID}/invites` && (c[1] as RequestInit)?.method === "POST",
    );
    expect(calls.length).toBe(1);
    expect(JSON.parse(String((calls[0]![1] as RequestInit).body))).toMatchObject({
      scopes: expect.arrayContaining(["project_tasks"]),
    });
  });

  it("adds 'lead' to the posted scopes when the Lead toggle is on", async () => {
    await act(async () => {
      render(<InviteAgentDialog projectId={PID} onClose={() => {}} onMinted={() => {}} />);
    });
    fireEvent.click(screen.getByLabelText(/make this agent the project lead/i));
    fireEvent.click(screen.getByRole("button", { name: /mint invite/i }));

    await waitFor(() => expect(mintBody).not.toBeNull());
    expect(mintBody!.scopes).toContain("lead");
    expect(mintBody!.scopes).toContain("project_tasks");
  });

  it("submits the manual-approval toggle and interval control values", async () => {
    await act(async () => {
      render(<InviteAgentDialog projectId={PID} onClose={() => {}} onMinted={() => {}} />);
    });
    fireEvent.click(screen.getByLabelText(/require manual approval/i));
    // pick the 5m preset to change the interval
    fireEvent.click(screen.getByRole("button", { name: /Set interval 5m/ }));
    fireEvent.click(screen.getByRole("button", { name: /mint invite/i }));

    await waitFor(() => expect(mintBody).not.toBeNull());
    expect(mintBody!.approval_mode).toBe("manual");
    expect(mintBody!.check_interval_secs).toBe(300);
  });

  it("shows the URL and PIN LARGE plus the copy instruction after mint", async () => {
    await act(async () => {
      render(<InviteAgentDialog projectId={PID} onClose={() => {}} onMinted={() => {}} />);
    });
    fireEvent.click(screen.getByRole("button", { name: /mint invite/i }));

    await waitFor(() => expect(screen.getByText("4821")).toBeInTheDocument());
    const expectedUrl = `${window.location.origin}/i/482910`;
    expect(screen.getByText(expectedUrl)).toBeInTheDocument();
    expect(
      screen.getByText(
        new RegExp(
          `Fetch ${expectedUrl.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} and redeem with PIN 4821; follow the returned JSON instructions to join the taOS project\\.`,
        ),
      ),
    ).toBeInTheDocument();
  });

  it("calls DELETE /api/projects/{pid}/invites/{invite_id} on revoke", async () => {
    let deleteCalled = false;
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === `/api/projects/${PID}/invites` && init?.method === "POST") {
        return Promise.resolve(
          ok({ invite_id: "482910", pin: "4821", expires_ts: Date.now() / 1000 + 900, scopes: ["project_tasks"], approval_mode: "auto", check_interval_secs: 1800 }),
        );
      }
      if (url === `/api/projects/${PID}/invites` && !init?.method) {
        return Promise.resolve(
          ok([
            {
              invite_id: "482910",
              scopes: ["project_tasks"],
              status: "pending",
              expires_ts: Date.now() / 1000 + 900,
              redeemed_by: null,
            },
          ]),
        );
      }
      if (url === `/api/projects/${PID}/invites/482910` && init?.method === "DELETE") {
        deleteCalled = true;
        return Promise.resolve(ok(null, 204));
      }
      return Promise.resolve(ok({}));
    });

    await act(async () => {
      render(<InviteAgentDialog projectId={PID} onClose={() => {}} onMinted={() => {}} />);
    });
    await waitFor(() => expect(screen.getByText("482910")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /revoke/i }));

    await waitFor(() => expect(deleteCalled).toBe(true));
  });
});
