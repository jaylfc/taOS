import { useState, useEffect, useCallback } from "react";
import { Search, MapPin, Home, Check } from "lucide-react";

interface Location {
  name: string;
  country: string;
  admin1?: string;
  latitude: number;
  longitude: number;
}

interface CurrentWeather {
  temperature: number;
  feelsLike: number;
  weatherCode: number;
  humidity: number;
  windSpeed: number;
  windDirection: number;
  isDay: boolean;
}

interface DailyForecast {
  date: string;
  tempMax: number;
  tempMin: number;
  weatherCode: number;
  precipitation: number;
}

const WEATHER_CODES: Record<number, { label: string; icon: string }> = {
  0: { label: "Clear sky", icon: "☀️" },
  1: { label: "Mainly clear", icon: "🌤" },
  2: { label: "Partly cloudy", icon: "⛅" },
  3: { label: "Overcast", icon: "☁️" },
  45: { label: "Fog", icon: "🌫" },
  48: { label: "Rime fog", icon: "🌫" },
  51: { label: "Light drizzle", icon: "🌦" },
  53: { label: "Drizzle", icon: "🌦" },
  55: { label: "Heavy drizzle", icon: "🌧" },
  61: { label: "Light rain", icon: "🌦" },
  63: { label: "Rain", icon: "🌧" },
  65: { label: "Heavy rain", icon: "🌧" },
  71: { label: "Light snow", icon: "🌨" },
  73: { label: "Snow", icon: "🌨" },
  75: { label: "Heavy snow", icon: "❄️" },
  77: { label: "Snow grains", icon: "🌨" },
  80: { label: "Rain showers", icon: "🌦" },
  81: { label: "Rain showers", icon: "🌧" },
  82: { label: "Heavy showers", icon: "⛈" },
  85: { label: "Snow showers", icon: "🌨" },
  86: { label: "Heavy snow showers", icon: "❄️" },
  95: { label: "Thunderstorm", icon: "⛈" },
  96: { label: "Thunderstorm + hail", icon: "⛈" },
  99: { label: "Severe thunderstorm", icon: "⛈" },
};

function codeInfo(code: number, isDay = true) {
  const info = WEATHER_CODES[code] ?? { label: "Unknown", icon: "🌤" };
  if ((code === 0 || code === 1) && !isDay) return { ...info, icon: "🌙" };
  return info;
}

// Weather preferences live under /api/preferences/weather so they
// follow the user across devices. The local-cache keys below are only
// used to avoid a flash of empty weather on first paint before the
// server fetch completes; the server is authoritative.
const WEATHER_PREF_NAMESPACE = "weather";
const WEATHER_PREF_CACHE = "taos-pref:weather";

export type TempUnit = "C" | "F";
export type WindUnit = "kmh" | "mph";

interface WeatherPrefs {
  home?: Location | null;
  tempUnit?: TempUnit;
  windUnit?: WindUnit;
}

function readCachedPrefs(): WeatherPrefs {
  try {
    const raw = localStorage.getItem(WEATHER_PREF_CACHE);
    return raw ? (JSON.parse(raw) as WeatherPrefs) : {};
  } catch {
    return {};
  }
}

export function getHomeLocation(): Location | null {
  return readCachedPrefs().home ?? null;
}

export function getTempUnit(): TempUnit {
  return readCachedPrefs().tempUnit === "F" ? "F" : "C";
}

export function getWindUnit(): WindUnit {
  return readCachedPrefs().windUnit === "mph" ? "mph" : "kmh";
}

export function cToF(c: number): number {
  return Math.round(c * 9 / 5 + 32);
}

export function kmhToMph(kmh: number): number {
  return Math.round(kmh * 0.621371);
}

// Fire a custom event so widgets on the same page can refresh without
// waiting for a full window reload. localStorage 'storage' events don't
// fire in the same window that made the change.
const UNIT_CHANGED_EVENT = "taos-weather-units-changed";
function emitUnitChange() {
  window.dispatchEvent(new Event(UNIT_CHANGED_EVENT));
}
export { UNIT_CHANGED_EVENT };

