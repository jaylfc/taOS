import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RegistryPanel } from "../RegistryPanel";
import { projectsApi } from "@/lib/projects";

function ok(data: unknown, status = 200) {
  return { ok: true, status, json: async () => data };
}

const ACTIVE_ENTRY = {
  canonical_id: "agent:free-builder@taos",
  framework: "builder",
  display_name: "@free-builder",
  user_id: "u_admin",
  origin: "taos",
  handle: "@free-builder",
  role: null,
  capabilities: ["build"],
  status: "active",
  registered_at: new Date().toISOString(),
  updated_at: null,
  revoked_at: null,
};

describe("RegistryPanel invite + assign wiring", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn((url: string) => {
      if (url === "/auth/status") {
        return Promise.resolve(ok({ user: { is_admin: true, id: "u_admin" } }));
      }
      if (url === "/api/agents/registry") {
        return Promise.resolve(ok([ACTIVE_ENTRY]));
      }
      return Promise.resolve(ok({}));
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(projectsApi, "list").mockResolvedValue([
      { id: "prj_a", name: "Alpha", slug: "alpha", description: "", status: "active", created_by: "u", created_at: 0, updated_at: 0 },
    ]);
  });

  it("renders the 'Invite external agent' button for admins", async () => {
    await act(async () => {
      render(<RegistryPanel />);
    });
    fireEvent.click(screen.getByRole("button", { name: /Agent Registry/ }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Invite external agent/i })).toBeInTheDocument(),
    );
  });

  it("renders the 'Assign to project' action for active entries", async () => {
    await act(async () => {
      render(<RegistryPanel />);
    });
    fireEvent.click(screen.getByRole("button", { name: /Agent Registry/ }));

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /Assign free-builder to project/i }),
      ).toBeInTheDocument(),
    );
  });

  it("opens the assign dialog from the row action", async () => {
    await act(async () => {
      render(<RegistryPanel />);
    });
    fireEvent.click(screen.getByRole("button", { name: /Agent Registry/ }));

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /Assign free-builder to project/i }),
      ).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: /Assign free-builder to project/i }));

    await waitFor(() =>
      expect(screen.getByRole("dialog", { name: /assign agent to project/i })).toBeInTheDocument(),
    );
    expect(screen.getByRole("option", { name: /Alpha/ })).toBeInTheDocument();
  });
});
