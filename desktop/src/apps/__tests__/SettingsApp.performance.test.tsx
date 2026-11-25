import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { SettingsApp } from "../SettingsApp";
import { detectHwClass } from "@/lib/hw-detect";

// Hardware detection is the gate under test, so it is mocked outright;
// every other module (and the /auth/status fetch below) behaves for real.
vi.mock("@/lib/hw-detect", () => ({ detectHwClass: vi.fn() }));
const mockDetect = vi.mocked(detectHwClass);

function mockAdmin() {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    if (String(input).includes("/auth/status")) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            configured: true,
            authenticated: true,
            user: { username: "jay", is_admin: true },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    }
    return Promise.resolve(new Response(null, { status: 404 }));
  });
}

describe("SettingsApp performance section gating (#361)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not mount the Performance panel while hardware detection is still pending", async () => {
    mockAdmin();
    mockDetect.mockReturnValue(new Promise(() => {})); // never resolves
    render(<SettingsApp windowId="w1" section="performance" />);
    const nav = await screen.findByRole("navigation", { name: "Settings sections" });
    // Admin detection has resolved (admin-only sections are listed)...
    await waitFor(() => {
      expect(within(nav).getByText("Updates")).toBeInTheDocument();
    });
    // ...but Performance stays fail-closed: no sidebar entry, and the
    // panel must not mount in the content area either.
    expect(within(nav).queryByText("Performance")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Performance settings")).not.toBeInTheDocument();
  });

  it("mounts the Performance panel once detection reports rk3588", async () => {
    mockAdmin();
    mockDetect.mockResolvedValue("rk3588");
    render(<SettingsApp windowId="w1" section="performance" />);
    expect(await screen.findByLabelText("Performance settings")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Settings sections" });
    expect(within(nav).getByText("Performance")).toBeInTheDocument();
  });

  it("keeps the Performance panel hidden on non-RK3588 hardware", async () => {
    mockAdmin();
    mockDetect.mockResolvedValue("cpu");
    render(<SettingsApp windowId="w1" section="performance" />);
    const nav = await screen.findByRole("navigation", { name: "Settings sections" });
    await waitFor(() => {
      expect(within(nav).getByText("Updates")).toBeInTheDocument();
    });
    expect(within(nav).queryByText("Performance")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Performance settings")).not.toBeInTheDocument();
  });
});
