interface Props {
  params?: Record<string, unknown>;
}

export function ScanlinesEffect({ params: _params }: Props) {
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        pointerEvents: "none",
        backgroundImage:
          "repeating-linear-gradient(0deg, rgba(0,0,0,0.18) 0px, rgba(0,0,0,0.18) 1px, transparent 1px, transparent 3px)",
        mixBlendMode: "multiply",
        opacity: 0.5,
      }}
    />
  );
}
