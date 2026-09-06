import { useEffect, useState } from "react";

export function useClock() {
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const interval = setInterval(() => setTime(new Date()), 30_000);
    return () => clearInterval(interval);
  }, []);

  const formatted = time.toLocaleDateString("en-GB", {
    weekday: "short", day: "numeric", month: "short",
  }) + "  " + time.toLocaleTimeString("en-GB", {
    hour: "2-digit", minute: "2-digit",
  });

  return formatted;
}
