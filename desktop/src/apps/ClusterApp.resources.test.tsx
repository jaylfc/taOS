import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ClusterResourcesPanel } from "./ClusterApp.resources";
import type { ClusterResource, ClusterResources } from "@/lib/cluster";

const EM_DASH = "\u2014";

function row(overrides: Partial<ClusterResource>): ClusterResource {
  return {
    id: `${overrides.type ?? "agent"}:${overrides.name ?? "x"}`,
    type: "agent",
    name: "x",
    worker: "local",
    project: null,
    state: "running",
    cpu_cores: null,
    memory_mb: null,
    warnings: [],
    ...overrides,
  };
}

function makeData(errors: string[] = []): ClusterResources {
  return {
    generated_at: 1_700_000_000,
    errors,
    resources: [
      row({ type: "agent", name: "alice", worker: "local", project: "alpha", state: "running", cpu_cores: 2, memory_mb: 2048 }),
      row({ type: "agent", name: "bob", worker: "local", project: "beta", state: "unknown", warnings: ["container not found"] }),
      row({ type: "backend", name: "llama-cpp", worker: "gpu-box", state: "stopped", warnings: ["worker offline"] }),
      row({ type: "controller", name: "local", worker: "local", state: "running" }),
    ],
  };
}

/** Names in the first column of every data row of the table. */
function rowNames(): string[] {
  const table = screen.getByRole("table");
  const bodyRows = within(table).getAllByRole("row").slice(1);
  return bodyRows.map((r) => within(r).getAllByRole("cell")[0].textContent ?? "");
}

describe("ClusterResourcesPanel", () => {
  it("renders one row per resource with warnings as badges", () => {
    const { container } = render(<ClusterResourcesPanel data={makeData()} loading={false} />);
    expect(rowNames()).toEqual(["alice", "bob", "llama-cpp", "local"]);
    expect(screen.getByRole("table").querySelector("caption")?.textContent).toContain("4 of 4");
    expect(screen.getByLabelText("Warning: container not found")).toBeInTheDocument();
    expect(screen.getByLabelText("Warning: worker offline")).toBeInTheDocument();
    expect(screen.getByText("2 cores")).toBeInTheDocument();
    expect(screen.getByText("2.0 GB")).toBeInTheDocument();
    expect(container.textContent).not.toContain(EM_DASH);
  });

  it("type filter narrows the rows", () => {
    render(<ClusterResourcesPanel data={makeData()} loading={false} />);
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "backend" } });
    expect(rowNames()).toEqual(["llama-cpp"]);
  });

  it("worker filter narrows the rows", () => {
    render(<ClusterResourcesPanel data={makeData()} loading={false} />);
    fireEvent.change(screen.getByLabelText("Worker"), { target: { value: "gpu-box" } });
    expect(rowNames()).toEqual(["llama-cpp"]);
  });

  it("project filter narrows the rows, including rows with no project", () => {
    render(<ClusterResourcesPanel data={makeData()} loading={false} />);
    const select = screen.getByLabelText("Project") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "alpha" } });
    expect(rowNames()).toEqual(["alice"]);
    const none = Array.from(select.options).find((o) => o.textContent === "(none)");
    expect(none).toBeDefined();
    fireEvent.change(select, { target: { value: none!.value } });
    expect(rowNames()).toEqual(["llama-cpp", "local"]);
  });

  it("state filter narrows the rows", () => {
    render(<ClusterResourcesPanel data={makeData()} loading={false} />);
    fireEvent.change(screen.getByLabelText("State"), { target: { value: "running" } });
    expect(rowNames()).toEqual(["alice", "local"]);
  });

  it("search box narrows by name", () => {
    render(<ClusterResourcesPanel data={makeData()} loading={false} />);
    fireEvent.change(screen.getByLabelText("Search resources by name"), { target: { value: "LLA" } });
    expect(rowNames()).toEqual(["llama-cpp"]);
  });

  it("shows the errors list as a notice above a still-rendered table", () => {
    render(<ClusterResourcesPanel data={makeData(["containers"])} loading={false} />);
    expect(screen.getByRole("status").textContent).toContain(
      "Some sources could not be read: containers",
    );
    expect(rowNames()).toHaveLength(4);
  });

  it("shows the empty state for an empty list", () => {
    render(
      <ClusterResourcesPanel
        data={{ resources: [], errors: [], generated_at: 0 }}
        loading={false}
      />,
    );
    expect(screen.getByText("No resources reported yet.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("resets a selected type filter whose value vanished after a refresh", () => {
    const first = makeData();
    const { rerender } = render(<ClusterResourcesPanel data={first} loading={false} />);

    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "backend" } });
    expect(rowNames()).toEqual(["llama-cpp"]);

    // Refresh: the "backend" type no longer appears in the data.
    const second: ClusterResources = {
      ...first,
      resources: first.resources.filter((r) => r.type !== "backend"),
    };
    rerender(<ClusterResourcesPanel data={second} loading={false} />);

    // Filter must reset to All and the remaining rows must render.
    expect((screen.getByLabelText("Type") as HTMLSelectElement).value).toBe("__all__");
    expect(rowNames()).toEqual(["alice", "bob", "local"]);
  });

  it("resets a selected worker filter whose value vanished after a refresh", () => {
    const first = makeData();
    const { rerender } = render(<ClusterResourcesPanel data={first} loading={false} />);

    fireEvent.change(screen.getByLabelText("Worker"), { target: { value: "gpu-box" } });
    expect(rowNames()).toEqual(["llama-cpp"]);

    const second: ClusterResources = {
      ...first,
      resources: first.resources.filter((r) => r.worker !== "gpu-box"),
    };
    rerender(<ClusterResourcesPanel data={second} loading={false} />);

    expect((screen.getByLabelText("Worker") as HTMLSelectElement).value).toBe("__all__");
    expect(rowNames()).toEqual(["alice", "bob", "local"]);
  });

  it("resets a selected state filter whose value vanished after a refresh", () => {
    const first = makeData();
    const { rerender } = render(<ClusterResourcesPanel data={first} loading={false} />);

    fireEvent.change(screen.getByLabelText("State"), { target: { value: "stopped" } });
    expect(rowNames()).toEqual(["llama-cpp"]);

    const second: ClusterResources = {
      ...first,
      resources: first.resources.filter((r) => r.state !== "stopped"),
    };
    rerender(<ClusterResourcesPanel data={second} loading={false} />);

    expect((screen.getByLabelText("State") as HTMLSelectElement).value).toBe("__all__");
    expect(rowNames()).toEqual(["alice", "bob", "local"]);
  });

  it("keeps a filter selection whose value still exists after a refresh", () => {
    const first = makeData();
    const { rerender } = render(<ClusterResourcesPanel data={first} loading={false} />);

    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "agent" } });
    expect(rowNames()).toEqual(["alice", "bob"]);

    const second: ClusterResources = {
      ...first,
      resources: [row({ type: "agent", name: "carol", worker: "gpu-box", state: "running" })],
    };
    rerender(<ClusterResourcesPanel data={second} loading={false} />);

    expect((screen.getByLabelText("Type") as HTMLSelectElement).value).toBe("agent");
    expect(rowNames()).toEqual(["carol"]);
  });

  it("shows the loading state before the first response", () => {
    render(<ClusterResourcesPanel data={null} loading={true} />);
    expect(screen.getByText("Loading resources...")).toBeInTheDocument();
  });
});
