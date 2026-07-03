import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { SetupChecklist } from "./SetupChecklist";
import { fetchAccount } from "@/lib/account-client";

vi.mock("@/stores/process-store", () => ({
  useProcessStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({
      openWindow: vi.fn(),
    }),
}));

vi.mock("@/registry/app-registry", () => ({
  getApp: (id: string) => ({
    id,
    name: id,
    icon: "app",
    category: "platform",
    defaultSize: { w: 800, h: 600 },
    minSize: { w: 400, h: 300 },
    singleton: true,
    pinned: false,
    launchpadOrder: 1,
  }),
}));

vi.mock("@/lib/account-client", () => ({
  fetchAccount: vi.fn(),
}));

const CLOUD_LABEL = "Sign in to your taOS account (optional)";
const CLOUD_DETAIL =
  "One account for taOSgo remote access, app sharing, and reserving your taOS username for a future website and socials.";
const HANDLE_HINT = "Tip: reserve your taOS username in Settings before someone else takes it.";

const baseStatus = {
  account: false,
  has_provider: false,
  taos_model_set: false,
  has_agent: false,
  memory_enabled: false,
  dismissed: false,
  complete: false,
};

function mockFetchStatus(status: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url === "/api/setup/status") {
        return Promise.resolve(
          new Response(JSON.stringify(status), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      if (url === "/api/setup/dismiss") {
        return Promise.resolve(new Response("{}", { status: 200 }));
      }
      return Promise.reject(new Error("unexpected fetch: " + url));
    }) as unknown as typeof fetch,
  );
}

// Same poll interval the component uses (BACKEND_POLL_MS in SetupChecklist.tsx)
// while an install is in flight.
const BACKEND_POLL_MS = 3000;

/**
 * Stateful fetch mock for the one-tap backend install flow: /api/setup/status
 * reflects `state.backendRunning` for the given `accel`, and
 * /api/setup/install-backend responds according to `installOk`. Returns the
 * mutable state so a test can flip backendRunning to simulate the backend
 * coming up mid-poll.
 */
function mockFetchBackendInstallFlow(
  { accel = "rknpu", installOk = true }: { accel?: string; installOk?: boolean } = {},
) {
  const state = { backendRunning: false };
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url === "/api/setup/status") {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              ...baseStatus,
              npu_present: accel === "rknpu",
              npu_backend_running: accel === "rknpu" && state.backendRunning,
              accel,
              default_backend_running: state.backendRunning,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      if (url === "/api/setup/install-backend") {
        return Promise.resolve(new Response("{}", { status: installOk ? 200 : 500 }));
      }
      if (url === "/api/setup/dismiss") {
        return Promise.resolve(new Response("{}", { status: 200 }));
      }
      return Promise.reject(new Error("unexpected fetch: " + url));
    }) as unknown as typeof fetch,
  );
  return state;
}

