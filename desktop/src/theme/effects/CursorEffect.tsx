import { useEffect } from "react";

interface Props {
  params?: Record<string, unknown>;
}

const SAFE_CURSORS = new Set([
  "crosshair", "default", "none", "pointer", "text", "move", "grab",
  "zoom-in", "zoom-out", "wait", "help", "not-allowed",
]);

export function CursorEffect({ params }: Props) {
  const cursor = SAFE_CURSORS.has(params?.cursor as string) ? (params!.cursor as string) : "crosshair";

  useEffect(() => {
    const prev = document.body.style.cursor;
    document.body.style.cursor = cursor;
    return () => {
      document.body.style.cursor = prev;
    };
  }, [cursor]);

  return <span style={{ display: "none" }} />;
}
