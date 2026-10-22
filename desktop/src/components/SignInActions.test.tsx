import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SignInActions, signInPayload } from "./SignInActions";

const URL_OK = "https://claude.ai/oauth/authorize?code=true&client_id=abc";

function okJson(body: unknown, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
}

describe("signInPayload", () => {
  it("reads an auth_broker notification payload", () => {
    expect(
      signInPayload({ request_id: "sib-1", url: URL_OK, host: "claude.ai", agent: "haiku", prompt: "Sign in" }),
    ).toEqual({ requestId: "sib-1", url: URL_OK, host: "claude.ai", agent: "haiku", prompt: "Sign in" });
  });

  it("refuses a url off the sign-in allowlist even if the server sent one", () => {
    expect(signInPayload({ request_id: "sib-1", url: "https://evil.example/x" })).toBeNull();
    expect(signInPayload({ request_id: "sib-1", url: "http://claude.ai/x" })).toBeNull();
    expect(signInPayload({ request_id: "sib-1", url: "https://claude.ai.evil.example/x" })).toBeNull();
    expect(signInPayload({ request_id: "sib-1", url: "https://u@claude.ai/x" })).toBeNull();
    expect(signInPayload({ request_id: "sib-1", url: "https://claude.ai:8443/x" })).toBeNull();
  });

  it("returns null without a request id", () => {
    expect(signInPayload({ url: URL_OK })).toBeNull();
    expect(signInPayload(undefined)).toBeNull();
  });
});

describe("SignInActions", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubGlobal("fetch", vi.fn(() => okJson({ ok: true })));
  });

  it("shows the agent and the host, and Open opens the url in a new browsing context", () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<SignInActions requestId="sib-1" url={URL_OK} host="claude.ai" agent="haiku" />);
    expect(screen.getByText(/haiku/)).toBeTruthy();
    expect(screen.getByText(/claude\.ai/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /open claude\.ai sign-in/i }));
    expect(open).toHaveBeenCalledWith(URL_OK, "_blank", "noopener,noreferrer");
  });

  it("posts the pasted code to the broker and resolves", async () => {
    const fetchMock = vi.fn(() => okJson({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const onResolved = vi.fn();
    render(<SignInActions requestId="sib-2" url={URL_OK} host="claude.ai" agent="haiku" onResolved={onResolved} />);

    const submit = screen.getByRole("button", { name: /send code/i });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/sign-in code/i), { target: { value: "  abc#123  " } });
    fireEvent.click(submit);

    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/auth-broker/requests/sib-2/code");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ code: "abc#123" });
  });

  it("surfaces the server error and does not resolve", async () => {
    vi.stubGlobal("fetch", vi.fn(() => okJson({ detail: "sign-in request expired" }, 410)));
    const onResolved = vi.fn();
    render(<SignInActions requestId="sib-3" url={URL_OK} host="claude.ai" agent="haiku" onResolved={onResolved} />);
    fireEvent.change(screen.getByLabelText(/sign-in code/i), { target: { value: "abc" } });
    fireEvent.click(screen.getByRole("button", { name: /send code/i }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/expired/));
    expect(onResolved).not.toHaveBeenCalled();
  });
});
