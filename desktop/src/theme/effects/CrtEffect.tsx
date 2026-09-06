interface Props {
  params?: Record<string, unknown>;
}

export function CrtEffect({ params: _params }: Props) {
  return (
    <>
      <style>{`
        @keyframes crt-flicker {
          0%   { opacity: 0.92; }
          50%  { opacity: 1; }
          100% { opacity: 0.92; }
        }
      `}</style>
      <div
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          background:
            "radial-gradient(ellipse at center, transparent 60%, rgba(0,0,0,0.35) 100%)",
          animation: "crt-flicker 0.15s infinite",
        }}
      />
    </>
  );
}
