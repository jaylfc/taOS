import { useState, useEffect } from "react";
import { getTempUnit, getWindUnit, cToF, kmhToMph, UNIT_CHANGED_EVENT } from "@/apps/WeatherApp";
import { useWidgetSize } from "@/hooks/use-widget-size";
import { useThemeStore } from "@/stores/theme-store";
import { WeatherIcon, weatherIconName } from "./weather-icons";
import { conditionGroup, weatherGradient } from "./weather-theme";
import { resolveWeatherLocation, type WeatherLocation } from "./weather-location";

interface Weather {
  temp: number;
  feelsLike: number;
  condition: string;
  code: number;
  isDay: boolean;
  humidity: number;
  wind: number;
  location: string;
  high: number;
  low: number;
  hourly: { time: string; temp: number; code: number; isDay: boolean }[];
  daily: { date: string; low: number; high: number; code: number }[];
}

const WEATHER_CODES: Record<number, { label: string }> = {
  0: { label: "Clear" },
  1: { label: "Mainly clear" },
  2: { label: "Partly cloudy" },
  3: { label: "Overcast" },
  45: { label: "Fog" },
  48: { label: "Fog" },
  51: { label: "Drizzle" },
  53: { label: "Drizzle" },
  55: { label: "Drizzle" },
  61: { label: "Light rain" },
  63: { label: "Rain" },
  65: { label: "Heavy rain" },
  71: { label: "Light snow" },
  73: { label: "Snow" },
  75: { label: "Heavy snow" },
  80: { label: "Showers" },
  81: { label: "Showers" },
  82: { label: "Heavy showers" },
  95: { label: "Thunderstorm" },
  96: { label: "Thunderstorm" },
  99: { label: "Thunderstorm" },
};

function codeInfo(code: number) {
  return WEATHER_CODES[code] ?? { label: "Unknown" };
}

async function fetchWeather(home: WeatherLocation): Promise<Weather | null> {
  try {
    const params = new URLSearchParams({
      latitude: String(home.latitude),
      longitude: String(home.longitude),
      current: "temperature_2m,apparent_temperature,is_day,weather_code,relative_humidity_2m,wind_speed_10m",
      daily: "temperature_2m_max,temperature_2m_min,weather_code",
      hourly: "temperature_2m,weather_code,is_day",
      forecast_days: "6",
      timezone: "auto",
    });
    const resp = await fetch(`https://api.open-meteo.com/v1/forecast?${params}`, { signal: AbortSignal.timeout(5000) });
    if (!resp.ok) return null;
    const data = await resp.json();
    const info = codeInfo(data.current.weather_code);
    const arr = (v: unknown): any[] => (Array.isArray(v) ? v : []);
    const hTime = arr(data.hourly?.time);
    const hTemp = arr(data.hourly?.temperature_2m);
    const hCode = arr(data.hourly?.weather_code);
    const hDay = arr(data.hourly?.is_day);
    const now = String(data.current.time ?? "");
    // ISO local strings of equal shape compare correctly as text.
    const first = hTime.findIndex((t) => String(t) > now);
    const hourly: Weather["hourly"] = [];
    if (first >= 0) {
      for (let i = first; i < hTime.length && hourly.length < 5; i++) {
        if (typeof hTemp[i] !== "number") continue;
        hourly.push({ time: String(hTime[i]), temp: Math.round(hTemp[i]), code: hCode[i] ?? 3, isDay: hDay[i] === 1 });
      }
    }
    const dTime = arr(data.daily?.time);
    const dMax = arr(data.daily?.temperature_2m_max);
    const dMin = arr(data.daily?.temperature_2m_min);
    const dCode = arr(data.daily?.weather_code);
    const daily: Weather["daily"] = [];
    for (let i = 1; i < dTime.length && daily.length < 5; i++) {
      if (typeof dMax[i] !== "number" || typeof dMin[i] !== "number") continue;
      daily.push({ date: String(dTime[i]), low: Math.round(dMin[i]), high: Math.round(dMax[i]), code: dCode[i] ?? 3 });
    }
    return {
      high: typeof dMax[0] === "number" ? Math.round(dMax[0]) : Math.round(data.current.temperature_2m),
      low: typeof dMin[0] === "number" ? Math.round(dMin[0]) : Math.round(data.current.temperature_2m),
      hourly,
      daily,
      temp: Math.round(data.current.temperature_2m),
      feelsLike: Math.round(data.current.apparent_temperature),
      condition: info.label,
      code: data.current.weather_code,
      isDay: data.current.is_day === 1,
      humidity: data.current.relative_humidity_2m,
      wind: Math.round(data.current.wind_speed_10m),
      location: home.name,
    };
  } catch {
    return null;
  }
}

