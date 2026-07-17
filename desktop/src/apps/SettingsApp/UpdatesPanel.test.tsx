import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { UpdatesPanel } from "./UpdatesPanel";

const jResp = (b: any) => Promise.resolve({ ok: true, json: async () => b } as any);

const BASE_FETCH = async (url: string) => {
  if (url === "/api/preferences/auto-update") return jResp({ check_enabled: true });
  if (url === "/api/settings/update-check")
    return jResp({ has_updates: false, current_version: "1.0.0-beta.2", current_commit: "abc x" });
  if (url === "/api/settings/update-status") return jResp({ current_sha: "abc", pending_restart_sha: null });
  if (url === "/api/settings/branches") return jResp({ branches: ["master", "dev"], current: "dev" });
  if (url === "/api/settings/update-channel") return jResp({ status: "switching", branch: "master" });
  if (url === "/api/apps/optional/catalog") return jResp({ apps: [] });
  return jResp({});
};

beforeEach(() => {
  global.fetch = vi.fn(BASE_FETCH) as any;
});

describe("UpdatesPanel -- version display", () => {
  it("shows current version prominently when up to date", async () => {
    render(<UpdatesPanel />);
    await waitFor(() => expect(screen.getByText("1.0.0-beta.2")).toBeInTheDocument());
  });

  it("shows available version when an update is present", async () => {
    (global.fetch as any) = vi.fn(async (url: string) => {
      if (url === "/api/preferences/auto-update") return jResp({ check_enabled: true });
      if (url === "/api/settings/update-check")
        return jResp({
          has_updates: true,
          current_version: "1.0.0-beta.2",
          new_version: "1.0.0-beta.3",
          current_commit: "abc defg",
          new_commit: "xyz uvwx",
        });
      if (url === "/api/settings/update-status") return jResp({ current_sha: "abc", pending_restart_sha: null });
      if (url === "/api/apps/optional/catalog") return jResp({ apps: [] });
      return jResp({});
    });
    render(<UpdatesPanel />);
    await waitFor(() => expect(screen.getByText("1.0.0-beta.2")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("1.0.0-beta.3")).toBeInTheDocument());
  });
});

