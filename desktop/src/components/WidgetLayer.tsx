import { useState, useEffect, useRef, useCallback } from "react";
import GridLayout from "react-grid-layout";
import type { Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import { Plus, X, Clock, Activity, StickyNote, Cpu, Cloud } from "lucide-react";
import { useWidgetStore } from "@/stores/widget-store";
import { ClockWidget } from "./widgets/ClockWidget";
import { AgentStatusWidget } from "./widgets/AgentStatusWidget";
import { QuickNotesWidget } from "./widgets/QuickNotesWidget";
import { SystemStatsWidget } from "./widgets/SystemStatsWidget";
import { WeatherWidget } from "./widgets/WeatherWidget";

const WIDGET_TYPES: { type: string; label: string; icon: React.ReactNode }[] = [
  { type: "clock", label: "Clock", icon: <Clock size={14} /> },
  { type: "agent-status", label: "Agent Status", icon: <Activity size={14} /> },
  { type: "quick-notes", label: "Quick Notes", icon: <StickyNote size={14} /> },
  { type: "system-stats", label: "System Stats", icon: <Cpu size={14} /> },
  { type: "weather", label: "Weather", icon: <Cloud size={14} /> },
];

function renderWidget(type: string) {
  switch (type) {
    case "clock":
      return <ClockWidget />;
    case "agent-status":
      return <AgentStatusWidget />;
    case "quick-notes":
      return <QuickNotesWidget />;
    case "system-stats":
      return <SystemStatsWidget />;
    case "weather":
      return <WeatherWidget />;
    default:
      return <div style={{ color: "rgba(255,255,255,0.4)", fontSize: "0.75rem", padding: 8 }}>Unknown widget</div>;
  }
}

export function WidgetLayer() {
  const { widgets, showWidgets, hydrated, addWidget, removeWidget, updateLayout } = useWidgetStore();
  const [pickerOpen, setPickerOpen] = useState(false);
  const [containerWidth, setContainerWidth] = useState(1200);
  const containerRef = useRef<HTMLDivElement>(null);
  const pickerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setContainerWidth(entry.contentRect.width);
      }
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!pickerOpen) return;
    function handleClick(e: MouseEvent) {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setPickerOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [pickerOpen]);

  const handleLayoutChange = useCallback(
    (layout: Layout[]) => {
      updateLayout(
        layout.map((l) => ({ id: l.i, x: l.x, y: l.y, w: l.w, h: l.h })),
      );
    },
    [updateLayout],
  );

  // Hide until the store has loaded from localStorage + resolved the server
  // fetch. Rendering before hydration shows DEFAULT_WIDGETS and then replaces
  // them, causing a visible flash + grid re-layout on mobile cold start.
  if (!hydrated || !showWidgets) return null;

  const gridLayout: Layout[] = widgets.map((w) => ({
    i: w.id,
    x: w.x,
    y: w.y,
    w: w.w,
    h: w.h,
    minW: w.minW ?? 2,
    minH: w.minH ?? 2,
  }));

  return (
    <div
      ref={containerRef}
      style={{
        position: "absolute",
        inset: 0,
        zIndex: 1,
        pointerEvents: "none",
      }}
    >
      <div style={{ pointerEvents: "auto", width: "100%", height: "100%" }}>
        <GridLayout
          className="widget-grid"
          layout={gridLayout}
          cols={12}
          rowHeight={72}
          width={containerWidth}
          margin={[16, 16]}
          containerPadding={[24, 24]}
          isDraggable
          isResizable
          compactType={null}
          preventCollision
          draggableHandle=".widget-drag-handle"
          onLayoutChange={handleLayoutChange}
        >
          {widgets.map((w) => (
            <div key={w.id} style={{ overflow: "hidden" }}>
              <div
                style={{
                  height: "100%",
                  background: "rgba(20, 20, 35, 0.65)",
                  backdropFilter: "blur(12px)",
                  WebkitBackdropFilter: "blur(12px)",
                  borderRadius: 12,
                  border: "1px solid rgba(255,255,255,0.1)",
                  display: "flex",
                  flexDirection: "column",
                  position: "relative",
                  overflow: "hidden",
                }}
              >
                {/* Drag handle + close button */}
                <div
                  className="widget-drag-handle"
                  style={{
                    display: "flex",
                    justifyContent: "flex-end",
                    padding: "4px 6px 0",
                    cursor: "grab",
                    minHeight: 20,
                  }}
                >
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      removeWidget(w.id);
                    }}
                    aria-label={`Remove ${w.type} widget`}
                    style={{
                      background: "rgba(255,255,255,0.1)",
                      border: "none",
                      borderRadius: 4,
                      width: 18,
                      height: 18,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      cursor: "pointer",
                      color: "rgba(255,255,255,0.4)",
                      transition: "color 0.15s, background 0.15s",
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.color = "#fff";
                      e.currentTarget.style.background = "rgba(239,68,68,0.6)";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.color = "rgba(255,255,255,0.4)";
                      e.currentTarget.style.background = "rgba(255,255,255,0.1)";
                    }}
                  >
                    <X size={12} />
                  </button>
                </div>
                {/* Widget content */}
                <div style={{ flex: 1, padding: "0 8px 8px", overflow: "hidden" }}>
                  {renderWidget(w.type)}
                </div>
              </div>
            </div>
          ))}
        </GridLayout>
      </div>

      {/* Add widget button */}
      <div
        style={{
          position: "absolute",
          bottom: 16,
          right: 16,
          pointerEvents: "auto",
          zIndex: 10,
        }}
      >
        <button
          onClick={() => setPickerOpen(!pickerOpen)}
          aria-label="Add widget"
          style={{
            width: 40,
            height: 40,
            borderRadius: "50%",
            background: "rgba(20, 20, 35, 0.7)",
            backdropFilter: "blur(12px)",
            WebkitBackdropFilter: "blur(12px)",
            border: "1px solid rgba(255,255,255,0.15)",
            color: "rgba(255,255,255,0.7)",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            transition: "transform 0.15s, background 0.15s",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "rgba(40, 40, 60, 0.85)";
            e.currentTarget.style.transform = "scale(1.1)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "rgba(20, 20, 35, 0.7)";
            e.currentTarget.style.transform = "scale(1)";
          }}
        >
          <Plus size={20} />
        </button>

        {/* Widget picker popover */}
        {pickerOpen && (
          <div
            ref={pickerRef}
            style={{
              position: "absolute",
              bottom: 50,
              right: 0,
              background: "rgba(20, 20, 35, 0.9)",
              backdropFilter: "blur(16px)",
              WebkitBackdropFilter: "blur(16px)",
              border: "1px solid rgba(255,255,255,0.15)",
              borderRadius: 10,
              padding: 6,
              minWidth: 170,
              display: "flex",
              flexDirection: "column",
              gap: 2,
            }}
          >
            {WIDGET_TYPES.map((wt) => (
              <button
                key={wt.type}
                onClick={() => {
                  addWidget(wt.type);
                  setPickerOpen(false);
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "8px 10px",
                  background: "transparent",
                  border: "none",
                  borderRadius: 6,
                  color: "rgba(255,255,255,0.8)",
                  fontSize: "0.8rem",
                  cursor: "pointer",
                  textAlign: "left",
                  transition: "background 0.12s",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "rgba(255,255,255,0.1)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "transparent";
                }}
              >
                {wt.icon}
                {wt.label}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