export function WeatherWidget() {
  const [weather, setWeather] = useState<Weather | null>(null);
  const [noHome, setNoHome] = useState(false);
  const [tempUnit, setTempUnit] = useState(getTempUnit);
  const [windUnit, setWindUnit] = useState(getWindUnit);
  const [containerRef, { tier, height }] = useWidgetSize();
  const reduceEffects = useThemeStore((s) => s.reduceEffects);

  useEffect(() => {
    const load = () => {
      resolveWeatherLocation().then((home) => {
        setNoHome(!home);
        if (home) fetchWeather(home).then(setWeather);
      });
    };
    const hydrate = async () => {
      try {
        const resp = await fetch("/api/preferences/weather");
        if (!resp.ok) { load(); return; }
        const data = await resp.json();
        if (data && typeof data === "object" && Object.keys(data).length > 0) {
          localStorage.setItem("taos-pref:weather", JSON.stringify(data));
          if (data.tempUnit === "C" || data.tempUnit === "F") setTempUnit(data.tempUnit);
          if (data.windUnit === "kmh" || data.windUnit === "mph") setWindUnit(data.windUnit);
        }
      } catch {
        // fall through to local cache
      }
      load();
    };
    hydrate();
    const timer = setInterval(load, 600_000);
    const onStorage = () => load();
    const onUnits = () => { setTempUnit(getTempUnit()); setWindUnit(getWindUnit()); };
    window.addEventListener("storage", onStorage);
    window.addEventListener(UNIT_CHANGED_EVENT, onUnits);
    return () => {
      clearInterval(timer);
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(UNIT_CHANGED_EVENT, onUnits);
    };
  }, []);

  const displayTemp = (c: number) => tempUnit === "C" ? c : cToF(c);
  const displayWind = (kmh: number) => windUnit === "kmh" ? `${kmh} km/h` : `${kmhToMph(kmh)} mph`;

  if (noHome) {
    return (
      <div
        ref={containerRef}
        style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 6, padding: 8, textAlign: "center" }}
        aria-label="Weather widget — no location set"
        role="region"
      >
        <WeatherIcon name="partly-cloudy-day" size={tier === "s" ? 44 : 56} animated={!reduceEffects} label="Weather" />
        <span style={{ fontSize: "0.7rem", color: "rgba(255,255,255,0.45)" }}>Set location</span>
      </div>
    );
  }

  if (!weather) {
    return (
      <div
        ref={containerRef}
        style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "rgba(255,255,255,0.3)", fontSize: "0.75rem" }}
        aria-label="Weather widget — loading"
        role="region"
      >
        Loading…
      </div>
    );
  }

  const t = displayTemp;
  const white = (a: number) => `rgba(255,255,255,${a})`;
  const iconSize = Math.max(24, Math.min(64, height - 16));
  const bg = weatherGradient(conditionGroup(weather.code), weather.isDay);
  const range = (() => {
    const lows = weather.daily.map((d) => d.low);
    const highs = weather.daily.map((d) => d.high);
    const min = Math.min(...lows);
    const max = Math.max(...highs);
    return { min, span: Math.max(1, max - min) };
  })();
  const weekday = (date: string) => {
    const d = new Date(`${date}T12:00:00`);
    return Number.isNaN(d.getTime()) ? date : d.toLocaleDateString(undefined, { weekday: "short" });
  };
  const hourLabel = (time: string) => time.slice(11, 13);

  return (
    <div
      ref={containerRef}
      style={{
        height: "100%", display: "flex", flexDirection: "column", justifyContent: "space-between", gap: 6,
        padding: "8px 12px", overflow: "hidden", borderRadius: 12, color: white(0.95),
        background: `linear-gradient(rgba(0,0,0,.2),rgba(0,0,0,.2)), ${bg}`,
        transition: reduceEffects ? "none" : "background 400ms",
        fontVariantNumeric: "tabular-nums",
      }}
      aria-label={`Weather: ${t(weather.temp)} degrees, ${weather.condition}, high ${t(weather.high)}, low ${t(weather.low)}, ${weather.location}`}
      role="region"
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <div>
          <div style={{ fontSize: "2rem", fontWeight: 600, lineHeight: 1 }}>{t(weather.temp)}°</div>
          <div style={{ fontSize: "0.7rem", color: white(0.7), marginTop: 2 }}>H {t(weather.high)}° L {t(weather.low)}°</div>
        </div>
        <WeatherIcon name={weatherIconName(weather.code, weather.isDay)} animated={!reduceEffects} label="" size={tier === "s" ? iconSize : 64} />
      </div>

      {tier !== "s" && (
        <div style={{ display: "flex", gap: 12, fontSize: "0.7rem", color: white(0.8) }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 2 }}><WeatherIcon name="humidity" size={20} animated={false} label="Humidity" />{weather.humidity}%</span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 2 }}><WeatherIcon name="wind" size={20} animated={false} label="Wind" />{displayWind(weather.wind)}</span>
        </div>
      )}

      {tier !== "s" && weather.hourly.length > 0 && (
        <div style={{ display: "flex", justifyContent: "space-between", gap: 4 }}>
          {weather.hourly.map((h) => (
            <div key={h.time} data-testid="hourly-slot" style={{ display: "flex", flexDirection: "column", alignItems: "center", flex: 1, fontSize: "0.7rem" }}>
              <span data-testid="hourly-hour" style={{ color: white(0.7) }}>{hourLabel(h.time)}</span>
              <WeatherIcon name={weatherIconName(h.code, h.isDay)} animated={!reduceEffects} label="" size={28} />
              <span style={{ fontWeight: 600 }}>{t(h.temp)}°</span>
            </div>
          ))}
        </div>
      )}

      {tier === "l" && weather.daily.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {weather.daily.map((d) => (
            <div key={d.date} data-testid="daily-row" style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.72rem" }}>
              <span style={{ width: 32, color: white(0.8) }}>{weekday(d.date)}</span>
              <WeatherIcon name={weatherIconName(d.code, true)} animated={!reduceEffects} label="" size={28} />
              <span style={{ width: 28, textAlign: "right", color: white(0.7) }}>{t(d.low)}°</span>
              <div style={{ position: "relative", flex: 1, height: 4, borderRadius: 2, background: white(0.2) }}>
                <div style={{
                  position: "absolute", top: 0, bottom: 0, borderRadius: 2, background: white(0.85),
                  left: `${((d.low - range.min) / range.span) * 100}%`,
                  width: `${Math.max(4, ((d.high - d.low) / range.span) * 100)}%`,
                }} />
              </div>
              <span style={{ width: 28, fontWeight: 600 }}>{t(d.high)}°</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
