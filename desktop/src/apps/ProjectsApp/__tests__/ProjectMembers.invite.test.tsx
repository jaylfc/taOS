import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { InviteAgentDialog } from "../InviteAgentDialog";

// The invite URL and PIN are shown EXACTLY ONCE and cannot be recovered.
// ProjectMembers used to close the dialog in its onMinted handler, unmounting
// it before the credentials rendered, so a successful mint looked like a
// silent failure (reported 2026-07-21).
describe("mint result survives onMinted", () => {
  it("renders the invite URL and PIN after a successful mint", async () => {
    const mint = { invite_id: "123456", pin: "4321", expires_ts: Date.now() / 1000 + 3600, scopes: [] };
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return { ok: true, json: async () => mint } as Response;
      }
      return { ok: true, json: async () => [] } as Response;
    }));

    // onMinted must NOT unmount the dialog: the parent only refreshes its list.
    const onMinted = vi.fn();
    render(<InviteAgentDialog projectId="prj-x" onClose={() => {}} onMinted={onMinted} />);

    fireEvent.click(screen.getByRole("button", { name: /mint invite/i }));

    await waitFor(() => {
      expect(screen.getByText("4321")).toBeInTheDocument();
    });
    expect(onMinted).toHaveBeenCalled();
    expect(screen.getByLabelText(/invite result/i)).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});
