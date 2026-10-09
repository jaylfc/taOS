// Animated Meteocons (MIT, Bas Milius) vendored under ./weather-icons/.
const RAW = import.meta.glob("./weather-icons/*.svg", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

const pair = (isDay: boolean, day: string, night: string) => (isDay ? day : night);

/** Map a WMO weather code to a Meteocons icon name. */
export function weatherIconName(code: number, isDay: boolean): string {
  switch (code) {
    case 0: return pair(isDay, "clear-day", "clear-night");
    case 1:
    case 2: return pair(isDay, "partly-cloudy-day", "partly-cloudy-night");
    case 3: return "overcast";
    case 45:
    case 48: return pair(isDay, "fog-day", "fog-night");
    case 51:
    case 53: return pair(isDay, "partly-cloudy-day-drizzle", "partly-cloudy-night-drizzle");
    case 55: return "drizzle";
    case 56:
    case 57:
    case 66:
    case 67: return "sleet";
    case 61:
    case 80: return pair(isDay, "partly-cloudy-day-rain", "partly-cloudy-night-rain");
    case 63:
    case 65:
    case 81:
    case 82: return "rain";
    case 71: return pair(isDay, "partly-cloudy-day-snow", "partly-cloudy-night-snow");
    case 73:
    case 75:
    case 85:
    case 86: return "snow";
    case 77: return "snowflake";
    case 95: return pair(isDay, "thunderstorms-day", "thunderstorms-night");
    case 96:
    case 99: return pair(isDay, "thunderstorms-day-rain", "thunderstorms-night-rain");
    default: return "not-available";
  }
}

const ANIMATION_RE = /<(animate|animateTransform|animateMotion|set)(?=[\s/>])[^>]*?(?:\/>|>[\s\S]*?<\/\1\s*>|>)/g;

/** Raw SVG markup for an icon; when not animated, animation elements are removed. */
export function weatherIconSvg(name: string, animated: boolean): string {
  const svg = RAW[`./weather-icons/${name}.svg`] ?? RAW["./weather-icons/not-available.svg"] ?? "";
  return animated ? svg : svg.replace(ANIMATION_RE, "");
}

export function WeatherIcon({ name, size, animated, label }: { name: string; size: number; animated: boolean; label: string }) {
  const svg = weatherIconSvg(name, animated);
  return (
    <img
      src={"data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg)}
      width={size}
      height={size}
      alt={label}
      draggable={false}
    />
  );
}
