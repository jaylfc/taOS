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
      if (url === "/api/agents/invites") {
        return Promise.resolve(ok({ invite_id: "482913", pin: "7788" }));
      }
      if (url === "/api/projects/prj_a/invites") {
        return Promise.resolve(ok([]));
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

  it("mints an OS-level invite (no project) with the entered alias and shows URL + PIN", async () => {
    await act(async () => {
      render(<RegistryPanel />);
    });
    fireEvent.click(screen.getByRole("button", { name: /Agent Registry/ }));
    fireEvent.click(
      await screen.findByRole("button", { name: /Invite external agent/i }),
    );

    // The project select defaults to the "None" option.
    const projectSelect = await screen.findByRole("combobox", { name: /Project to invite into/i });
    expect((projectSelect as HTMLSelectElement).value).toBe("");
    expect(screen.getByRole("option", { name: /None — available in chat/i })).toBeInTheDocument();

    // Enter an alias.
    fireEvent.change(screen.getByRole("textbox", { name: /Agent name or alias/i }), {
      target: { value: "Scout" },
    });

    // Mint → posts to the OS-level endpoint with the alias, no project.
    fireEvent.click(screen.getByRole("button", { name: /Mint invite/i }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]: [string]) => url === "/api/agents/invites"),
      ).toBe(true),
    );
    const call = fetchMock.mock.calls.find(([url]: [string]) => url === "/api/agents/invites");
    expect(call?.[1]?.method).toBe("POST");
    const body = JSON.parse(call?.[1]?.body as string);
    expect(body.display_name).toBe("Scout");
    expect(body.scopes).toContain("a2a_send");

    // The result view shows the redeemable URL + PIN.
    await waitFor(() => expect(screen.getByLabelText(/Invite result/i)).toBeInTheDocument());
    // The invite id appears inside the /i/<id> URL; the PIN stands alone.
    expect(screen.getAllByText(/482913/).length).toBeGreaterThan(0);
    expect(screen.getByText("7788")).toBeInTheDocument();
  });

  it("chains to the project-scoped mint dialog when a project is chosen", async () => {
    await act(async () => {
      render(<RegistryPanel />);
    });
    fireEvent.click(screen.getByRole("button", { name: /Agent Registry/ }));
    fireEvent.click(
      await screen.findByRole("button", { name: /Invite external agent/i }),
    );

    const projectSelect = await screen.findByRole("combobox", { name: /Project to invite into/i });
    fireEvent.change(projectSelect, { target: { value: "prj_a" } });
    fireEvent.click(screen.getByRole("button", { name: /Continue/i }));

    // The existing project-scoped invite dialog opens (fetches its pending list).
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]: [string]) => url === "/api/projects/prj_a/invites"),
      ).toBe(true),
    );
  });
});
