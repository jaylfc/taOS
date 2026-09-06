import { useEffect, useState } from "react";

export interface VisualViewportState {
  height: number;
  keyboardInset: number;
}

function read(): VisualViewportState {
  if (typeof window === "undefined") return { height: 0, keyboardInset: 0 };
  const vv = window.visualViewport;
  if (!vv) return { height: window.innerHeight, keyboardInset: 0 };
  const inset = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
  return { height: vv.height, keyboardInset: inset };
}

export function useVisualViewport(): VisualViewportState {
  const [state, setState] = useState<VisualViewportState>(read);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const vv = window.visualViewport;
    if (!vv) return;
    const update = () => setState(read());
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);
    return () => {
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
    };
  }, []);

  return state;
}