describe("UpdatesPanel -- release channel selector", () => {
  it("shows Stable and Beta channel radio buttons at the top level", async () => {
    render(<UpdatesPanel />);
    await waitFor(() => expect(screen.getByRole("radio", { name: "Stable" })).toBeInTheDocument());
    expect(screen.getByRole("radio", { name: "Beta" })).toBeInTheDocument();
  });

  it("shows current branch indicator", async () => {
    render(<UpdatesPanel />);
    // The branch info is fetched when Advanced opens. Current is dev per BASE_FETCH.
    // Open Advanced first to trigger the branch fetch.
    fireEvent.click(await screen.findByRole("button", { name: /advanced/i }));
    // "dev" appears in both the top-level Current indicator and the Advanced section
    await waitFor(() => expect(screen.getAllByText("dev").length).toBeGreaterThanOrEqual(1));
  });

  it("requires confirm before posting a channel switch", async () => {
    render(<UpdatesPanel />);
    // Open Advanced to trigger branch fetch (needed for switch button to enable)
    fireEvent.click(await screen.findByRole("button", { name: /advanced/i }));
    // Wait for branches to load (dev appears in both Current indicators)
    await waitFor(() => expect(screen.getAllByText("dev").length).toBeGreaterThanOrEqual(1));

    // Select Stable (master) — it's different from current (dev), so button enables
    fireEvent.click(screen.getByRole("radio", { name: "Stable" }));
    fireEvent.click(screen.getByRole("button", { name: /switch channel/i }));
    expect((global.fetch as any).mock.calls.find((c: any[]) => c[0] === "/api/settings/update-channel")).toBeUndefined();
    // Confirm the dialog
    fireEvent.click(await screen.findByRole("button", { name: /^confirm/i }));
    await waitFor(() =>
      expect((global.fetch as any).mock.calls.find((c: any[]) => c[0] === "/api/settings/update-channel")).toBeTruthy()
    );
  });

  it("shows confirm dialog with channel label", async () => {
    render(<UpdatesPanel />);
    fireEvent.click(await screen.findByRole("button", { name: /advanced/i }));
    await waitFor(() => expect(screen.getAllByText("dev").length).toBeGreaterThanOrEqual(1));

    fireEvent.click(screen.getByRole("radio", { name: "Stable" }));
    fireEvent.click(screen.getByRole("button", { name: /switch channel/i }));
    // Dialog should show "Switch channel?" title
    await waitFor(() => expect(screen.getByText("Switch channel?")).toBeInTheDocument());
    // The dialog mentions the selected channel
    const dialogText = screen.getByRole("dialog").textContent ?? "";
    expect(dialogText).toContain("Stable");
    expect(dialogText).toContain("master");
  });

  it("switch button is disabled when on current channel", async () => {
    (global.fetch as any) = vi.fn(async (url: string) => {
      if (url === "/api/preferences/auto-update") return jResp({ check_enabled: true });
      if (url === "/api/settings/update-check")
        return jResp({ has_updates: false, current_version: "1.0.0-beta.2", current_commit: "abc x" });
      if (url === "/api/settings/update-status") return jResp({ current_sha: "abc", pending_restart_sha: null });
      if (url === "/api/settings/branches") return jResp({ branches: ["master", "dev"], current: "master" });
      if (url === "/api/apps/optional/catalog") return jResp({ apps: [] });
      return jResp({});
    });
    render(<UpdatesPanel />);
    fireEvent.click(await screen.findByRole("button", { name: /advanced/i }));
    await waitFor(() => expect(screen.getAllByText("master").length).toBeGreaterThanOrEqual(1));
    // Current is master; Stable (master) should be selected and button disabled
    const btn = screen.getByRole("button", { name: /switch channel/i });
    expect(btn).toBeDisabled();
  });

  it("custom branch input is behind Advanced disclosure", async () => {
    render(<UpdatesPanel />);
    // Custom branch input should not be visible until Advanced is expanded
    expect(screen.queryByRole("textbox", { name: /custom branch/i })).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: /advanced/i }));
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: /custom branch/i })).toBeInTheDocument()
    );
  });

  it("custom branch input clears channel selection", async () => {
    render(<UpdatesPanel />);
    fireEvent.click(await screen.findByRole("button", { name: /advanced/i }));
    await waitFor(() => expect(screen.getAllByText("dev").length).toBeGreaterThanOrEqual(1));

    // Type a custom branch
    const input = screen.getByRole("textbox", { name: /custom branch/i });
    fireEvent.change(input, { target: { value: "feature/test" } });
    // Stable radio should be unchecked
    const stableRadio = screen.getByRole("radio", { name: "Stable" }) as HTMLInputElement;
    expect(stableRadio.checked).toBe(false);
  });
});

describe("UpdatesPanel -- optional apps section", () => {
  it("renders nothing in the Apps section when no optional apps are installed", async () => {
    (global.fetch as any) = vi.fn(async (url: string) => {
      if (url === "/api/apps/optional/catalog") return jResp({ apps: [
        { id: "reddit", version: "1.0.0", trust: "first-party", source: "core", installed: false, update_available: false },
      ]});
      return jResp(await BASE_FETCH(url).then((r) => r.json()));
    });
    render(<UpdatesPanel />);
    await waitFor(() => {
      expect(screen.getByText("No optional apps installed.")).toBeInTheDocument();
    });
  });

  it("renders an installed app row with its version and Core badge", async () => {
    (global.fetch as any) = vi.fn(async (url: string) => {
      if (url === "/api/apps/optional/catalog") return jResp({ apps: [
        { id: "reddit", version: "1.0.0", trust: "first-party", source: "core", installed: true, update_available: false },
      ]});
      return jResp(await BASE_FETCH(url).then((r) => r.json()));
    });
    render(<UpdatesPanel />);
    await waitFor(() => expect(screen.getByText("Reddit")).toBeInTheDocument());
    expect(screen.getByText("1.0.0")).toBeInTheDocument();
    expect(screen.getByText("Core")).toBeInTheDocument();
    expect(screen.getByText("Up to date")).toBeInTheDocument();
  });

  it("shows update available copy when catalog flags it", async () => {
    (global.fetch as any) = vi.fn(async (url: string) => {
      if (url === "/api/apps/optional/catalog") return jResp({ apps: [
        { id: "reddit", version: "1.0.0", trust: "first-party", source: "core", installed: true, update_available: true },
      ]});
      return jResp(await BASE_FETCH(url).then((r) => r.json()));
    });
    render(<UpdatesPanel />);
    await waitFor(() =>
      expect(screen.getByText(/Update available, included in the next system update/i)).toBeInTheDocument()
    );
    // Should have a button/link to trigger the system update flow.
    expect(screen.getByRole("button", { name: /scroll to system update/i })).toBeInTheDocument();
  });
});
