interface Props {
  params?: Record<string, unknown>;
}

export function GlowEffect({ params: _params }: Props) {
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        pointerEvents: "none",
        boxShadow:
          "inset 0 0 120px 20px var(--color-accent-glow, rgba(139,146,163,0.25))",
        mixBlendMode: "screen",
      }}
    />
  );
}
