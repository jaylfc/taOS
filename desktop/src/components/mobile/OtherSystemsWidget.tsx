import { useState, useEffect, useRef } from "react";
import { Smartphone, Loader2, AlertCircle, CheckCircle, XCircle, ChevronLeft } from "lucide-react";

interface OtherSystem {
  id: string;
  name: string;
  ready: boolean;
  reason: string;
}

interface OtherSystemsResponse {
  available: boolean;
  systems: OtherSystem[];
}

const CARD_STYLE: React.CSSProperties = {
  borderRadius: "16px",
  background: "rgba(255,255,255,0.06)",
  border: "1px solid rgba(255,255,255,0.08)",
  backdropFilter: "blur(10px)",
  WebkitBackdropFilter: "blur(10px)",
  padding: "12px",
};

const SHEET_BUTTON: React.CSSProperties = {
  ...CARD_STYLE,
  minHeight: 44,
  width: "100%",
  color: "rgba(255,255,255,0.95)",
  fontSize: 15,
  cursor: "pointer",
};

const SHEET_BUTTON_DANGER: React.CSSProperties = {
  ...SHEET_BUTTON,
  background: "rgba(220, 50, 50, 0.2)",
  border: "1px solid rgba(220, 50, 50, 0.4)",
  color: "rgba(255, 150, 150, 0.95)",
};

function SystemRow({ system, onClick, disabled }: { system: OtherSystem; onClick: () => void; disabled: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        ...CARD_STYLE,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "12px",
        width: "100%",
        padding: "12px",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        transition: "background 150ms ease, opacity 150ms ease",
      }}
      aria-disabled={disabled}
      aria-label={disabled ? `${system.name} — not ready: ${system.reason}` : `Switch to ${system.name}`}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "12px", flex: 1, minWidth: 0 }}>
        <Smartphone size={24} style={{ color: "rgba(255,255,255,0.8)" }} aria-hidden="true" />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 500, fontSize: 15, color: "rgba(255,255,255,0.95)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {system.name}
          </div>
          {system.reason && (
            <div style={{ fontSize: 12, color: "rgba(255,255,255,0.5)", marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {system.reason}
            </div>
          )}
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {system.ready ? (
          <CheckCircle size={16} style={{ color: "rgba(80,200,120,0.9)" }} aria-hidden="true" />
        ) : (
          <AlertCircle size={16} style={{ color: "rgba(220,150,50,0.9)" }} aria-hidden="true" />
        )}
      </div>
    </button>
  );
}

