"use client";
// 3차원 그래프(막대·곡면·산점도)를 SVG에 직접 그린다(계산은 three.ts). 한 번 눌러 켠 뒤(active, 가로축 확대와 같은 켜기) 끌면 돌고, 휠로 크게·작게, 두 번 누르면 처음 각도.
// 켜져 있을 때만 휠을 막아(preventDefault) 페이지·부모 창이 같이 스크롤되지 않는다. 그림은 SVG라 카드 그림 저장·보고서에 지금 각도 그대로 찍힌다.
import { useEffect, useMemo, useRef, useState, type MouseEvent, type PointerEvent, type ReactNode } from "react";
import { DRAG_PX } from "../svg/__pure__";
import { LABEL_FONT, START_VIEW, axisShapes, orbit, paint, zoomed, type Scene, type View3 } from "./three";

export const HELP_3D = "그림을 한 번 누른 뒤 끌면 돌아가요. 휠로 크게·작게, 두 번 누르면 처음 각도로 돌아가요.";
const pts = (xy: [number, number][]) => xy.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");

export function Chart3D({ scene, legend, active }: { scene: Scene; legend?: ReactNode; active?: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<[number, number]>([0, 0]);
  const [view, setView] = useState<View3>(START_VIEW);
  const on = useRef(active);
  const drag = useRef<{ x: number; y: number; from: View3 } | null>(null);
  const moved = useRef(false);
  useEffect(() => { on.current = active; });
  useEffect(() => {
    const el = box.current!;
    const ro = new ResizeObserver(([e]) => setSize([Math.round(e.contentRect.width), Math.round(e.contentRect.height)]));
    const wheel = (e: WheelEvent) => {
      if (!on.current) return;
      e.preventDefault();
      setView(v => zoomed(v, -Math.sign(e.deltaY)));
    };
    ro.observe(el);
    el.addEventListener("wheel", wheel, { passive: false });
    return () => { ro.disconnect(); el.removeEventListener("wheel", wheel); };
  }, []);
  const [w, h] = size;
  const drawn = useMemo(() => (w && h ? paint([...axisShapes(scene.axes, view), ...scene.shapes], view, w, h) : []), [scene, view, w, h]);
  const down = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || !active) return;
    drag.current = { x: e.clientX, y: e.clientY, from: view };
    moved.current = false;
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    if (!moved.current && Math.abs(dx) + Math.abs(dy) <= DRAG_PX) return;  // 덜 움직이면 누르기 그대로
    moved.current = true;
    setView(orbit(d.from, dx, dy));
  };
  const up = () => { drag.current = null; };
  // 끌고 난 뒤의 click은 카드로 올리지 않는다(카드 고르기가 바뀌지 않게, 가로축 확대의 끌기와 같다)
  const afterDrag = (e: MouseEvent<HTMLDivElement>) => {
    if (!moved.current) return;
    moved.current = false;
    e.stopPropagation();
    e.preventDefault();
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", fontSize: 12 }}>
      {legend}
      <div ref={box} title={HELP_3D} style={{ flex: 1, minHeight: 0, overflow: "hidden", touchAction: active ? "none" : undefined, cursor: active ? "grab" : undefined }}
        onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onClickCapture={afterDrag} onDoubleClick={() => setView(START_VIEW)}>
        <svg width={w} height={h} role="img" aria-label="3차원 그래프" style={{ display: "block" }}>
          {drawn.map(({ shape: s, xy }, i) => {
            if (s.kind === "poly") return <polygon key={i} points={pts(xy)} fill={s.fill} stroke={s.stroke} strokeWidth={0.5} strokeLinejoin="round">{s.title ? <title>{s.title}</title> : null}</polygon>;
            if (s.kind === "line") return <polyline key={i} points={pts(xy)} fill="none" stroke={s.stroke} strokeWidth={s.width} strokeLinecap="round" strokeLinejoin="round" />;
            if (s.kind === "dot") return <circle key={i} cx={xy[0][0]} cy={xy[0][1]} r={s.r} fill={s.fill} fillOpacity={0.8}>{s.title ? <title>{s.title}</title> : null}</circle>;
            return <text key={i} x={xy[0][0] + (s.dx ?? 0)} y={xy[0][1] + (s.dy ?? 0)} textAnchor={s.align} fontSize={LABEL_FONT} fontWeight={s.bold ? 600 : undefined} fill="var(--muted)" pointerEvents="none">{s.text}</text>;
          })}
        </svg>
      </div>
    </div>
  );
}
