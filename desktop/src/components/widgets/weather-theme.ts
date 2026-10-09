export type ConditionGroup = "clear" | "partly" | "cloudy" | "fog" | "rain" | "snow" | "storm";

export function conditionGroup(code: number): ConditionGroup {
  if (code === 0) return "clear";
  if (code === 1 || code === 2) return "partly";
  if (code === 3) return "cloudy";
  if (code === 45 || code === 48) return "fog";
  if ((code >= 51 && code <= 67) || (code >= 80 && code <= 82)) return "rain";
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return "snow";
  if (code >= 95 && code <= 99) return "storm";
  return "cloudy";
}

type Stops = [string, string];

// Muted, dark-enough pairs: every stop has relative luminance <= 0.18 so white
// text reaches 4.5:1 on both ends. Night variants are darker and bluer.
export const WEATHER_GRADIENTS: Record<ConditionGroup, { day: Stops; night: Stops }> = {
  clear: { day: ["#2f6fa8", "#1f4f80"], night: ["#14233f", "#0a1228"] },
  partly: { day: ["#3f6f98", "#2b5073"], night: ["#1a2842", "#0e1629"] },
  cloudy: { day: ["#56667a", "#3c4a5c"], night: ["#222c3d", "#141b28"] },
  fog: { day: ["#5d6b78", "#434f5a"], night: ["#252e3a", "#171d27"] },
  rain: { day: ["#3f5870", "#2a3d52"], night: ["#17243a", "#0c1522"] },
  snow: { day: ["#5a7088", "#415468"], night: ["#202c42", "#121a2b"] },
  storm: { day: ["#3d3f66", "#282a4a"], night: ["#17172e", "#0b0b1a"] },
};

export function weatherGradient(group: ConditionGroup, isDay: boolean): string {
  const [a, b] = WEATHER_GRADIENTS[group][isDay ? "day" : "night"];
  return `linear-gradient(180deg, ${a}, ${b})`;
}
