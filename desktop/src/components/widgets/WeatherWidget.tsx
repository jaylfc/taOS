import { useState, useEffect } from "react";
import { getHomeLocation, getTempUnit, getWindUnit, cToF, kmhToMph, UNIT_CHANGED_EVENT } from "@/apps/WeatherApp";
import { useWidgetSize } from "@/hooks/use-widget-size";
import { useThemeStore } from "@/stores/theme-store";
import { WeatherIcon, weatherIconName } from "./weather-icons";

interface Weather {
  temp: number;
  feelsLike: number;
  condition: string;
  code: number;
  isDay: boolean;
  humidity: number;
  wind: number;
  location: string;
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

async function fetchWeather(): Promise<Weather | null> {
  const home = getHomeLocation();
  if (!home) return null;
  try {
    const params = new URLSearchParams({
      latitude: String(home.latitude),
      longitude: String(home.longitude),
      current: "temperature_2m,apparent_temperature,is_day,weather_code,relative_humidity_2m,wind_speed_10m",
      timezone: "auto",
    });
    const resp = await fetch(`https://api.open-meteo.com/v1/forecast?${params}`, { signal: AbortSignal.timeout(5000) });
    if (!resp.ok) return null;
    const data = await resp.json();
    const info = codeInfo(data.current.weather_code);
    return {
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
  const [noHome, setNoHome] = useState(!getHomeLocation());
  const [tempUnit, setTempUnit] = useState(getTempUnit);
  const [windUnit, setWindUnit] = useState(getWindUnit);
  const [containerRef, { tier }] = useWidgetSize();
  const reduceEffects = useThemeStore((s) => s.reduceEffects);

  useEffect(() => {
    const load = () => {
      const home = getHomeLocation();
      setNoHome(!home);
      if (home) fetchWeather().then(setWeather);
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

  return (
    <div
      ref={containerRef}
      style={{ height: "100%", display: "flex", flexDirection: "column", padding: tier === "s" ? "0 4px" : "2px 4px 6px", overflow: "hidden" }}
      aria-label={`Weather: ${weather.condition}, ${displayTemp(weather.temp)}°${tempUnit} in ${weather.location}`}
      role="region"
    >
      {tier === "s" && (
        /* Small: icon + temp + compact wind/humidity row */
        <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between", alignItems: "center", height: "100%", padding: "4px 2px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <WeatherIcon name={weatherIconName(weather.code, weather.isDay)} animated={!reduceEffects} label={weather.condition} size={52} />
            <span style={{ fontSize: "1.6rem", fontWeight: 600, color: "rgba(255,255,255,0.95)", lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>
              {displayTemp(weather.temp)}°
            </span>
          </div>
          <div style={{ display: "flex", gap: 8, fontSize: "0.65rem", color: "rgba(255,255,255,0.4)" }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 2 }}><WeatherIcon name="humidity" size={tier === "s" ? 18 : 22} animated={false} label="Humidity" />{weather.humidity}%</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 2 }}><WeatherIcon name="wind" size={tier === "s" ? 18 : 22} animated={false} label="Wind" />{displayWind(weather.wind)}</span>
          </div>
        </div>
      )}

      {tier === "m" && (
        /* Medium: icon + temp, condition, location, + compact detail row */
        <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between", height: "100%" }}>
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <WeatherIcon name={weatherIconName(weather.code, weather.isDay)} animated={!reduceEffects} label={weather.condition} size={64} />
              <div>
                <div style={{ fontSize: "1.8rem", fontWeight: 600, color: "rgba(255,255,255,0.95)", lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>
                  {displayTemp(weather.temp)}°<span style={{ fontSize: "0.85rem", fontWeight: 400, color: "rgba(255,255,255,0.4)", marginLeft: 1 }}>{tempUnit}</span>
                </div>
                <div style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.5)", marginTop: 2 }}>{weather.condition}</div>
              </div>
            </div>
          </div>
          <div style={{ display: "flex", gap: 10, fontSize: "0.7rem", color: "rgba(255,255,255,0.4)" }}>
            <span>Feels {displayTemp(weather.feelsLike)}°</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 2 }}><WeatherIcon name="humidity" size={22} animated={false} label="Humidity" />{weather.humidity}%</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 2 }}><WeatherIcon name="wind" size={22} animated={false} label="Wind" />{displayWind(weather.wind)}</span>
          </div>
          <div style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.35)", textTransform: "uppercase", letterSpacing: "0.04em", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {weather.location}
          </div>
        </div>
      )}

      {tier === "l" && (
        /* Large: full detail, no empty space */
        <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between", height: "100%" }}>
          {/* Top: location label */}
          <div style={{ fontSize: "0.65rem", fontWeight: 600, color: "rgba(255,255,255,0.35)", textTransform: "uppercase", letterSpacing: "0.06em", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {weather.location}
          </div>

          {/* Middle: big icon + temp */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "4px 0" }}>
            <WeatherIcon name={weatherIconName(weather.code, weather.isDay)} animated={!reduceEffects} label={weather.condition} size={84} />
            <div>
              <div style={{ fontSize: "2.4rem", fontWeight: 600, color: "rgba(255,255,255,0.95)", lineHeight: 1, fontVariantNumeric: "tabular-nums", letterSpacing: "-0.02em" }}>
                {displayTemp(weather.temp)}°<span style={{ fontSize: "1rem", fontWeight: 400, color: "rgba(255,255,255,0.4)", marginLeft: 2 }}>{tempUnit}</span>
              </div>
              <div style={{ fontSize: "0.8rem", color: "rgba(255,255,255,0.55)", marginTop: 3 }}>{weather.condition}</div>
            </div>
          </div>

          {/* Bottom: detail row */}
          <div
            style={{
              display: "flex", justifyContent: "space-between",
              background: "rgba(255,255,255,0.05)", borderRadius: 8,
              padding: "6px 10px", gap: 4,
            }}
          >
            {[
              { icon: "thermometer", alt: "Feels like", label: "Feels", value: `${displayTemp(weather.feelsLike)}°` },
              { icon: "humidity", alt: "Humidity", label: "Humidity", value: `${weather.humidity}%` },
              { icon: "wind", alt: "Wind", label: "Wind", value: displayWind(weather.wind) },
            ].map(({ icon, alt, label, value }) => (
              <div key={label} style={{ display: "flex", flexDirection: "column", alignItems: "center", flex: 1 }}>
                <WeatherIcon name={icon} size={22} animated={false} label={alt} />
                <span style={{ fontSize: "0.72rem", fontWeight: 600, color: "rgba(255,255,255,0.8)", fontVariantNumeric: "tabular-nums" }}>{value}</span>
                <span style={{ fontSize: "0.58rem", color: "rgba(255,255,255,0.3)", textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
