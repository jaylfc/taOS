import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { UpdatesPanel } from "./UpdatesPanel";

const jResp = (b: any) => Promise.resolve({ ok: true, json: async () => b } as any);

beforeEach(() => {
  global.fetch = vi.fn(async (url: string) => {
    if (url === "/api/preferences/auto-update") return jResp({ check_enabled: true });
    if (url === "/api/settings/update-check") return jResp({ has_updates: false, current_commit: "abc x" });
    if (url === "/api/settings/update-status") return jResp({ current_sha: "abc", pending_restart_sha: null });
    if (url === "/api/settings/branches") return jResp({ branches: ["master", "dev"], current: "dev" });
    if (url === "/api/settings/update-channel") return jResp({ status: "switching", branch: "master" });
    return jResp({});
  }) as any;
});

describe("UpdatesPanel — branch selector", () => {
  it("hides the branch selector until Advanced is expanded", async () => {
    render(<UpdatesPanel />);
    expect(screen.queryByRole("combobox", { name: /branch/i })).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: /advanced/i }));
    await waitFor(() => expect(screen.getByRole("combobox", { name: /branch/i })).toBeInTheDocument());
  });

  it("requires confirm before posting a switch", async () => {
    render(<UpdatesPanel />);
    fireEvent.click(await screen.findByRole("button", { name: /advanced/i }));
    const select = await screen.findByRole("combobox", { name: /branch/i });
    fireEvent.change(select, { target: { value: "master" } });
    fireEvent.click(screen.getByRole("button", { name: /switch branch/i }));
    expect((global.fetch as any).mock.calls.find((c: any[]) => c[0] === "/api/settings/update-channel")).toBeUndefined();
    fireEvent.click(await screen.findByRole("button", { name: /^confirm/i }));
    await waitFor(() =>
      expect((global.fetch as any).mock.calls.find((c: any[]) => c[0] === "/api/settings/update-channel")).toBeTruthy()
    );
  });
});
