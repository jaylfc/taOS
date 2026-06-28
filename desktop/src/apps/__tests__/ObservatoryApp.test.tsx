import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import React from "react";

vi.mock("@/components/ui", () => ({
  Switch: ({ checked, onCheckedChange, ...rest }: {
    checked?: boolean;
    onCheckedChange?: (v: boolean) => void;
  } & React.HTMLAttributes<HTMLButtonElement>) => (
    <button role="switch" aria-checked={checked} onClick={() => onCheckedChange?.(!checked)} {...rest} />
  ),
}));

import { ObservatoryApp } from "../ObservatoryApp";

function makeFetch(health: unknown) {
  return vi.fn(async (url: string) => {
    const u = String(url);
    if (u === "/api/observatory/fleet") {
      return {
        ok: true,
        json: async () => ({ agents: [], paused: { global: false, lanes: {} }, health }),
      };
    }
    if (u === "/api/observatory/throttle") {
      return { ok: true, json: async () => ({ global: null, lanes: {} }) };
    }
    return { ok: true, json: async () => ({}) };
  });
}

describe("ObservatoryApp fleet health pill", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("shows an active pill with working/total when fleet is active", async () => {
    global.fetch = makeFetch({
      total: 3, working: 2, idle: 1, stale: 0, stale_handles: [], status: "active",
    }) as typeof fetch;
    render(<ObservatoryApp windowId="w1" />);
    await waitFor(() => expect(screen.getByText("2/3 active")).toBeDefined());
  });

  it("shows a degraded pill with the stale count when a lane is wedged", async () => {
    global.fetch = makeFetch({
      total: 2, working: 1, idle: 1, stale: 1, stale_handles: ["@lane-x"], status: "degraded",
    }) as typeof fetch;
    render(<ObservatoryApp windowId="w1" />);
    await waitFor(() => expect(screen.getByText("1 stale")).toBeDefined());
  });

  it("renders no pill when the controller omits health (graceful)", async () => {
    global.fetch = makeFetch(undefined) as typeof fetch;
    render(<ObservatoryApp windowId="w1" />);
    // Header still renders.
    await waitFor(() => expect(screen.getByText("Observatory")).toBeDefined());
    expect(screen.queryByText(/active$/)).toBeNull();
    expect(screen.queryByText(/stale$/)).toBeNull();
  });
});
