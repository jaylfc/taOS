import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { VfsBrowser } from "../VfsBrowser";

// The API returns FileEntry[] (flat array with is_dir boolean).
const mockEntries = [
  { name: "report.md", path: "report.md", is_dir: false, size: 100, modified: 1700000000 },
  { name: "notes", path: "notes", is_dir: true, size: 0, modified: 1700000000 },
];

function mockFetch(entries = mockEntries) {
  global.fetch = vi.fn((url: RequestInfo | URL) => {
    const u = String(url);
    if (u.includes("/api/workspace/files")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Headers({ "Content-Type": "application/json" }),
        json: () => Promise.resolve(entries),
      } as Response);
    }
    if (u.includes("/api/agents/") && u.includes("/workspace/files")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: new Headers({ "Content-Type": "application/json" }),
        json: () => Promise.resolve(entries),
      } as Response);
    }
    return Promise.resolve({
      ok: false,
      status: 404,
      headers: new Headers({ "Content-Type": "application/json" }),
      json: () => Promise.resolve({}),
    } as Response);
  }) as unknown as typeof fetch;
}

describe("VfsBrowser", () => {
  it("renders the root listing from the API mock", async () => {
    mockFetch();
    render(<VfsBrowser root="/workspaces/user" onSelect={vi.fn()} />);
    // folders first, then files
    expect(await screen.findByText(/notes/)).toBeInTheDocument();
    expect(screen.getByText(/report\.md/)).toBeInTheDocument();
  });

  it("fetches agent workspace when root starts with /workspaces/agent/", async () => {
    mockFetch();
    render(<VfsBrowser root="/workspaces/agent/my-bot" onSelect={vi.fn()} />);
    expect(await screen.findByText(/notes/)).toBeInTheDocument();
    const fetchCalls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls;
    expect(String(fetchCalls[0][0])).toContain("/api/agents/my-bot/workspace/files");
  });

  it("calls onSelect with absolute path on file click", async () => {
    mockFetch();
    const onSelect = vi.fn();
    render(<VfsBrowser root="/workspaces/user" onSelect={onSelect} />);
    const fileBtn = await screen.findByText(/report\.md/);
    fireEvent.click(fileBtn);
    expect(onSelect).toHaveBeenCalledWith("/workspaces/user/report.md");
  });

  it("navigates into folder on click and shows go-up button", async () => {
    mockFetch();
    render(<VfsBrowser root="/workspaces/user" onSelect={vi.fn()} />);
    const folderBtn = await screen.findByText(/notes/);
    fireEvent.click(folderBtn);
    expect(await screen.findByLabelText("Go up one folder")).toBeInTheDocument();
  });

  it("multi mode toggles selection and calls onSelect with array", async () => {
    mockFetch();
    const onSelect = vi.fn();
    render(<VfsBrowser root="/workspaces/user" onSelect={onSelect} multi />);
    const fileBtn = await screen.findByText(/report\.md/);
    fireEvent.click(fileBtn);
    expect(onSelect).toHaveBeenCalledWith(["/workspaces/user/report.md"]);
    fireEvent.click(fileBtn);
    expect(onSelect).toHaveBeenLastCalledWith([]);
  });
});