async function searchLocations(query: string): Promise<Location[]> {
  if (!query.trim()) return [];
  try {
    const resp = await fetch(`https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(query)}&count=10&language=en&format=json`);
    if (!resp.ok) return [];
    const data = await resp.json();
    return (data.results ?? []).map((r: Record<string, unknown>) => ({
      name: r.name as string,
      country: r.country as string,
      admin1: r.admin1 as string | undefined,
      latitude: r.latitude as number,
      longitude: r.longitude as number,
    }));
  } catch {
    return [];
  }
}

async function fetchForecast(loc: Location) {
  const params = new URLSearchParams({
    latitude: String(loc.latitude),
    longitude: String(loc.longitude),
    current: "temperature_2m,apparent_temperature,is_day,weather_code,relative_humidity_2m,wind_speed_10m,wind_direction_10m",
    daily: "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
    timezone: "auto",
    forecast_days: "7",
  });
  const resp = await fetch(`https://api.open-meteo.com/v1/forecast?${params}`);
  if (!resp.ok) return null;
  const data = await resp.json();
  const current: CurrentWeather = {
    temperature: Math.round(data.current.temperature_2m),
    feelsLike: Math.round(data.current.apparent_temperature),
    weatherCode: data.current.weather_code,
    humidity: data.current.relative_humidity_2m,
    windSpeed: Math.round(data.current.wind_speed_10m),
    windDirection: data.current.wind_direction_10m,
    isDay: data.current.is_day === 1,
  };
  const daily: DailyForecast[] = data.daily.time.map((date: string, i: number) => ({
    date,
    tempMax: Math.round(data.daily.temperature_2m_max[i]),
    tempMin: Math.round(data.daily.temperature_2m_min[i]),
    weatherCode: data.daily.weather_code[i],
    precipitation: data.daily.precipitation_sum[i],
  }));
  return { current, daily };
}

