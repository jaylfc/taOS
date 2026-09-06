import { useState, useEffect } from "react";
import { useWidgetSize } from "@/hooks/use-widget-size";

export function ClockWidget() {
  const [now, setNow] = useState(new Date());
  const [containerRef, { tier }] = useWidgetSize();

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const timeHM = now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const timeHMS = now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const dateLong = now.toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" });
  const dateMed = now.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  const weekday = now.toLocaleDateString(undefined, { weekday: "long" });

  return (
    <div
      ref={containerRef}
      style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: tier === "s" ? 0 : 4 }}
      aria-label="Clock widget"
      role="region"
    >
      {tier === "s" && (
        <span
          style={{ fontSize: "1.8rem", fontWeight: 600, fontVariantNumeric: "tabular-nums", color: "rgba(255,255,255,0.95)", lineHeight: 1, letterSpacing: "-0.02em" }}
          aria-live="polite"
          aria-label={`Current time: ${timeHM}`}
        >
          {timeHM}
        </span>
      )}

      {tier === "m" && (
        <>
          <span
            style={{ fontSize: "2.4rem", fontWeight: 600, fontVariantNumeric: "tabular-nums", color: "rgba(255,255,255,0.95)", lineHeight: 1, letterSpacing: "-0.03em" }}
            aria-live="polite"
            aria-label={`Current time: ${timeHMS}`}
          >
            {timeHMS}
          </span>
          <span style={{ fontSize: "0.7rem", color: "rgba(255,255,255,0.45)", letterSpacing: "0.03em", marginTop: 4 }}>
            {dateMed}
          </span>
        </>
      )}

      {tier === "l" && (
        <>
          <span
            style={{ fontSize: "2.8rem", fontWeight: 600, fontVariantNumeric: "tabular-nums", color: "rgba(255,255,255,0.95)", lineHeight: 1, letterSpacing: "-0.03em" }}
            aria-live="polite"
            aria-label={`Current time: ${timeHMS}`}
          >
            {timeHMS}
          </span>
          <span style={{ fontSize: "0.85rem", fontWeight: 600, color: "rgba(255,255,255,0.6)", letterSpacing: "0.08em", textTransform: "uppercase", marginTop: 6 }}>
            {weekday}
          </span>
          <span style={{ fontSize: "0.7rem", color: "rgba(255,255,255,0.35)", marginTop: 2 }}>
            {dateLong}
          </span>
        </>
      )}
    </div>
  );
}
