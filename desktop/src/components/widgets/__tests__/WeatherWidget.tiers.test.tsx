import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

let mockTier: "s" | "m" | "l" = "s";
vi.mock("@/hooks/use-widget-size", () => ({
  useWidgetSize: () => [{ current: null }, { tier: mockTier, width: 300, height: mockTier === "s" ? 120 : mockTier === "m" ? 240 : 340 }],
}));

vi.mock("@/apps/WeatherApp", async () => {
  const actual = await vi.importActual<typeof import("@/apps/WeatherApp")>("@/apps/WeatherApp");
  return { ...actual, getHomeLocation: () => ({ name: "Testville", latitude: 1, longitude: 2 }) };
});

// 48 hourly slots from 2026-10-09T00:00; temp = 100 + index so each is distinct.
const hourlyTime = Array.from({ length: 48 }, (_, i) => {
  const day = i < 24 ? "09" : "10";
  return `2026-10-${day}T${String(i % 24).padStart(2, "0")}:00`;
});
const BODY = {
  current: {
    time: "2026-10-09T15:00",
    temperature_2m: 18.4,
    apparent_temperature: 17,
    is_day: 1,
    weather_code: 3,
    relative_humidity_2m: 63,
    wind_speed_10m: 17,
  },
  hourly: {
    time: hourlyTime,
    temperature_2m: hourlyTime.map((_, i) => 100 + i),
    weather_code: hourlyTime.map(() => 3),
    is_day: hourlyTime.map((_, i) => (i % 24 >= 7 && i % 24 < 19 ? 1 : 0)),
  },
  daily: {
    time: ["2026-10-09", "2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13", "2026-10-14"],
    temperature_2m_max: [21, 22, 23, 24, 25, 26],
    temperature_2m_min: [9, 10, 11, 12, 13, 14],
    weather_code: [3, 61, 0, 73, 95, 2],
  },
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      String(url).includes("open-meteo")
        ? { ok: true, json: async () => BODY }
        : { ok: false, json: async () => ({}) },
    ),
  );
});

async function renderTier(tier: "s" | "m" | "l") {
  mockTier = tier;
  const view = render(<WeatherWidget />);
  await waitFor(() => expect(view.container.textContent).toContain("18"));
  return view;
}

import { WeatherWidget } from "../WeatherWidget";

describe("WeatherWidget tiers", () => {
  it("s shows today's high and low but no humidity or wind", async () => {
    const { container } = await renderTier("s");
    const text = container.textContent ?? "";
    expect(text).toContain("H 21°");
    expect(text).toContain("L 9°");
    expect(text).not.toContain("63%");
    expect(text).not.toContain("mph");
    expect(text).not.toContain("km/h");
    expect(text).not.toContain("Testville");
    expect(screen.queryAllByTestId("hourly-slot")).toHaveLength(0);
    expect(container.querySelector("[role=region]")?.getAttribute("aria-label")).toBe(
      "Weather: 18 degrees, Overcast, high 21, low 9, Testville",
    );
  });

  it("m shows 5 hourly slots starting strictly after the current hour", async () => {
    const { container } = await renderTier("m");
    const slots = screen.getAllByTestId("hourly-slot");
    expect(slots).toHaveLength(5);
    expect(slots[0].querySelector("[data-testid=hourly-hour]")?.textContent).toBe("16");
    expect(slots[0].textContent).toContain("116°");
    expect(slots[4].querySelector("[data-testid=hourly-hour]")?.textContent).toBe("20");
    expect(container.textContent).toContain("63%");
    expect(screen.queryAllByTestId("daily-row")).toHaveLength(0);
  });

  it("l shows 5 daily rows after today", async () => {
    await renderTier("l");
    expect(screen.getAllByTestId("hourly-slot")).toHaveLength(5);
    const rows = screen.getAllByTestId("daily-row");
    expect(rows).toHaveLength(5);
    expect(rows[0].textContent).toContain("10°");
    expect(rows[0].textContent).toContain("22°");
    expect(rows[4].textContent).toContain("26°");
  });
});
