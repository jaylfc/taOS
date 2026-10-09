import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

let mockHome: { name: string; latitude: number; longitude: number } | null = null;
vi.mock("@/apps/WeatherApp", () => ({ getHomeLocation: () => mockHome }));

import { resolveWeatherLocation } from "../weather-location";

const FIX = { lat: 51.5, lon: -0.12, accuracy_m: 10, fix_utc: "2026-10-09T15:00:00Z", source: "gps" };

function routeReturns(body: unknown, status = 200) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function setGeolocation(impl: ((ok: PositionCallback, err: PositionErrorCallback) => void) | undefined) {
  Object.defineProperty(navigator, "geolocation", {
    configurable: true,
    value: impl ? { getCurrentPosition: impl } : undefined,
  });
}

const browserAt = (latitude: number, longitude: number) => (ok: PositionCallback) =>
  ok({ coords: { latitude, longitude } } as GeolocationPosition);

beforeEach(() => {
  mockHome = null;
  setGeolocation(undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("resolveWeatherLocation", () => {
  it("prefers a fresh device fix over the saved home", async () => {
    mockHome = { name: "Testville", latitude: 1, longitude: 2 };
    routeReturns({ available: true, fix: FIX, age_s: 5 });
    const loc = await resolveWeatherLocation();
    expect(loc).toEqual({ latitude: 51.5, longitude: -0.12, name: "Current location", source: "device" });
  });

  it("uses the saved home when the device has no fix, before asking the browser", async () => {
    mockHome = { name: "Testville", latitude: 1, longitude: 2 };
    routeReturns({ available: true, fix: null, age_s: null });
    const getCurrentPosition = vi.fn();
    setGeolocation(getCurrentPosition);
    const loc = await resolveWeatherLocation();
    expect(loc).toEqual({ latitude: 1, longitude: 2, name: "Testville", source: "home" });
    expect(getCurrentPosition).toHaveBeenCalledTimes(0);
  });

  it("uses the device fix when there is no saved home", async () => {
    const fetchMock = routeReturns({ available: true, fix: FIX, age_s: 5 });
    const loc = await resolveWeatherLocation();
    expect(loc).toEqual({ latitude: 51.5, longitude: -0.12, name: "Current location", source: "device" });
    expect(fetchMock).toHaveBeenCalledWith("/api/system/location", expect.anything());
  });

  it("falls through to browser geolocation when the host has no location daemon", async () => {
    routeReturns({ available: false, fix: null, age_s: null });
    setGeolocation(browserAt(40, -3));
    const loc = await resolveWeatherLocation();
    expect(loc).toEqual({ latitude: 40, longitude: -3, name: "Current location", source: "browser" });
  });

  it.each([
    ["a server error", () => routeReturns({ detail: "boom" }, 500)],
    ["a thrown fetch", () => vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("offline"); }))],
    ["no fix yet", () => routeReturns({ available: true, fix: null, age_s: null })],
    ["an out-of-range latitude", () => routeReturns({ available: true, fix: { ...FIX, lat: 200 }, age_s: 5 })],
  ])("falls through past %s", async (_label, arrange) => {
    arrange();
    setGeolocation(browserAt(40, -3));
    const loc = await resolveWeatherLocation();
    expect(loc?.source).toBe("browser");
  });

  it("returns null when nothing knows the location", async () => {
    routeReturns({ available: false, fix: null, age_s: null });
    expect(await resolveWeatherLocation()).toBeNull();
  });

  it("returns null when geolocation never calls back", async () => {
    vi.useFakeTimers();
    routeReturns({ available: false, fix: null, age_s: null });
    setGeolocation(() => {});
    const pending = resolveWeatherLocation();
    await vi.advanceTimersByTimeAsync(9000);
    expect(await pending).toBeNull();
  });

  it("returns null when geolocation reports an error", async () => {
    routeReturns({ available: false, fix: null, age_s: null });
    setGeolocation((_ok, err) => err({ code: 1, message: "denied" } as GeolocationPositionError));
    expect(await resolveWeatherLocation()).toBeNull();
  });

  it("re-resolves to the saved home once the device fix goes stale", async () => {
    mockHome = { name: "Testville", latitude: 1, longitude: 2 };
    routeReturns({ available: true, fix: FIX, age_s: 5 });
    expect((await resolveWeatherLocation())?.source).toBe("device");
    routeReturns({ available: true, fix: null, age_s: 1900 });
    expect((await resolveWeatherLocation())?.source).toBe("home");
  });
});
