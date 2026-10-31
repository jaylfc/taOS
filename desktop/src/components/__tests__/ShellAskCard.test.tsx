/**
 * Unit tests for ShellAskCard: the inline chat card that lets the user allow
 * or deny a shell command the taOS Agent asked for. It answers the same
 * Decision the Decisions inbox shows, so either place settles it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import React from "react";

import { ShellAskCard } from "../ShellAskCard";

const ASK = { program: "npm run", decision_id: "dec-1", command: "npm run build" };

describe("<ShellAskCard />", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("names the command and what the agent tried", () => {
    render(<ShellAskCard ask={ASK} />);
    expect(screen.getByText(/npm run build/)).toBeInTheDocument();
    expect(screen.getByLabelText("Allow npm run")).toBeInTheDocument();
    expect(screen.getByLabelText("Deny npm run")).toBeInTheDocument();
  });

  it("allow answers the decision with approve", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
    render(<ShellAskCard ask={ASK} />);
    fireEvent.click(screen.getByLabelText("Allow npm run"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/decisions/dec-1/answer");
    expect(JSON.parse(init.body)).toEqual({ value: "approve" });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Allowed"));
  });

  it("deny answers the decision with deny", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
    render(<ShellAskCard ask={ASK} />);
    fireEvent.click(screen.getByLabelText("Deny npm run"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ value: "deny" });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Denied"));
  });

  it("says so when the decision was already answered elsewhere", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 409, json: async () => ({}) });
    render(<ShellAskCard ask={ASK} />);
    fireEvent.click(screen.getByLabelText("Allow npm run"));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Already answered"));
  });
});