describe("SetupChecklist", () => {
  beforeEach(() => {
    // Default the cloud account to unavailable so tests that don't care
    // about it settle deterministically instead of hanging on "loading".
    vi.mocked(fetchAccount).mockResolvedValue({ kind: "unavailable" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders nothing when status is still loading (null)", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise(() => {
            /* never resolves */
          }),
      ) as unknown as typeof fetch,
    );
    const { container } = render(<SetupChecklist />);
    expect(container.innerHTML).toBe("");
  });

  it("renders nothing when status.dismissed is true", async () => {
    mockFetchStatus({ ...baseStatus, dismissed: true });
    const { container } = render(<SetupChecklist />);
    // Wait for the status fetch to land, then assert against the settled
    // render; asserting synchronously would pass trivially on the initial
    // (status === null) render even if the hiding logic were broken.
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledWith("/api/setup/status"));
    await act(async () => {});
    expect(container.innerHTML).toBe("");
  });

  it("renders nothing when status.complete is true", async () => {
    mockFetchStatus({ ...baseStatus, complete: true });
    const { container } = render(<SetupChecklist />);
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledWith("/api/setup/status"));
    await act(async () => {});
    expect(container.innerHTML).toBe("");
  });

  it("renders the header with Get started and step count", async () => {
    mockFetchStatus(baseStatus);
    render(<SetupChecklist />);
    expect(await screen.findByText("Get started")).toBeInTheDocument();
    expect(screen.getByText("0/6")).toBeInTheDocument();
  });

  it("renders all six step labels", async () => {
    mockFetchStatus(baseStatus);
    render(<SetupChecklist />);
    expect(await screen.findByText("Create your account")).toBeInTheDocument();
    expect(screen.getByText(CLOUD_LABEL)).toBeInTheDocument();
    expect(screen.getByText("Add a provider")).toBeInTheDocument();
    expect(screen.getByText("Choose a model for the taOS agent")).toBeInTheDocument();
    expect(screen.getByText("Deploy your first agent")).toBeInTheDocument();
    expect(screen.getByText("Set up memory")).toBeInTheDocument();
  });

  it("renders detail text for incomplete steps", async () => {
    mockFetchStatus(baseStatus);
    render(<SetupChecklist />);
    expect(await screen.findByText("Connect a cloud API key or local model server")).toBeInTheDocument();
    expect(screen.getByText("Pick the model your taOS agent will use")).toBeInTheDocument();
    expect(screen.getByText("Deploy an AI agent (Hermes recommended)")).toBeInTheDocument();
    expect(screen.getByText("taOSmd memory is recommended and on by default")).toBeInTheDocument();
  });

  it("shows the correct done count when some steps are complete", async () => {
    mockFetchStatus({
      ...baseStatus,
      account: true,
      has_provider: true,
    });
    render(<SetupChecklist />);
    expect(await screen.findByText("2/6")).toBeInTheDocument();
  });

  it("does not show detail text for completed steps", async () => {
    mockFetchStatus({
      ...baseStatus,
      account: true,
    });
    render(<SetupChecklist />);
    expect(await screen.findByText("Create your account")).toBeInTheDocument();
    // "Done at sign-up" is the detail for account, which should not render when done
    expect(screen.queryByText("Done at sign-up")).toBeNull();
  });

  it("renders a dismiss button with accessible label", async () => {
    mockFetchStatus(baseStatus);
    render(<SetupChecklist />);
    const dismissBtn = await screen.findByRole("button", {
      name: "Dismiss setup checklist",
    });
    expect(dismissBtn).toBeInTheDocument();
  });

  it("calls onDismissed after clicking dismiss", async () => {
    mockFetchStatus(baseStatus);
    const onDismissed = vi.fn();
    render(<SetupChecklist onDismissed={onDismissed} />);
    const dismissBtn = await screen.findByRole("button", {
      name: "Dismiss setup checklist",
    });
    fireEvent.click(dismissBtn);
    await waitFor(() => {
      expect(onDismissed).toHaveBeenCalledTimes(1);
    });
  });

  it("renders accessible aria-labels for completed and pending steps", async () => {
    mockFetchStatus({
      ...baseStatus,
      account: true,
      has_provider: false,
    });
    render(<SetupChecklist />);
    const doneStep = await screen.findByRole("button", {
      name: "Create your account — complete",
    });
    expect(doneStep).toBeInTheDocument();
    const pendingStep = screen.getByRole("button", {
      name: "Add a provider",
    });
    expect(pendingStep).toBeInTheDocument();
  });

  it("renders a list with role=list for the steps", async () => {
    mockFetchStatus(baseStatus);
    render(<SetupChecklist />);
    expect(await screen.findByRole("list")).toBeInTheDocument();
  });

  it("hides the NPU step on boards without an NPU", async () => {
    mockFetchStatus(baseStatus);
    render(<SetupChecklist />);
    await screen.findByText("Get started");
    expect(screen.queryByText("Install the NPU backend")).toBeNull();
    expect(screen.getByText("0/6")).toBeInTheDocument();
  });

  it("shows the NPU step as an additional item when an NPU is present", async () => {
    mockFetchStatus({
      ...baseStatus,
      npu_present: true,
      npu_backend_running: false,
      accel: "rknpu",
      default_backend_running: false,
    });
    render(<SetupChecklist />);
    expect(await screen.findByText("Install the NPU backend")).toBeInTheDocument();
    expect(
      screen.getByText("Installs the Rockchip NPU runtime so models run on this device's NPU."),
    ).toBeInTheDocument();
    expect(screen.getByText("0/7")).toBeInTheDocument();
  });

  it("stays visible when core steps are complete but the NPU step is outstanding", async () => {
    mockFetchStatus({
      ...baseStatus,
      has_provider: true,
      taos_model_set: true,
      complete: true,
      npu_present: true,
      npu_backend_running: false,
      accel: "rknpu",
      default_backend_running: false,
    });
    render(<SetupChecklist />);
    expect(await screen.findByText("Install the NPU backend")).toBeInTheDocument();
  });

  it("hides once complete and the NPU backend is running", async () => {
    mockFetchStatus({
      ...baseStatus,
      complete: true,
      npu_present: true,
      npu_backend_running: true,
      accel: "rknpu",
      default_backend_running: true,
    });
    const { container } = render(<SetupChecklist />);
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledWith("/api/setup/status"));
    await act(async () => {});
    expect(container.innerHTML).toBe("");
  });

  it("marks the NPU step complete when the backend is running", async () => {
    mockFetchStatus({
      ...baseStatus,
      npu_present: true,
      npu_backend_running: true,
      accel: "rknpu",
      default_backend_running: true,
    });
    render(<SetupChecklist />);
    const doneStep = await screen.findByRole("button", {
      name: "Install the NPU backend — complete",
    });
    expect(doneStep).toBeInTheDocument();
    expect(screen.getByText("1/7")).toBeInTheDocument();
  });

  it("renders the cloud-account step with the exact label and detail copy", async () => {
    mockFetchStatus(baseStatus);
    render(<SetupChecklist />);
    expect(await screen.findByText(CLOUD_LABEL)).toBeInTheDocument();
    expect(screen.getByText(CLOUD_DETAIL)).toBeInTheDocument();
  });

  it("ticks the cloud-account step when the cloud account is signed in", async () => {
    vi.mocked(fetchAccount).mockResolvedValue({
      kind: "signed-in",
      account: {
        user_id: "u1",
        email: "user@example.com",
        taosgo: { status: "none" },
        handle: "someuser",
      },
    });
    mockFetchStatus(baseStatus);
    render(<SetupChecklist />);
    const doneStep = await screen.findByRole("button", {
      name: `${CLOUD_LABEL} — complete`,
    });
    expect(doneStep).toBeInTheDocument();
    expect(screen.getByText("1/6")).toBeInTheDocument();
  });

  it("shows the cloud-account step unticked without error when the account service is unavailable", async () => {
    vi.mocked(fetchAccount).mockResolvedValue({ kind: "unavailable" });
    mockFetchStatus(baseStatus);
    render(<SetupChecklist />);
    const pendingStep = await screen.findByRole("button", { name: CLOUD_LABEL });
    expect(pendingStep).toBeInTheDocument();
    expect(screen.getByText("0/6")).toBeInTheDocument();
    expect(screen.queryByText(HANDLE_HINT)).toBeNull();
  });

  it("shows the reserve-username hint when signed in without a handle", async () => {
    vi.mocked(fetchAccount).mockResolvedValue({
      kind: "signed-in",
      account: {
        user_id: "u1",
        email: "user@example.com",
        taosgo: { status: "none" },
        handle: null,
      },
    });
    mockFetchStatus(baseStatus);
    render(<SetupChecklist />);
    expect(await screen.findByText(HANDLE_HINT)).toBeInTheDocument();
  });

  it("omits the reserve-username hint when the account has a handle", async () => {
    vi.mocked(fetchAccount).mockResolvedValue({
      kind: "signed-in",
      account: {
        user_id: "u1",
        email: "user@example.com",
        taosgo: { status: "none" },
        handle: "someuser",
      },
    });
    mockFetchStatus(baseStatus);
    render(<SetupChecklist />);
    await screen.findByRole("button", { name: `${CLOUD_LABEL} — complete` });
    expect(screen.queryByText(HANDLE_HINT)).toBeNull();
  });

  it("omits the reserve-username hint when signed out", async () => {
    vi.mocked(fetchAccount).mockResolvedValue({ kind: "signed-out" });
    mockFetchStatus(baseStatus);
    render(<SetupChecklist />);
    await screen.findByText(CLOUD_LABEL);
    expect(screen.queryByText(HANDLE_HINT)).toBeNull();
  });

  it("also offers a secondary Open in Store link on the outstanding NPU step", async () => {
    mockFetchStatus({
      ...baseStatus,
      npu_present: true,
      npu_backend_running: false,
      accel: "rknpu",
      default_backend_running: false,
    });
    render(<SetupChecklist />);
    expect(await screen.findByRole("button", { name: "Install NPU backend" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open in Store" })).toBeInTheDocument();
  });

  it("tapping Install NPU backend POSTs to install-backend and shows an in-progress state", async () => {
    mockFetchBackendInstallFlow({ accel: "rknpu" });
    render(<SetupChecklist />);
    const installBtn = await screen.findByRole("button", { name: "Install NPU backend" });

    fireEvent.click(installBtn);

    await waitFor(() => {
      expect(vi.mocked(fetch)).toHaveBeenCalledWith(
        "/api/setup/install-backend",
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(await screen.findByText("Installing NPU backend...")).toBeInTheDocument();
  });

  it("ticks the NPU step automatically once a status poll reports the backend running", async () => {
    vi.useFakeTimers();
    try {
      const state = mockFetchBackendInstallFlow({ accel: "rknpu" });
      render(<SetupChecklist />);

      // Let the initial /api/setup/status fetch (mount effect) resolve.
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      const installBtn = screen.getByRole("button", { name: "Install NPU backend" });

      await act(async () => {
        fireEvent.click(installBtn);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(screen.getByText("Installing NPU backend...")).toBeInTheDocument();

      // Simulate the backend coming up, then let the in-flight poll interval fire.
      state.backendRunning = true;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(BACKEND_POLL_MS);
      });

      expect(
        screen.getByRole("button", { name: "Install the NPU backend — complete" }),
      ).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a clear error with retry when the install POST fails", async () => {
    mockFetchBackendInstallFlow({ accel: "rknpu", installOk: false });
    render(<SetupChecklist />);
    const installBtn = await screen.findByRole("button", { name: "Install NPU backend" });

    fireEvent.click(installBtn);

    expect(await screen.findByText("Couldn't install the NPU backend. Try again.")).toBeInTheDocument();
    // The button reverts to its idle label so tapping it again retries.
    expect(screen.getByRole("button", { name: "Install NPU backend" })).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Platform-generalized backend step (beyond Rockchip): NVIDIA CUDA / AMD
  // ROCm / Apple Silicon (Metal) / x86 CPU-only all get the llama.cpp-server
  // copy and one-tap flow via the same /api/setup/install-backend endpoint.
  // -------------------------------------------------------------------------

  it("hides the backend step entirely when accel is none (e.g. Intel Arc)", async () => {
    mockFetchStatus({ ...baseStatus, accel: "none", default_backend_running: false });
    render(<SetupChecklist />);
    await screen.findByText("Get started");
    expect(screen.queryByText("Install a local model backend")).toBeNull();
    expect(screen.getByText("0/6")).toBeInTheDocument();
  });

  it("shows the llama.cpp-server copy for a CUDA device", async () => {
    mockFetchStatus({ ...baseStatus, accel: "cuda", default_backend_running: false });
    render(<SetupChecklist />);
    expect(await screen.findByText("Install a local model backend")).toBeInTheDocument();
    expect(
      screen.getByText("Runs chat and memory models locally on this device's GPU."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Install llama.cpp server" })).toBeInTheDocument();
  });

  it("shows the llama.cpp-server copy for a ROCm device", async () => {
    mockFetchStatus({ ...baseStatus, accel: "rocm", default_backend_running: false });
    render(<SetupChecklist />);
    expect(await screen.findByText("Install a local model backend")).toBeInTheDocument();
    expect(
      screen.getByText("Runs chat and memory models locally on this device's GPU."),
    ).toBeInTheDocument();
  });

  it("shows the llama.cpp-server copy for an Apple Silicon (Metal) device", async () => {
    mockFetchStatus({ ...baseStatus, accel: "metal", default_backend_running: false });
    render(<SetupChecklist />);
    expect(await screen.findByText("Install a local model backend")).toBeInTheDocument();
    expect(
      screen.getByText("Runs chat and memory models locally on this device's GPU."),
    ).toBeInTheDocument();
  });

  it("shows the CPU-flavored copy for a CPU-only device", async () => {
    mockFetchStatus({ ...baseStatus, accel: "cpu", default_backend_running: false });
    render(<SetupChecklist />);
    expect(await screen.findByText("Install a local model backend")).toBeInTheDocument();
    expect(
      screen.getByText("Runs chat and memory models locally on this device's CPU."),
    ).toBeInTheDocument();
  });

  it("tapping Install llama.cpp server on a CUDA device POSTs to install-backend and ticks on completion", async () => {
    vi.useFakeTimers();
    try {
      const state = mockFetchBackendInstallFlow({ accel: "cuda" });
      render(<SetupChecklist />);

      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      const installBtn = screen.getByRole("button", { name: "Install llama.cpp server" });

      await act(async () => {
        fireEvent.click(installBtn);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(vi.mocked(fetch)).toHaveBeenCalledWith(
        "/api/setup/install-backend",
        expect.objectContaining({ method: "POST" }),
      );
      expect(screen.getByText("Installing llama.cpp server...")).toBeInTheDocument();

      state.backendRunning = true;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(BACKEND_POLL_MS);
      });

      expect(
        screen.getByRole("button", { name: "Install a local model backend — complete" }),
      ).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a clear error with retry for the llama.cpp-server install on a CUDA device", async () => {
    mockFetchBackendInstallFlow({ accel: "cuda", installOk: false });
    render(<SetupChecklist />);
    const installBtn = await screen.findByRole("button", { name: "Install llama.cpp server" });

    fireEvent.click(installBtn);

    expect(
      await screen.findByText("Couldn't install the local model backend. Try again."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Install llama.cpp server" })).toBeInTheDocument();
  });
});
