import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ConsentActions } from "./ConsentActions";

function okFetch() {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ status: "ok" }),
  });
}

describe("ConsentActions", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders Allow and Deny buttons", () => {
    vi.stubGlobal("fetch", okFetch());
    render(<ConsentActions requestId="req-1" scopes={["memory_read"]} />);
    expect(screen.getByRole("button", { name: /allow/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /deny/i })).toBeInTheDocument();
  });

  it("posts the requested scopes to the approve endpoint and calls onResolved", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    const onResolved = vi.fn();
    render(
      <ConsentActions
        requestId="req-1"
        scopes={["memory_read", "a2a_send"]}
        onResolved={onResolved}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /allow/i }));

    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agents/auth-requests/req-1/approve",
      expect.objectContaining({ method: "POST" }),
    );
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({
      granted_scopes: ["memory_read", "a2a_send"],
    });
  });

  it("posts to the deny endpoint with no body", async () => {
    const fetchMock = okFetch();
    vi.stubGlobal("fetch", fetchMock);
    const onResolved = vi.fn();
    render(<ConsentActions requestId="req-2" scopes={["files_read"]} onResolved={onResolved} />);

    fireEvent.click(screen.getByRole("button", { name: /deny/i }));

    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/agents/auth-requests/req-2/deny",
      expect.objectContaining({ method: "POST" }),
    );
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.body).toBeUndefined();
  });

  it("surfaces an error and does not call onResolved on a failed request", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: () => Promise.resolve({ detail: "forbidden" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const onResolved = vi.fn();
    render(<ConsentActions requestId="req-3" scopes={[]} onResolved={onResolved} />);

    fireEvent.click(screen.getByRole("button", { name: /allow/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("forbidden"));
    expect(onResolved).not.toHaveBeenCalled();
  });
});
