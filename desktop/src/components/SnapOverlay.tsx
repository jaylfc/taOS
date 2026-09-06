interface Props {
  bounds: { x: number; y: number; w: number; h: number } | null;
}

export function SnapOverlay({ bounds }: Props) {
  if (!bounds) return null;

  return (
    <div
      className="fixed pointer-events-none z-[9998] rounded-lg border-2 border-dashed transition-all duration-150"
      style={{
        left: bounds.x,
        top: bounds.y,
        width: bounds.w,
        height: bounds.h,
        backgroundColor: "var(--color-snap-preview)",
        borderColor: "var(--color-snap-border)",
      }}
    />
  );
}
