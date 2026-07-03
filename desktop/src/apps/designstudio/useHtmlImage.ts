import { useEffect, useState } from "react";

export interface HtmlImageResult {
  image: HTMLImageElement | undefined;
  /** True once `src` has failed to load, so the caller can render a placeholder. */
  failed: boolean;
}

/** Loads an HTMLImageElement for a given src so it can be handed to Konva's <Image>. */
export function useHtmlImage(src: string | undefined): HtmlImageResult {
  const [img, setImg] = useState<HTMLImageElement | undefined>(undefined);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
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
      // stale bitmap on the node, and surface the failure so the caller can
      // render a broken-image placeholder instead of nothing.
      if (!cancelled) {
        setImg(undefined);
        setFailed(true);
      }
    };
    el.src = src;
    return () => {
      cancelled = true;
    };
  }, [src]);

  return { image: img, failed };
}