// Sync preferences to the server so the same location / units follow the
// user across devices. localStorage is only an immediate-paint cache.
async function saveWeatherPrefs(prefs: WeatherPrefs): Promise<void> {
  try {
    localStorage.setItem(WEATHER_PREF_CACHE, JSON.stringify(prefs));
  } catch {
    // quota or disabled — fine, server is still authoritative
  }
  try {
    await fetch(`/api/preferences/${WEATHER_PREF_NAMESPACE}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(prefs),
    });
  } catch {
    // network error — cached locally, will sync when next mutation runs
  }
}

export function WeatherApp() {
  const [home, setHome] = useState<Location | null>(getHomeLocation);
  const [viewing, setViewing] = useState<Location | null>(home);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<Location[]>([]);
  const [searching, setSearching] = useState(false);
  const [forecast, setForecast] = useState<{ current: CurrentWeather; daily: DailyForecast[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [tempUnit, setTempUnit] = useState<TempUnit>(getTempUnit);
  const [windUnit, setWindUnit] = useState<WindUnit>(getWindUnit);

  // Hydrate from server on mount — overrides any stale local cache so a
  // fresh device shows the location the user set on their phone.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch(`/api/preferences/${WEATHER_PREF_NAMESPACE}`);
        if (!resp.ok) return;
        const data = (await resp.json()) as WeatherPrefs;
        if (cancelled || !data || Object.keys(data).length === 0) return;
        if (data.home) {
          setHome(data.home);
          setViewing((cur) => cur ?? data.home ?? null);
        }
        if (data.tempUnit === "C" || data.tempUnit === "F") setTempUnit(data.tempUnit);
        if (data.windUnit === "kmh" || data.windUnit === "mph") setWindUnit(data.windUnit);
        localStorage.setItem(WEATHER_PREF_CACHE, JSON.stringify(data));
        emitUnitChange();
      } catch {
        // ignore — local cache is already loaded
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const toggleTempUnit = useCallback(() => {
    const next: TempUnit = tempUnit === "C" ? "F" : "C";
    setTempUnit(next);
    saveWeatherPrefs({ home, tempUnit: next, windUnit });
    emitUnitChange();
  }, [tempUnit, home, windUnit]);

  const toggleWindUnit = useCallback(() => {
    const next: WindUnit = windUnit === "kmh" ? "mph" : "kmh";
    setWindUnit(next);
    saveWeatherPrefs({ home, tempUnit, windUnit: next });
    emitUnitChange();
  }, [windUnit, home, tempUnit]);


  const loadForecast = useCallback(async (loc: Location) => {
    setLoading(true);
    const data = await fetchForecast(loc);
    setForecast(data);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (viewing) loadForecast(viewing);
  }, [viewing, loadForecast]);

  useEffect(() => {
    if (!query.trim()) {
      setSearchResults([]);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const handle = setTimeout(async () => {
      const results = await searchLocations(query);
      if (!cancelled) {
        setSearchResults(results);
        setSearching(false);
      }
    }, 300);
    return () => { cancelled = true; clearTimeout(handle); };
  }, [query]);

  const selectLocation = (loc: Location) => {
    setViewing(loc);
    setQuery("");
    setSearchResults([]);
  };

  const setAsHome = (loc: Location) => {
    setHome(loc);
    saveWeatherPrefs({ home: loc, tempUnit, windUnit });
  };

  const info = forecast ? codeInfo(forecast.current.weatherCode, forecast.current.isDay) : null;
  const isHome = viewing && home && viewing.latitude === home.latitude && viewing.longitude === home.longitude;

  return (
    <div className="flex h-full flex-col overflow-hidden bg-shell-bg text-shell-text select-none">
      {/* Search bar */}
      <div className="flex-none border-b border-shell-border p-4">
        <div className="relative">
          <Search
            size={16}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-shell-text-tertiary"
          />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search for a town or city..."
            aria-label="Search for a town or city"
            className="w-full rounded-xl border border-shell-border-strong bg-white/5 py-2.5 pl-9 pr-3 text-[14px] text-shell-text outline-none transition-colors placeholder:text-shell-text-tertiary focus-visible:border-accent/40 focus-visible:ring-2 focus-visible:ring-accent/30"
          />
          {searchResults.length > 0 && (
            <div className="absolute left-0 right-0 top-full z-10 mt-1.5 max-h-[280px] overflow-y-auto rounded-xl border border-shell-border-strong bg-shell-bg-glass shadow-[var(--shadow-card-hover)] backdrop-blur-xl">
              {searchResults.map((loc, i) => (
                <button
                  key={`${loc.latitude}-${loc.longitude}-${i}`}
                  onClick={() => selectLocation(loc)}
                  className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-[13px] transition-colors hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30"
                >
                  <MapPin size={14} className="flex-none text-shell-text-tertiary" />
                  <span className="flex-1 truncate">
                    <span className="text-shell-text">{loc.name}</span>
                    <span className="ml-1.5 text-shell-text-secondary">
                      {loc.admin1 ? `${loc.admin1}, ` : ""}
                      {loc.country}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          )}
          {searching && query && searchResults.length === 0 && (
            <div className="absolute left-0 right-0 top-full z-10 mt-1.5 rounded-xl border border-shell-border-strong bg-shell-bg-glass px-3.5 py-3 text-[13px] text-shell-text-secondary backdrop-blur-xl">
              Searching...
            </div>
          )}
        </div>
      </div>

      {/* Main content. Extra bottom inset so the last forecast row clears the
          phone home indicator / dock edge. env(safe-area-inset-bottom) is 0 on
          desktop, so the layout is unchanged there. */}
      <div
        className="flex-1 overflow-y-auto p-5"
        style={{ paddingBottom: "calc(1.25rem + env(safe-area-inset-bottom, 0px))" }}
      >
        {!viewing && (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-shell-text-secondary">
            <MapPin size={48} className="opacity-30" />
            <p className="text-[15px]">Search for a location to see its weather</p>
          </div>
        )}

        {viewing && loading && !forecast && (
          <div className="mt-10 text-center text-shell-text-secondary">
            Loading forecast...
          </div>
        )}

        {viewing && forecast && info && (
          <>
            {/* Location header */}
            <div className="mb-5 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <h1 className="truncate text-[24px] font-bold tracking-[-0.02em] text-shell-text">
                  {viewing.name}
                </h1>
                <p className="mt-0.5 truncate text-[13px] text-shell-text-secondary">
                  {viewing.admin1 ? `${viewing.admin1}, ` : ""}
                  {viewing.country}
                </p>
              </div>
              <button
                onClick={() => setAsHome(viewing)}
                disabled={!!isHome}
                className={`flex flex-none items-center gap-1.5 rounded-lg border px-3.5 py-2 text-[12px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 ${
                  isHome
                    ? "cursor-default border-shell-border-strong bg-white/10 text-shell-text"
                    : "border-shell-border-strong bg-white/5 text-shell-text-secondary hover:bg-white/10 hover:text-shell-text"
                }`}
              >
                {isHome ? (
                  <>
                    <Check size={14} /> Home
                  </>
                ) : (
                  <>
                    <Home size={14} /> Set as home
                  </>
                )}
              </button>
            </div>

            {/* Current */}
            <div className="mb-7 flex items-center gap-4 rounded-2xl border border-shell-border bg-white/5 px-5 py-4">
              <div className="text-[72px] leading-none">{info.icon}</div>
              <div className="min-w-0 flex-1">
                <button
                  onClick={toggleTempUnit}
                  title="Tap to toggle °C / °F"
                  className="flex items-baseline gap-2 rounded-md transition-colors hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30"
                >
                  <span className="text-[56px] font-light leading-none tabular-nums text-shell-text">
                    {tempUnit === "C"
                      ? forecast.current.temperature
                      : cToF(forecast.current.temperature)}
                    °
                  </span>
                  <span className="text-[18px] text-shell-text-secondary">
                    {tempUnit}
                  </span>
                </button>
                <p className="mt-1 text-[14px] text-shell-text">{info.label}</p>
                <p className="mt-0.5 text-[12px] text-shell-text-secondary">
                  Feels like{" "}
                  {tempUnit === "C"
                    ? forecast.current.feelsLike
                    : cToF(forecast.current.feelsLike)}
                  °
                </p>
              </div>
            </div>

            {/* Stats */}
            <div className="mb-7 grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-2.5">
              <div className="rounded-2xl border border-shell-border bg-white/5 p-3.5">
                <div className="text-[10px] uppercase tracking-[0.5px] text-shell-text-tertiary">
                  Humidity
                </div>
                <div className="mt-1 text-[20px] font-semibold tabular-nums text-shell-text">
                  {forecast.current.humidity}%
                </div>
              </div>
              <button
                onClick={toggleWindUnit}
                title="Tap to toggle km/h / mph"
                className="rounded-2xl border border-shell-border bg-white/5 p-3.5 text-left transition-colors hover:bg-white/10 hover:border-shell-border-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30"
              >
                <div className="text-[10px] uppercase tracking-[0.5px] text-shell-text-tertiary">
                  Wind
                </div>
                <div className="mt-1 text-[20px] font-semibold tabular-nums text-shell-text">
                  {windUnit === "kmh"
                    ? forecast.current.windSpeed
                    : kmhToMph(forecast.current.windSpeed)}
                  <span className="ml-1 text-[13px] font-normal text-shell-text-secondary">
                    {windUnit === "kmh" ? "km/h" : "mph"}
                  </span>
                </div>
              </button>
            </div>

            {/* 7-day forecast */}
            <div>
              <h2 className="mb-2.5 text-[11px] font-semibold uppercase tracking-[0.8px] text-shell-text-tertiary">
                7-Day Forecast
              </h2>
              <div className="overflow-hidden rounded-2xl border border-shell-border bg-white/3">
                {forecast.daily.map((day, i) => {
                  const dayInfo = codeInfo(day.weatherCode);
                  const dateObj = new Date(day.date);
                  const label =
                    i === 0
                      ? "Today"
                      : dateObj.toLocaleDateString("en", { weekday: "short" });
                  return (
                    <div
                      key={day.date}
                      className={`flex items-center gap-3.5 px-4 py-3 transition-colors hover:bg-white/5 ${
                        i === 0 ? "" : "border-t border-shell-border"
                      }`}
                    >
                      <span className="w-[52px] text-[13px] font-medium text-shell-text">
                        {label}
                      </span>
                      <span className="w-8 text-center text-[22px]">
                        {dayInfo.icon}
                      </span>
                      <span className="flex-1 truncate text-[12px] text-shell-text-secondary">
                        {dayInfo.label}
                      </span>
                      <span className="w-9 text-right text-[13px] tabular-nums text-shell-text-secondary">
                        {tempUnit === "C" ? day.tempMin : cToF(day.tempMin)}°
                      </span>
                      <span className="w-9 text-right text-[13px] font-medium tabular-nums text-shell-text">
                        {tempUnit === "C" ? day.tempMax : cToF(day.tempMax)}°
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
