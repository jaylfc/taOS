import { describe, it, expect } from "vitest";
import { conditionGroup, weatherGradient, WEATHER_GRADIENTS } from "../weather-theme";

function parseColor(s: string): [number, number, number] {
  const hex = s.match(/^#([0-9a-f]{6})$/i);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const rgb = s.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
  if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])];
  throw new Error(`unparseable colour: ${s}`);
}

function luminance([r, g, b]: [number, number, number]): number {
  const lin = (v: number) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

describe("conditionGroup", () => {
  it("maps WMO codes to groups", () => {
    expect(conditionGroup(0)).toBe("clear");
    expect(conditionGroup(2)).toBe("partly");
    expect(conditionGroup(3)).toBe("cloudy");
    expect(conditionGroup(45)).toBe("fog");
    expect(conditionGroup(61)).toBe("rain");
    expect(conditionGroup(82)).toBe("rain");
    expect(conditionGroup(73)).toBe("snow");
    expect(conditionGroup(95)).toBe("storm");
    expect(conditionGroup(4242)).toBe("cloudy");
  });
});

describe("weather gradients", () => {
  it("has 14 entries (7 groups x day/night)", () => {
    const stops = Object.values(WEATHER_GRADIENTS).flatMap((g) => [g.day, g.night]);
    expect(stops).toHaveLength(14);
  });

  it("every stop keeps white text at 4.5:1 or better (luminance <= 0.18)", () => {
    for (const [group, g] of Object.entries(WEATHER_GRADIENTS)) {
      for (const [when, pair] of Object.entries(g)) {
        for (const stop of pair as string[]) {
          expect(luminance(parseColor(stop)), `${group}/${when} ${stop}`).toBeLessThanOrEqual(0.18);
        }
      }
    }
  });

  it("weatherGradient returns a 180deg linear-gradient", () => {
    expect(weatherGradient("rain", true)).toMatch(/^linear-gradient\(180deg, .+, .+\)$/);
  });

  it("clear day differs from clear night", () => {
    expect(weatherGradient("clear", true)).not.toBe(weatherGradient("clear", false));
  });
});
