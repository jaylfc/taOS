import { ChevronRight } from "lucide-react";
import { type ReactNode, type CSSProperties } from "react";

/**
 * iOS 26-style grouped list primitive.
 *
 * ``<MobileListSection>`` wraps a group of rows with the characteristic
 * rounded rectangle and inset grouped styling. ``<MobileListRow>`` is a
 * tappable row with consistent padding, chevron, and accent-coloured
 * active state. Used across apps to keep list UI consistent with the
 * rest of the mobile shell.
 */

export function MobileListSection({
  header,
  footer,
  children,
  style,
}: {
  header?: string;
  footer?: string;
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <div style={{ marginBottom: 20, ...style }}>
      {header && (
        <div
          style={{
            fontSize: 12,
            textTransform: "uppercase",
            letterSpacing: 0.5,
            color: "rgba(255,255,255,0.45)",
            padding: "0 20px 6px",
            fontWeight: 600,
          }}
        >
          {header}
        </div>
      )}
      <div
        style={{
          margin: "0 12px",
          borderRadius: 16,
          background: "rgba(255,255,255,0.05)",
          border: "1px solid rgba(255,255,255,0.08)",
          backdropFilter: "blur(20px)",
          WebkitBackdropFilter: "blur(20px)",
          overflow: "hidden",
        }}
      >
        {children}
      </div>
      {footer && (
        <div
          style={{
            fontSize: 12,
            color: "rgba(255,255,255,0.4)",
            padding: "6px 20px 0",
          }}
        >
          {footer}
        </div>
      )}
    </div>
  );
}

interface RowProps {
  leading?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  trailing?: ReactNode;
  onClick?: () => void;
  active?: boolean;
  showChevron?: boolean;
  danger?: boolean;
  isLast?: boolean;
  "aria-label"?: string;
}

export function MobileListRow({
  leading,
  title,
  subtitle,
  trailing,
  onClick,
  active,
  showChevron,
  danger,
  isLast,
  "aria-label": ariaLabel,
}: RowProps) {
  const interactive = !!onClick;
  const Tag = interactive ? "button" : "div";

  return (
    <Tag
      onClick={onClick}
      aria-label={ariaLabel}
      aria-pressed={active}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        width: "100%",
        padding: "12px 16px",
        background: active ? "rgba(100,180,255,0.12)" : "none",
        border: "none",
        borderBottom: isLast ? "none" : "1px solid rgba(255,255,255,0.06)",
        cursor: interactive ? "pointer" : "default",
        color: danger ? "rgb(255,100,100)" : "rgba(255,255,255,0.9)",
        textAlign: "left",
        fontSize: 15,
        transition: "background 120ms ease",
      }}
      onMouseDown={
        interactive
          ? (e) => {
              (e.currentTarget as HTMLElement).style.background =
                "rgba(255,255,255,0.08)";
            }
          : undefined
      }
      onMouseUp={
        interactive
          ? (e) => {
              (e.currentTarget as HTMLElement).style.background = active
                ? "rgba(100,180,255,0.12)"
                : "none";
            }
          : undefined
      }
      onMouseLeave={
        interactive
          ? (e) => {
              (e.currentTarget as HTMLElement).style.background = active
                ? "rgba(100,180,255,0.12)"
                : "none";
            }
          : undefined
      }
    >
      {leading && <div style={{ flexShrink: 0 }}>{leading}</div>}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontWeight: 500,
            fontSize: 15,
            color: danger ? "rgb(255,100,100)" : "rgba(255,255,255,0.95)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {title}
        </div>
        {subtitle && (
          <div
            style={{
              fontSize: 13,
              color: "rgba(255,255,255,0.5)",
              marginTop: 2,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {subtitle}
          </div>
        )}
      </div>
      {trailing && (
        <div
          style={{
            flexShrink: 0,
            fontSize: 13,
            color: "rgba(255,255,255,0.5)",
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          {trailing}
        </div>
      )}
      {showChevron && (
        <ChevronRight
          size={18}
          style={{ color: "rgba(255,255,255,0.3)", flexShrink: 0 }}
        />
      )}
    </Tag>
  );
}
