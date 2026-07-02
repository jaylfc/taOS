import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SetupChecklist } from "./SetupChecklist";

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

describe("SetupChecklist", () => {
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

  it("renders nothing when status.dismissed is true", () => {
    mockFetchStatus({ ...baseStatus, dismissed: true });
    const { container } = render(<SetupChecklist />);
    expect(container.innerHTML).toBe("");
  });

  it("renders nothing when status.complete is true", () => {
    mockFetchStatus({ ...baseStatus, complete: true });
    const { container } = render(<SetupChecklist />);
    expect(container.innerHTML).toBe("");
  });

  it("renders the header with Get started and step count", async () => {
    mockFetchStatus(baseStatus);
    render(<SetupChecklist />);
    expect(await screen.findByText("Get started")).toBeInTheDocument();
    expect(screen.getByText("0/5")).toBeInTheDocument();
  });

  it("renders all five step labels", async () => {
    mockFetchStatus(baseStatus);
    render(<SetupChecklist />);
    expect(await screen.findByText("Create your account")).toBeInTheDocument();
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
    expect(await screen.findByText("2/5")).toBeInTheDocument();
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
    expect(screen.getByText("0/5")).toBeInTheDocument();
  });

  it("shows the NPU step as a sixth item when an NPU is present", async () => {
    mockFetchStatus({ ...baseStatus, npu_present: true, npu_backend_running: false });
    render(<SetupChecklist />);
    expect(await screen.findByText("Install the NPU backend")).toBeInTheDocument();
    expect(
      screen.getByText("Install rkllama from the Store to run models on this device's NPU"),
    ).toBeInTheDocument();
    expect(screen.getByText("0/6")).toBeInTheDocument();
  });

  it("stays visible when core steps are complete but the NPU step is outstanding", async () => {
    mockFetchStatus({
      ...baseStatus,
      has_provider: true,
      taos_model_set: true,
      complete: true,
      npu_present: true,
      npu_backend_running: false,
    });
    render(<SetupChecklist />);
    expect(await screen.findByText("Install the NPU backend")).toBeInTheDocument();
  });

  it("hides once complete and the NPU backend is running", () => {
    mockFetchStatus({
      ...baseStatus,
      complete: true,
      npu_present: true,
      npu_backend_running: true,
    });
    const { container } = render(<SetupChecklist />);
    expect(container.innerHTML).toBe("");
  });

  it("marks the NPU step complete when the backend is running", async () => {
    mockFetchStatus({ ...baseStatus, npu_present: true, npu_backend_running: true });
    render(<SetupChecklist />);
    const doneStep = await screen.findByRole("button", {
      name: "Install the NPU backend — complete",
    });
    expect(doneStep).toBeInTheDocument();
    expect(screen.getByText("1/6")).toBeInTheDocument();
  });
});
