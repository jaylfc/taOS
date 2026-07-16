import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";

vi.mock("@/components/ui", () => ({
  Button: ({
    children,
    onClick,
    disabled,
    ...rest
  }: React.ButtonHTMLAttributes<HTMLButtonElement> & { children?: React.ReactNode }) => (
    <button onClick={onClick} disabled={disabled} {...rest}>
      {children}
    </button>
  ),
  Textarea: ({
    onChange,
    value,
    placeholder,
    ...rest
  }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) => (
    <textarea onChange={onChange} value={value} placeholder={placeholder} {...rest} />
  ),
}));

import { HubApp } from "./HubApp";

const POST = {
  type: "post",
  author: "fp",
  seq: 1,
  prev: null,
  created_at: new Date().toISOString(),
  visibility: "circle",
  body: { text: "hello hub", format: "md-subset" },
  attachments: [],
  sig: "deadbeef",
  hash: "hash-1",
};

function makeFetch(handler: (url: string, init?: RequestInit) => { ok: boolean; json: () => unknown }) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const data = handler(String(url), init);
    return {
      ok: data.ok,
      status: data.ok ? 200 : 404,
      json: async () => data.json(),
    } as Response;
  });
}

describe("HubApp slice 4", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("composer defaults to Friends-only and publishes to the timeline", async () => {
    const fetchMock = makeFetch((url, init) => {
      if (url === "/api/hub/timeline") {
        return { ok: true, json: () => ({ state: "ok", posts: [POST] }) };
      }
      if (url === "/api/hub/posts" && (init?.method ?? "GET") === "POST") {
        return { ok: true, json: () => ({ state: "ok", post: POST }) };
      }
      return { ok: true, json: () => ({ state: "ok", posts: [] }) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<HubApp windowId="w1" />);

    // Default visibility switch shows Friends-only as active.
    expect(screen.getByTitle(/Only friends can read this/)).toHaveAttribute("aria-pressed", "true");

    // No posts yet.
    await waitFor(() => expect(screen.queryByText("hello hub")).not.toBeInTheDocument());

    // Type and publish.
    const textarea = screen.getByLabelText("Post text");
    fireEvent.change(textarea, { target: { value: "hello hub" } });
    fireEvent.click(screen.getByLabelText("Publish post"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/hub/posts",
      expect.objectContaining({ method: "POST" }),
    ));

    // Timeline now shows the published post.
    await waitFor(() => expect(screen.getByText("hello hub")).toBeInTheDocument());
    expect(screen.getAllByText("Friends-only").length).toBeGreaterThan(0);
  });

  it("switching to Public updates the published visibility", async () => {
    const fetchMock = makeFetch((url, init) => {
      if (url === "/api/hub/timeline") {
        return { ok: true, json: () => ({ state: "ok", posts: [] }) };
      }
      if (url === "/api/hub/posts" && (init?.method ?? "GET") === "POST") {
        return { ok: true, json: () => ({ state: "ok", post: { ...POST, visibility: "public" } }) };
      }
      return { ok: true, json: () => ({ state: "ok", posts: [] }) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<HubApp windowId="w2" />);

    fireEvent.click(screen.getByTitle(/Anyone with the link can read this/));
    const textarea = screen.getByLabelText("Post text");
    fireEvent.change(textarea, { target: { value: "broadcast" } });
    fireEvent.click(screen.getByLabelText("Publish post"));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/hub/posts",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining('"visibility":"public"'),
        }),
      ),
    );
  });

  it("deletes a post via the tombstone route", async () => {
    let deleted = false;
    const fetchMock = makeFetch((url, init) => {
      if (url === "/api/hub/timeline") {
        return {
          ok: true,
          json: () => ({ state: "ok", posts: deleted ? [] : [POST] }),
        };
      }
      if (url.endsWith("/delete") && (init?.method ?? "GET") === "POST") {
        deleted = true;
        return { ok: true, json: () => ({ state: "ok", tombstone: { type: "tombstone", target: POST.hash } }) };
      }
      return { ok: true, json: () => ({ state: "ok", posts: [POST] }) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<HubApp windowId="w3" />);

    await waitFor(() => expect(screen.getByText("hello hub")).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText("Delete post"));

    await waitFor(() => expect(screen.queryByText("hello hub")).not.toBeInTheDocument());
  });
});
