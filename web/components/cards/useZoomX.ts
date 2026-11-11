import { useEffect, useRef, useState } from "react";
import type { PointerEvent, MouseEvent } from "react";
import { clamp } from "@/lib/calc";
import { DRAG_PX, fullSpan, panRange, zoomRange } from "./svg/__pure__";
import type { Span, ZoomAxis } from "./svg/__pure__";

// 전체와 같은 범위는 null(확대 안 함)로 돌린다
const orNull = (r: Span, axis: ZoomAxis): Span | null => {
  const [f0, f1] = fullSpan(axis);
  return r[0] <= f0 && r[1] >= f1 ? null : r;
};

export function useZoomX(axis: ZoomAxis | null) {
  const [view, setView] = useState<Span | null>(null);
  const [active, setActive] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const drag = useRef<{ x: number; span: Span; width: number; moved: boolean } | null>(null);
  const moved = useRef(false);
  const key = axis ? JSON.stringify(axis) : "";
  const live = useRef({ axis, view, active });
  useEffect(() => { live.current = { axis, view, active }; });
  const [seenKey, setSeenKey] = useState(key);
  if (seenKey !== key) { setSeenKey(key); setView(null); }  // 축이 바뀌면 확대를 푼다(렌더 중 상태 맞추기)

  useEffect(() => {
    const down = (e: Event) => {
      if (!ref.current?.contains(e.target as Node)) setActive(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setActive(false);
    };
    document.addEventListener("pointerdown", down);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", down);
      document.removeEventListener("keydown", esc);
    };
  }, []);

  useEffect(() => {
    const box = ref.current?.closest(".bcard");
    box?.classList.toggle("zoomable", active);
    return () => box?.classList.remove("zoomable");
  }, [active]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const on = (e: WheelEvent) => {
      const { axis: a, view: v, active: act } = live.current;
      if (!a || !act) return;
      e.preventDefault();
      if (Math.abs(e.deltaY) < Math.abs(e.deltaX)) return;
      const rect = el.getBoundingClientRect();
      const anchor = clamp((e.clientX - rect.left) / rect.width, 0, 1);
      setView(orNull(zoomRange(v ?? fullSpan(a), a, anchor, -Math.sign(e.deltaY)), a));
    };
    el.addEventListener("wheel", on, { passive: false });
    return () => el.removeEventListener("wheel", on);
  }, []);

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || !axis || !active) return;
    drag.current = { x: e.clientX, span: view ?? fullSpan(axis), width: e.currentTarget.getBoundingClientRect().width, moved: false };
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || !axis) return;
    const dx = e.clientX - d.x;
    if (!d.moved && Math.abs(dx) <= DRAG_PX) return;
    if (!d.moved) {
      d.moved = true;
      moved.current = true;
      e.currentTarget.setPointerCapture(e.pointerId);
    }
    setView(orNull(panRange(d.span, axis, dx / d.width), axis));
  };
  const release = (e: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.moved) e.currentTarget.releasePointerCapture(e.pointerId);
    drag.current = null;
  };
  const onClickCapture = (e: MouseEvent<HTMLDivElement>) => {
    if (!moved.current) return;
    e.stopPropagation();
    e.preventDefault();
    moved.current = false;
  };

  return {
    view,
    bind: {
      ref,
      onClick: () => setActive(true),
      onClickCapture,
      onDoubleClick: () => {
        if (active) setView(null);
      },
      onPointerDown,
      onPointerMove,
      onPointerUp: release,
      onPointerCancel: release,
      style: {
        height: "100%",
        touchAction: axis && active ? ("none" as const) : undefined,
        cursor: !axis || !active ? undefined : view ? "grab" : "zoom-in",
      },
    },
  };
}