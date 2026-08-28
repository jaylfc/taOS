import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { UsersSection } from "./UsersPanel";

function jsonResponse(obj: unknown, init?: ResponseInit) {
  return new Response(JSON.stringify(obj), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

describe("UsersPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("announces a profile-save failure via role=alert", async () => {
    const mockUser = {
      username: "jay",
      full_name: "Jay",
      email: "jay@example.com",
      is_admin: true,
    };
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/status")) {
        return Promise.resolve(
          jsonResponse({ authenticated: true, user: mockUser, multi_user: true }),
        );
      }
      if (url.includes("/auth/users") && !url.includes("/profile")) {
        return Promise.resolve(jsonResponse({ users: [] }));
      }
      if (url.includes("/profile")) {
        return Promise.resolve(
          new Response(JSON.stringify({ error: "Save failed" }), {
            status: 500,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      return Promise.resolve(new Response(null, { status: 401 }));
    });
    render(<UsersSection />);
    await screen.findByDisplayValue("jay@example.com");
    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "Jay Test" },
    });
    fireEvent.click(screen.getByText("Save changes"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Save failed");
  });

  it("does not render an alert when the profile saves successfully", async () => {
    const mockUser = {
      username: "jay",
      full_name: "Jay",
      email: "jay@example.com",
      is_admin: true,
    };
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/auth/status")) {
        return Promise.resolve(
          jsonResponse({ authenticated: true, user: mockUser, multi_user: true }),
        );
      }
      if (url.includes("/auth/users") && !url.includes("/profile")) {
        return Promise.resolve(jsonResponse({ users: [] }));
      }
      if (url.includes("/profile")) {
        return Promise.resolve(jsonResponse({ ok: true }));
      }
      return Promise.resolve(new Response(null, { status: 401 }));
    });
    render(<UsersSection />);
    await screen.findByDisplayValue("jay@example.com");
    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "Jay Test" },
    });
    fireEvent.click(screen.getByText("Save changes"));
    await waitFor(() => expect(screen.getByText("Saved")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
