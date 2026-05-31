import { useEffect } from "react";

interface Props {
  params?: Record<string, unknown>;
}

export function CursorEffect({ params }: Props) {
  const cursor = typeof params?.cursor === "string" ? params.cursor : "crosshair";

  useEffect(() => {
    const prev = document.body.style.cursor;
    document.body.style.cursor = cursor;
    return () => {
      document.body.style.cursor = prev;
    };
  }, [cursor]);

  return <span style={{ display: "none" }} />;
}
