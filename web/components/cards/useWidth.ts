import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

// [SPECIFICATION] plans/phase-9.md#useWidth
export function useWidth(): { ref: RefObject<HTMLDivElement | null>; width: number } {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (el === null) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return { ref, width };
}
