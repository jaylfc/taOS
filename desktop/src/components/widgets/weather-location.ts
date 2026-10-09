import { getHomeLocation } from "@/apps/WeatherApp";

export interface WeatherLocation {
  latitude: number;
  longitude: number;
  name: string;
  source: "home" | "device" | "browser";
}

const CURRENT = "Current location";
const GEO_TIMEOUT_MS = 8000;
// Some browsers never call either geolocation callback; this guard ends the wait.
const GEO_GUARD_MS = 8500;

const inRange = (lat: unknown, lon: unknown): lat is number =>
  typeof lat === "number" && typeof lon === "number" &&
  Number.isFinite(lat) && Number.isFinite(lon) &&
  Math.abs(lat) <= 90 && Math.abs(lon) <= 180;

async function fromHome(): Promise<WeatherLocation | null> {
  const home = getHomeLocation();
  return home ? { latitude: home.latitude, longitude: home.longitude, name: home.name, source: "home" } : null;
}

// The handset's GPS fix from taos-locationd. Coordinates are personal data:
// never logged, never persisted.
async function fromDevice(): Promise<WeatherLocation | null> {
  try {
    const resp = await fetch("/api/system/location", { signal: AbortSignal.timeout(3000) });
    if (!resp.ok) return null;
    const body = await resp.json();
    const fix = body?.available === true ? body.fix : null;
    if (!fix || !inRange(fix.lat, fix.lon)) return null;
    return { latitude: fix.lat, longitude: fix.lon, name: CURRENT, source: "device" };
  } catch {
    return null;
  }
}

function fromBrowser(): Promise<WeatherLocation | null> {
  const geo = typeof navigator !== "undefined" ? navigator.geolocation : undefined;
  if (!geo) return Promise.resolve(null);
  return new Promise((resolve) => {
    const guard = setTimeout(() => resolve(null), GEO_GUARD_MS);
    const done = (loc: WeatherLocation | null) => { clearTimeout(guard); resolve(loc); };
    geo.getCurrentPosition(
      ({ coords }) => done(inRange(coords.latitude, coords.longitude)
        ? { latitude: coords.latitude, longitude: coords.longitude, name: CURRENT, source: "browser" }
        : null),
      () => done(null),
      { timeout: GEO_TIMEOUT_MS, maximumAge: 600_000 },
    );
  });
}

// First hit wins. A fresh device fix beats the saved home so a phone shows
// where it is (Jay 2026-10-09); the route drops fixes older than 30 min.
const CHAIN = [fromDevice, fromHome, fromBrowser];

export async function resolveWeatherLocation(): Promise<WeatherLocation | null> {
  for (const step of CHAIN) {
    const loc = await step();
    if (loc) return loc;
  }
  return null;
}