export function OtherSystemsWidget() {
  const [data, setData] = useState<OtherSystemsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [showSheet, setShowSheet] = useState(false);
  const [selectedSystem, setSelectedSystem] = useState<OtherSystem | null>(null);
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const [drainFailed, setDrainFailed] = useState(false);
  const sheetRef = useRef<HTMLDivElement>(null);
  const previousActiveElement = useRef<HTMLElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/system/other-systems", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (showSheet) {
      previousActiveElement.current = document.activeElement as HTMLElement;
      document.body.style.overflow = "hidden";
      setTimeout(() => sheetRef.current?.focus(), 0);
      
      const handleGlobalKeyDown = (e: KeyboardEvent) => {
        if (e.key === "Escape") {
          e.preventDefault();
          if (!switching) {
            setShowSheet(false);
            setSelectedSystem(null);
            setSwitchError(null);
          }
        }
        if ((e.key === "Tab" || e.key === "Shift") && sheetRef.current) {
          const focusableElements = sheetRef.current.querySelectorAll<HTMLButtonElement>(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
          );
          const focusableArray = Array.from(focusableElements).filter((el) => !el.disabled && el.style.display !== "none" && el.style.visibility !== "hidden");
          if (focusableArray.length === 0) return;
          
          const firstElement = focusableArray[0];
          const lastElement = focusableArray[focusableArray.length - 1];
          
          if (e.key === "Tab" && !e.shiftKey && document.activeElement === lastElement) {
            e.preventDefault();
            firstElement?.focus();
          } else if (e.key === "Tab" && e.shiftKey && document.activeElement === firstElement) {
            e.preventDefault();
            lastElement?.focus();
          }
        }
      };
      document.addEventListener("keydown", handleGlobalKeyDown);
      return () => {
        document.removeEventListener("keydown", handleGlobalKeyDown);
        document.body.style.overflow = "";
        previousActiveElement.current?.focus();
      };
    } else {
      document.body.style.overflow = "";
      previousActiveElement.current?.focus();
    }
  }, [showSheet, switching]);

  const openConfirmSheet = (system: OtherSystem) => {
    setSelectedSystem(system);
    setShowSheet(true);
    setSwitchError(null);
  };

  const closeSheet = () => {
    setShowSheet(false);
    setSelectedSystem(null);
    setSwitchError(null);
  };

  const handleSwitch = async (force = false) => {
    if (!selectedSystem) return;
    setSwitching(true);
    setSwitchError(null);
    setDrainFailed(false);
    try {
      const res = await fetch(`/api/system/other-systems/${selectedSystem.id}/switch`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true, force }),
      });
      if (res.ok) {
        setSwitching(true);
      } else if (res.status === 409) {
        const err = await res.json().catch(() => ({ detail: "Switch failed" }));
        if (err.detail && err.detail.includes("agents not ready")) {
          setDrainFailed(true);
          setSwitchError(err.detail);
        } else {
          setSwitchError(err.detail || "Switch failed");
        }
        setSwitching(false);
      } else {
        const err = await res.json().catch(() => ({ detail: "Switch failed" }));
        setSwitchError(err.detail || "Switch failed");
        setSwitching(false);
      }
    } catch {
      setSwitchError("Network error");
      setSwitching(false);
    }
  };

  if (!data?.available || loading) {
    return null;
  }

  const readySystems = data.systems.filter((s) => s.ready);
  const notReadySystems = data.systems.filter((s) => !s.ready);

  const openListSheet = () => {
    setSelectedSystem(null);
    setShowSheet(true);
    setSwitchError(null);
  };

  return (
    <>
      <WidgetCard onClick={openListSheet} disabled={data.systems.length === 0}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <Smartphone size={24} style={{ color: "rgba(255,255,255,0.8)" }} aria-hidden="true" />
          <div>
            <div style={{ fontWeight: 500, fontSize: 15, color: "rgba(255,255,255,0.95)" }}>Other Systems</div>
            <div style={{ fontSize: 12, color: "rgba(255,255,255,0.5)", marginTop: 2 }}>
              {readySystems.length} ready{notReadySystems.length > 0 ? `, ${notReadySystems.length} not ready` : ""}
            </div>
          </div>
        </div>
      </WidgetCard>

      {showSheet && (
        <div
          onClick={closeSheet}
          style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(0,0,0,0.4)" }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="other-systems-title"
        >
          <div
            ref={sheetRef}
            tabIndex={-1}
            onClick={(e) => e.stopPropagation()}
            style={{
              ...CARD_STYLE,
              position: "fixed",
              left: 12,
              right: 12,
              bottom: 12,
              display: "flex",
              flexDirection: "column",
              gap: 12,
              background: "rgba(30,30,40,0.95)",
              maxHeight: "80vh",
              overflowY: "auto",
            }}
            role="document"
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", paddingBottom: 8, borderBottom: "1px solid rgba(255,255,255,0.1)" }}>
              <h2 id="other-systems-title" style={{ fontSize: 17, fontWeight: 600, color: "rgba(255,255,255,0.95)" }}>
                {selectedSystem ? `Switch to ${selectedSystem.name}?` : "Other Systems"}
              </h2>
              <button
                type="button"
                onClick={closeSheet}
                style={{ background: "none", border: "none", color: "rgba(255,255,255,0.7)", cursor: "pointer", padding: 4 }}
                aria-label="Close"
              >
                <ChevronLeft size={24} />
              </button>
            </div>

            {selectedSystem && !switching && !switchError && (
              <>
                <div style={{ fontSize: 14, color: "rgba(255,255,255,0.8)", lineHeight: 1.5 }}>
                  Your phone restarts into {selectedSystem.name}. Save your work first: open apps will close.
                  To come back, choose Return to taOS in {selectedSystem.name}, or restart the phone.
                </div>
                <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
                  <button
                    type="button"
                    onClick={() => handleSwitch(false)}
                    disabled={switching}
                    style={{ ...SHEET_BUTTON_DANGER, flex: 1 }}
                  >
                    {switching ? <Loader2 size={16} className="animate-spin" /> : "Switch"}
                  </button>
                  <button type="button" onClick={closeSheet} disabled={switching} style={SHEET_BUTTON}>
                    Cancel
                  </button>
                </div>
              </>
            )}

            {selectedSystem && switching && (
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12, padding: "20px 0" }}>
                <Loader2 size={32} className="animate-spin" style={{ color: "rgba(255,255,255,0.7)" }} />
                <div style={{ fontSize: 16, fontWeight: 500, color: "rgba(255,255,255,0.95)", textAlign: "center" }}>
                  Switching to {selectedSystem.name}...
                </div>
                <div style={{ fontSize: 13, color: "rgba(255,255,255,0.5)", textAlign: "center" }}>
                  To come back, open Return to taOS there.
                </div>
              </div>
            )}

            {selectedSystem && switchError && (
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12, padding: "20px 0" }}>
                <XCircle size={32} style={{ color: "rgba(220,80,80,0.9)" }} />
                <div style={{ fontSize: 15, fontWeight: 500, color: "rgba(255,255,255,0.95)", textAlign: "center" }}>
                  Switch failed
                </div>
                <div style={{ fontSize: 13, color: "rgba(255,255,255,0.7)", textAlign: "center" }}>
                  {switchError}
                </div>
                {drainFailed ? (
                  <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
                    <button
                      type="button"
                      onClick={() => handleSwitch(true)}
                      disabled={switching}
                      style={{ ...SHEET_BUTTON_DANGER, flex: 1 }}
                    >
                      {switching ? <Loader2 size={16} className="animate-spin" /> : "Switch anyway"}
                    </button>
                    <button type="button" onClick={closeSheet} disabled={switching} style={SHEET_BUTTON}>
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button type="button" onClick={closeSheet} style={SHEET_BUTTON}>
                    OK
                  </button>
                )}
              </div>
            )}

            {!selectedSystem && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {data.systems.map((system) => (
                  <SystemRow
                    key={system.id}
                    system={system}
                    onClick={() => openConfirmSheet(system)}
                    disabled={!system.ready}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

function WidgetCard({ children, onClick, disabled }: { children: React.ReactNode; onClick?: () => void; disabled?: boolean }) {
  return (
    <div
      onClick={onClick}
      style={{
        ...CARD_STYLE,
        cursor: onClick && !disabled ? "pointer" : undefined,
        opacity: disabled ? 0.5 : 1,
        transition: "background 150ms ease, opacity 150ms ease",
      }}
      aria-disabled={disabled}
      tabIndex={onClick && !disabled ? 0 : undefined}
      onKeyDown={(e) => { if ((e.key === "Enter" || e.key === " ") && onClick && !disabled) { e.preventDefault(); onClick(); } }}
      role={onClick && !disabled ? "button" : undefined}
      aria-label={disabled ? "Other systems — not available" : "Other systems"}
    >
      {children}
    </div>
  );
}