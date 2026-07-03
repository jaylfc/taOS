import { useEffect, useState } from "react";

/** Loads an HTMLImageElement for a given src so it can be handed to Konva's <Image>. */
export function useHtmlImage(src: string | undefined): HTMLImageElement | undefined {
  const [img, setImg] = useState<HTMLImageElement | undefined>(undefined);

  useEffect(() => {
    if (!src) {
      setImg(undefined);
      return;
    }
    let cancelled = false;
    const el = new window.Image();
    el.crossOrigin = "anonymous";
    el.onload = () => {
      if (!cancelled) setImg(el);
    };
    el.onerror = () => {
      // Clear any previously loaded image so a failed src doesn't leave a
      // stale bitmap on the node; the Konva <Image> renders nothing until a
      // valid src loads.
      if (!cancelled) setImg(undefined);
    };
    el.src = src;
    return () => {
      cancelled = true;
    };
  }, [src]);

  return img;
}
