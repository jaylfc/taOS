import { describe, it, expect } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { weatherIconName, weatherIconSvg } from "../weather-icons";

const CODES = [0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99, 12345];

describe("weather icons", () => {
  it("every mapped name has a raw SVG", () => {
    for (const code of CODES) {
      for (const isDay of [true, false]) {
        const name = weatherIconName(code, isDay);
        expect(weatherIconSvg(name, true).startsWith("<svg"), `${code}/${isDay} -> ${name}`).toBe(true);
        if (code === 12345) expect(name).toBe("not-available");
        else expect(name, `${code}/${isDay}`).not.toBe("not-available");
      }
    }
  });

  it("day and night differ for clear sky", () => {
    expect(weatherIconName(0, true)).toBe("clear-day");
    expect(weatherIconName(0, false)).toBe("clear-night");
  });

  it("strips animation elements when not animated", () => {
    expect(weatherIconSvg("rain", true)).toContain("<animate");
    const still = weatherIconSvg("rain", false);
    expect(still).toContain("<svg");
    expect(still).not.toMatch(/<animate|<set[\s>]/);
  });

  it("vendors the MIT license", () => {
    const rel = "src/components/widgets/weather-icons/LICENSE";
    const path = existsSync(rel) ? rel : `desktop/${rel}`;
    const text = readFileSync(path, "utf8");
    expect(text).toContain("MIT License");
    expect(text).toContain("Bas Milius");
  });
});
