import { describe, expect, it, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ProfileManager } from "./ProfileManager";
import { useBrowserStore } from "@/stores/browser-store";

const originalFetch = global.fetch;

function mockListResponse(profiles: any[]) {
  return {
    ok: true,
    status: 200,
    json: async () => ({ profiles }),
  };
}

beforeEach(() => {
  global.fetch = vi.fn().mockResolvedValue(
    mockListResponse([
      { profile_id: "personal", name: "Personal", color: "#6c8df0", created_at: 0 },
      { profile_id: "work", name: "Work", color: "#f5b86b", created_at: 0 },
    ]),
  );
});

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("ProfileManager — list view", () => {
  it("renders all profiles after loading", async () => {
    render(
      <ProfileManager
        activeProfileId="personal"
        onClose={() => {}}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText("Personal")).toBeTruthy();
      expect(screen.getByText("Work")).toBeTruthy();
    });
  });

  it("each profile row has rename + delete buttons", async () => {
    render(
      <ProfileManager
        activeProfileId="personal"
        onClose={() => {}}
      />,
    );
    await waitFor(() => {
      const rename = screen.getAllByLabelText(/rename/i);
      expect(rename.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("delete button on the active profile is disabled", async () => {
    render(
      <ProfileManager
        activeProfileId="personal"
        onClose={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("Personal"));

    const deleteBtns = screen.getAllByLabelText(/delete profile/i);
    // Find the one inside the Personal row
    const personalRow = screen.getByText("Personal").closest('[role="listitem"], li, div');
    const deleteBtn = deleteBtns.find((b) => personalRow?.contains(b)) as HTMLButtonElement;
    expect(deleteBtn?.disabled).toBe(true);
  });
});

describe("ProfileManager — create flow", () => {
  it("Add profile form takes name + color, posts to backend", async () => {
    let postBody: any = null;
    global.fetch = vi.fn().mockImplementation((url, opts) => {
      if (opts?.method === "POST") {
        postBody = JSON.parse(opts.body as string);
        return Promise.resolve({
          ok: true,
          json: async () => ({
            profile_id: "research",
            name: postBody.name,
            color: postBody.color,
            created_at: 0,
          }),
        });
      }
      // Default: list returns initial 2 profiles
      return Promise.resolve(mockListResponse([
        { profile_id: "personal", name: "Personal", color: "#6c8df0", created_at: 0 },
        { profile_id: "work", name: "Work", color: "#f5b86b", created_at: 0 },
      ]));
    });

    render(
      <ProfileManager
        activeProfileId="personal"
        onClose={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("Personal"));

    // Open the add form
    fireEvent.click(screen.getByLabelText(/add profile/i));
    const nameInput = screen.getByLabelText(/profile name/i);
    fireEvent.change(nameInput, { target: { value: "Research" } });

    // Pick a color swatch (any one)
    const swatches = screen.getAllByLabelText(/color/i);
    if (swatches.length > 0) fireEvent.click(swatches[0]);

    fireEvent.click(screen.getByLabelText(/^create$/i));

    await waitFor(() => {
      expect(postBody).not.toBeNull();
      expect(postBody.name).toBe("Research");
    });
  });
});

describe("ProfileManager — delete flow", () => {
  it("delete button shows confirmation with cookie-cascade warning", async () => {
    render(
      <ProfileManager
        activeProfileId="personal"
        onClose={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("Work"));

    // Click delete on Work (not active)
    const deleteBtns = screen.getAllByLabelText(/delete profile/i);
    const workRow = screen.getByText("Work").closest('[role="listitem"], li, div');
    const workDelete = deleteBtns.find((b) => workRow?.contains(b)) as HTMLButtonElement;
    fireEvent.click(workDelete);

    // Confirmation should appear
    await waitFor(() => {
      expect(screen.getByText(/this also clears all saved cookies/i)).toBeTruthy();
    });
  });

  it("confirming delete sends DELETE request", async () => {
    let deleteUrl: string | null = null;
    global.fetch = vi.fn().mockImplementation((url, opts) => {
      if (opts?.method === "DELETE") {
        deleteUrl = url as string;
        return Promise.resolve({ ok: true, status: 204 });
      }
      return Promise.resolve(mockListResponse([
        { profile_id: "personal", name: "Personal", color: "#6c8df0", created_at: 0 },
        { profile_id: "work", name: "Work", color: "#f5b86b", created_at: 0 },
      ]));
    });

    render(
      <ProfileManager
        activeProfileId="personal"
        onClose={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("Work"));

    const deleteBtns = screen.getAllByLabelText(/delete profile/i);
    const workRow = screen.getByText("Work").closest('[role="listitem"], li, div');
    const workDelete = deleteBtns.find((b) => workRow?.contains(b)) as HTMLButtonElement;
    fireEvent.click(workDelete);

    await waitFor(() => screen.getByText(/this also clears all saved cookies/i));

    fireEvent.click(screen.getByLabelText(/confirm delete/i));

    await waitFor(() => {
      expect(deleteUrl).toContain("/api/desktop/browser/profiles/work");
    });
  });
});

describe("ProfileManager — close", () => {
  it("close button calls onClose", async () => {
    const onClose = vi.fn();
    render(
      <ProfileManager
        activeProfileId="personal"
        onClose={onClose}
      />,
    );
    await waitFor(() => screen.getByText("Personal"));
    fireEvent.click(screen.getByLabelText(/close manager/i));
    expect(onClose).toHaveBeenCalled();
  });
});

describe("ProfileManager — rename double-fire prevention", () => {
  it("pressing Enter calls renameProfile exactly once, not twice", async () => {
    let patchCount = 0;
    // First GET returns original names; PATCH increments counter; subsequent GETs return renamed
    global.fetch = vi.fn()
      .mockImplementationOnce(() =>
        Promise.resolve(mockListResponse([
          { profile_id: "personal", name: "Personal", color: "#6c8df0", created_at: 0 },
          { profile_id: "work", name: "Work", color: "#f5b86b", created_at: 0 },
        ])),
      )
      .mockImplementation((url: string, opts: any) => {
        if (opts?.method === "PATCH") {
          patchCount++;
          return Promise.resolve({
            ok: true,
            json: async () => ({
              profile_id: "work",
              name: "Work Renamed",
              color: "#f5b86b",
              created_at: 0,
            }),
          });
        }
        return Promise.resolve(mockListResponse([
          { profile_id: "personal", name: "Personal", color: "#6c8df0", created_at: 0 },
          { profile_id: "work", name: "Work Renamed", color: "#f5b86b", created_at: 0 },
        ]));
      });

    render(
      <ProfileManager
        activeProfileId="personal"
        onClose={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("Work"));

    // Click rename on Work
    const renameBtns = screen.getAllByLabelText(/rename work/i);
    fireEvent.click(renameBtns[0]);

    // Type new name and press Enter
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Work Renamed" } });
    fireEvent.keyDown(input, { key: "Enter" });

    // Wait for the rename to complete — patchCount must be exactly 1
    await waitFor(() => {
      expect(patchCount).toBe(1);
    });
  });
});

describe("ProfileManager — network error handling", () => {
  it("shows error banner when delete throws a network error", async () => {
    global.fetch = vi.fn().mockImplementation((url: string, opts: any) => {
      if (opts?.method === "DELETE") {
        return Promise.reject(new Error("Network failure"));
      }
      return Promise.resolve(mockListResponse([
        { profile_id: "personal", name: "Personal", color: "#6c8df0", created_at: 0 },
        { profile_id: "work", name: "Work", color: "#f5b86b", created_at: 0 },
      ]));
    });

    render(
      <ProfileManager
        activeProfileId="personal"
        onClose={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("Work"));

    // Click delete on Work
    const deleteBtns = screen.getAllByLabelText(/delete profile work/i);
    fireEvent.click(deleteBtns[0]);

    await waitFor(() => screen.getByText(/this also clears all saved cookies/i));
    fireEvent.click(screen.getByLabelText(/confirm delete/i));

    // Error banner must appear
    await waitFor(() => {
      expect(screen.getByText(/network error/i)).toBeTruthy();
    });
  });
});

describe("ProfileManager — orphan recovery", () => {
  beforeEach(() => {
    // Reset browser store to a clean slate before each test
    useBrowserStore.setState({ windows: {} });
  });

  it("Test A: recovers orphan windows in other windows after delete", async () => {
    // Set up two windows: window-a on personal, window-b on work
    useBrowserStore.getState().createWindow("window-a", "personal");
    useBrowserStore.getState().createWindow("window-b", "work");

    // Mock: DELETE succeeds, then GET list returns only personal (work was removed)
    global.fetch = vi.fn().mockImplementation((url: string, opts: any) => {
      if (opts?.method === "DELETE") {
        return Promise.resolve({ ok: true, status: 204 });
      }
      // GET /profiles — return only personal after delete
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({
          profiles: [
            { profile_id: "personal", name: "Personal", color: "#6c8df0", created_at: 0 },
          ],
        }),
      });
    });

    // Render ProfileManager as if the user in window-a opened it (active = personal)
    render(
      <ProfileManager
        activeProfileId="personal"
        onClose={() => {}}
      />,
    );

    // Wait for the initial profile list (which initially has personal + work)
    // But we've already set up fetch to return only personal, so wait for Work to appear
    // via the initial load mock — we need to override the initial load too.
    // Let's wait for the list to settle: the first GET returns personal only (no work).
    // Because the fetch is mocked to return personal-only for ALL GETs, the list
    // will show only Personal. We won't see Work in the UI.
    // To properly test the flow, we need Work in the initial list.
    // Re-mock: first GET returns both, DELETE succeeds, second GET returns only personal.
    global.fetch = vi.fn()
      .mockImplementationOnce(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            profiles: [
              { profile_id: "personal", name: "Personal", color: "#6c8df0", created_at: 0 },
              { profile_id: "work", name: "Work", color: "#f5b86b", created_at: 0 },
            ],
          }),
        }),
      )
      .mockImplementationOnce(() =>
        Promise.resolve({ ok: true, status: 204 }),
      )
      .mockImplementationOnce(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            profiles: [
              { profile_id: "personal", name: "Personal", color: "#6c8df0", created_at: 0 },
            ],
          }),
        }),
      );

    // Re-render with correct mock sequence
    const { unmount } = render(
      <ProfileManager
        activeProfileId="personal"
        onClose={() => {}}
      />,
    );

    await waitFor(() => screen.getAllByText("Work")[0]);

    // Click delete on Work
    const deleteBtns = screen.getAllByLabelText(/delete profile work/i);
    fireEvent.click(deleteBtns[deleteBtns.length - 1]);

    // Confirm the deletion
    await waitFor(() => screen.getAllByText(/this also clears all saved cookies/i)[0]);
    const confirmBtns = screen.getAllByLabelText(/confirm delete/i);
    fireEvent.click(confirmBtns[confirmBtns.length - 1]);

    // After delete completes, window-b should have been switched to personal
    await waitFor(() => {
      const windowB = useBrowserStore.getState().windows["window-b"];
      expect(windowB?.profileId).toBe("personal");
    });

    unmount();
  });

  it("Test B: no switchProfile called when deleted profile is not in any window", async () => {
    // Set up a single window on personal; work profile is not in any window
    useBrowserStore.getState().createWindow("window-a", "personal");

    // Spy on switchProfile
    const switchProfileSpy = vi.spyOn(useBrowserStore.getState(), "switchProfile");

    global.fetch = vi.fn()
      .mockImplementationOnce(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            profiles: [
              { profile_id: "personal", name: "Personal", color: "#6c8df0", created_at: 0 },
              { profile_id: "work", name: "Work", color: "#f5b86b", created_at: 0 },
            ],
          }),
        }),
      )
      .mockImplementationOnce(() =>
        Promise.resolve({ ok: true, status: 204 }),
      )
      .mockImplementationOnce(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            profiles: [
              { profile_id: "personal", name: "Personal", color: "#6c8df0", created_at: 0 },
            ],
          }),
        }),
      );

    render(
      <ProfileManager
        activeProfileId="personal"
        onClose={() => {}}
      />,
    );

    await waitFor(() => screen.getByText("Work"));

    // Click delete on Work
    const deleteBtns = screen.getAllByLabelText(/delete profile work/i);
    fireEvent.click(deleteBtns[0]);

    await waitFor(() => screen.getByText(/this also clears all saved cookies/i));
    fireEvent.click(screen.getByLabelText(/confirm delete/i));

    // After delete, window-a should still be on personal (no switch needed)
    await waitFor(() => {
      const windowA = useBrowserStore.getState().windows["window-a"];
      expect(windowA?.profileId).toBe("personal");
    });

    // switchProfile should NOT have been called (no window was on work)
    expect(switchProfileSpy).not.toHaveBeenCalled();
  });
});
