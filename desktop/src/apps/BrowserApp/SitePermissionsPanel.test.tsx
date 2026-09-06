import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { SitePermissionsPanel } from "./SitePermissionsPanel";
import * as sitePermsApi from "@/lib/browser-site-permissions-api";

vi.mock("@/lib/browser-site-permissions-api");

const PROFILE_ID = "prof-1";

function makeGrant(
  overrides: Partial<sitePermsApi.SitePermissionGrant> = {},
): sitePermsApi.SitePermissionGrant {
  return {
    host_pattern: "*.example.com",
    permission: "geolocation",
    state: "allow",
    ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(sitePermsApi.listSitePermissions).mockResolvedValue([]);
  vi.mocked(sitePermsApi.revokeSitePermission).mockResolvedValue(true);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("SitePermissionsPanel", () => {
  it("shows loading state initially", async () => {
    let resolve!: (v: sitePermsApi.SitePermissionGrant[]) => void;
    vi.mocked(sitePermsApi.listSitePermissions).mockReturnValue(
      new Promise((r) => { resolve = r; }),
    );

    render(<SitePermissionsPanel profileId={PROFILE_ID} onClose={vi.fn()} />);
    expect(screen.getByText(/loading/i)).toBeTruthy();
    resolve([]);
  });

  it("renders one row per permission grant", async () => {
    vi.mocked(sitePermsApi.listSitePermissions).mockResolvedValue([
      makeGrant({ host_pattern: "foo.com", permission: "camera" }),
      makeGrant({ host_pattern: "bar.com", permission: "notifications" }),
    ]);

    render(<SitePermissionsPanel profileId={PROFILE_ID} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText("foo.com"));
    expect(screen.getByText("bar.com")).toBeTruthy();
    expect(screen.getByText("camera")).toBeTruthy();
    expect(screen.getByText("notifications")).toBeTruthy();
  });

  it("shows empty-state message when no grants", async () => {
    vi.mocked(sitePermsApi.listSitePermissions).mockResolvedValue([]);

    render(<SitePermissionsPanel profileId={PROFILE_ID} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText(/no site permissions yet/i));
  });

  it("revoke button calls revokeSitePermission with host_pattern and permission", async () => {
    vi.mocked(sitePermsApi.listSitePermissions).mockResolvedValue([
      makeGrant({ host_pattern: "secure.com", permission: "geolocation" }),
    ]);
    vi.mocked(sitePermsApi.revokeSitePermission).mockResolvedValue(true);
    // After revoke the list comes back empty
    vi.mocked(sitePermsApi.listSitePermissions)
      .mockResolvedValueOnce([makeGrant({ host_pattern: "secure.com", permission: "geolocation" })])
      .mockResolvedValueOnce([]);

    render(<SitePermissionsPanel profileId={PROFILE_ID} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText("secure.com"));

    const revokeBtn = screen.getByRole("button", { name: /revoke geolocation on secure\.com/i });
    await act(async () => {
      fireEvent.click(revokeBtn);
    });

    expect(sitePermsApi.revokeSitePermission).toHaveBeenCalledWith(
      PROFILE_ID,
      "secure.com",
      "geolocation",
    );
    await waitFor(() => screen.getByText(/no site permissions yet/i));
  });

  it("revoke failure shows inline error", async () => {
    vi.mocked(sitePermsApi.listSitePermissions).mockResolvedValue([
      makeGrant({ host_pattern: "fail.com", permission: "camera" }),
    ]);
    vi.mocked(sitePermsApi.revokeSitePermission).mockResolvedValue(false);

    render(<SitePermissionsPanel profileId={PROFILE_ID} onClose={vi.fn()} />);
    await waitFor(() => screen.getByText("fail.com"));

    const revokeBtn = screen.getByRole("button", { name: /revoke camera on fail\.com/i });
    await act(async () => {
      fireEvent.click(revokeBtn);
    });

    await waitFor(() => screen.getByText(/failed to revoke/i));
  });

  it("Esc closes via onClose", async () => {
    vi.mocked(sitePermsApi.listSitePermissions).mockResolvedValue([]);
    const onClose = vi.fn();

    render(<SitePermissionsPanel profileId={PROFILE_ID} onClose={onClose} />);
    await waitFor(() => screen.getByRole("dialog"));

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("backdrop click closes via onClose", async () => {
    vi.mocked(sitePermsApi.listSitePermissions).mockResolvedValue([]);
    const onClose = vi.fn();

    render(<SitePermissionsPanel profileId={PROFILE_ID} onClose={onClose} />);
    const backdrop = await waitFor(() => screen.getByRole("dialog").parentElement!);

    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("role=dialog + aria-modal + aria-label present", async () => {
    vi.mocked(sitePermsApi.listSitePermissions).mockResolvedValue([]);

    render(<SitePermissionsPanel profileId={PROFILE_ID} onClose={vi.fn()} />);
    const dialog = await waitFor(() => screen.getByRole("dialog"));
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-label")).toMatch(/site permissions/i);
  });
});
