"use client";
// 3차원 그래프(막대·곡면·산점도)를 SVG에 직접 그린다(계산은 three.ts). 한 번 눌러 켠 뒤(active, 가로축 확대와 같은 켜기) 끌면 돌고, 휠로 크게·작게, 두 번 누르면 처음 각도.
// 켜져 있을 때만 휠을 막아(preventDefault) 페이지·부모 창이 같이 스크롤되지 않는다. 그림은 SVG라 카드 그림 저장·보고서에 지금 각도 그대로 찍힌다.
// 곡면(surface)이면 범례 아래에 조절 칸: 매끈함 단계(칸을 잘게 나눠 곡선으로), [모양 조절](격자점 손잡이를 위아래로 끌어 높이를 바꾼다)·퍼짐·[처음 모양으로].
// 조절한 높이는 이 컴포넌트 상태에만 있다(값·서버에는 보내지 않는다). 같은 격자 크기로 다시 그려지면 남고, 크기가 바뀌면 지운다.
import { useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent, type PointerEvent, type ReactNode } from "react";
import { DRAG_PX } from "../svg/__pure__";
import { rem, scaledBox, useRootScale } from "./scale";
import {
  LABEL_FONT, SMOOTH_STEPS, SPREAD_RANGE, SPREAD_START, SPREAD_STEP, START_VIEW, axisShapes, dragMode, liftGrid, orbit, paint, smoothReason, smoothStep, zPerPx, zoomed,
  type GridScene, type Key3, type Scene, type Shaping, type View3,
} from "./three";

export const HELP_3D = "그림을 한 번 누른 뒤 끌면 돌아가요. Ctrl을 누른 채 끌어도 돌아가요. 휠로 크게·작게, 두 번 누르면 처음 각도로 돌아가요.";
export const SHAPED_NOTE = "모양을 조절한 그림이에요(실제 값과 달라요)";
const NEED_ACTIVE = "그림을 한 번 눌러 켠 뒤에 쓸 수 있어요.";
const NOTE_AT = { x: 8, y: 14 };  // 조절 표시가 그림 왼쪽 위에서 떨어진 자리(px)
const DIM = 0.5;  // 꺼진 조절 칸·고를 수 없는 단계의 흐림
const CONTROL_FONT = 12;  // 조절 칸 글자 크기(기준 px, rem으로 바꿔 바탕 글자 크기를 따라간다)
const pts = (xy: [number, number][]) => xy.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
const BUTTON: CSSProperties = { font: "inherit", color: "var(--muted)", background: "none", border: "1px solid var(--line)", borderRadius: 4, padding: "0 6px", cursor: "pointer" };
const PICKED: CSSProperties = { color: "var(--text)", borderColor: "var(--text)" };
const FIELD: CSSProperties = { display: "inline-flex", alignItems: "center", gap: 4, whiteSpace: "nowrap" };

/** 곡면 조절에 필요한 것: 격자 크기(X 값 수 nx × 계열 수 ny), 모양(Shaping)대로 그림을 만드는 함수, 그 그림의 범례 */
export type Reshape = { nx: number; ny: number; scene: (shape: Shaping) => GridScene; legend: (keys: Key3[]) => ReactNode };
type Drag = { x: number; y: number; from: View3; grip: { i: number; j: number; base: number[][] | null; perPx: number } | null };

export function Chart3D({ scene, legend, active, surface }: { scene: Scene; legend?: ReactNode; active?: boolean; surface?: Reshape }) {
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<[number, number]>([0, 0]);
  const [view, setView] = useState<View3>(START_VIEW);
  const [want, setWant] = useState(0);  // 고른 매끈함 단계
  const [sculpt, setSculpt] = useState(false);
  const [spread, setSpread] = useState(SPREAD_START);
  const [lift, setLift] = useState<number[][] | null>(null);  // 손잡이로 더한 높이(없으면 받은 값 그대로)
  const dims = surface ? `${surface.nx}x${surface.ny}` : "";
  const [seen, setSeen] = useState(dims);
  if (seen !== dims) { setSeen(dims); setLift(null); }  // 격자 크기가 바뀌면 조절한 모양을 지운다(렌더 중 상태 맞추기)
  const on = useRef(active);
  const drag = useRef<Drag | null>(null);
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
  const [w, h] = size, s = useRootScale();  // w·h는 배율을 곱하기 전의 그림 크기(ResizeObserver는 transform 전 크기를 준다)
  const step = surface ? smoothStep(surface.nx, surface.ny, want) : 0, make = surface?.scene;
  const made = useMemo(() => make?.({ smooth: SMOOTH_STEPS[step], lift, grips: sculpt }), [make, step, lift, sculpt]);
  const shown = made ?? scene;
  const drawn = useMemo(() => (w && h ? paint([...axisShapes(shown.axes, view), ...shown.shapes], view, w, h) : []), [shown, view, w, h]);
  const down = (e: PointerEvent<HTMLDivElement>) => {
    const at = (e.target as Element).getAttribute("data-grip")?.split(",").map(Number), mode = dragMode(e.ctrlKey, !!active, !!at);
    if (e.button !== 0 || !mode) return;
    // 그림은 배율 s만큼 커져 보이므로 화면 1px = 그림 1/s px
    drag.current = { x: e.clientX, y: e.clientY, from: view, grip: made && at && mode === "lift" ? { i: at[0], j: at[1], base: lift, perPx: zPerPx(view, w, h) * made.span / s } : null };
    moved.current = false;
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    if (!moved.current && Math.abs(dx) + Math.abs(dy) <= DRAG_PX) return;  // 덜 움직이면 누르기 그대로
    moved.current = true;
    if (d.grip && surface) setLift(liftGrid(d.grip.base, surface.nx, surface.ny, d.grip.i, d.grip.j, -dy * d.grip.perPx, spread));  // 손잡이: 위로 끌면 높아진다
    else setView(orbit(d.from, dx, dy));
  };
  const up = () => { drag.current = null; };
  // 끌고 난 뒤의 click은 카드로 올리지 않는다(카드 고르기가 바뀌지 않게, 가로축 확대의 끌기와 같다)
  const afterDrag = (e: MouseEvent<HTMLDivElement>) => {
    if (!moved.current) return;
    moved.current = false;
    e.stopPropagation();
    e.preventDefault();
  };
  const live = (run: () => void) => () => { if (active) run(); };  // 조절 칸은 그림이 켜져 있을 때만 듣는다(꺼져 있으면 그 누르기가 그림을 켠다)
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", fontSize: 12 }}>
      {surface && made ? surface.legend(made.keys) : legend}
      {surface ? (
        <div role="group" aria-label="곡면 모양" title={active ? undefined : NEED_ACTIVE} style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "2px 12px", fontSize: rem(CONTROL_FONT), color: "var(--muted)", padding: "0 0 4px", opacity: active ? undefined : DIM }}>
          <span style={FIELD}>매끈함
            {SMOOTH_STEPS.map((times, n) => {
              const why = smoothReason(surface.nx, surface.ny, n);
              return <button key={n} type="button" aria-label={`매끈함 ${n}단계`} aria-pressed={n === step} disabled={!!why} title={why || (n ? `칸을 ${times}배로 잘게 나눠 곡선으로 이어 그려요(값은 그대로).` : "받은 값 그대로 그려요.")}
                onClick={live(() => setWant(n))} style={{ ...BUTTON, ...(n === step ? PICKED : {}), ...(why ? { opacity: DIM, cursor: "not-allowed" } : {}) }}>{n}</button>;
            })}
          </span>
          <label style={FIELD} title="켜면 격자점마다 손잡이가 보여요. 손잡이를 위아래로 끌면 그 점과 주변의 높이가 바뀌어요(그림만, 값은 그대로)."><input type="checkbox" aria-label="모양 조절" checked={sculpt} onChange={live(() => setSculpt(!sculpt))} />모양 조절</label>
          {sculpt ? <label style={FIELD} title="손잡이를 끌 때 주변 점이 따라오는 넓이(칸 수). 0이면 그 점만 움직여요.">퍼짐<input type="range" aria-label="퍼짐" min={SPREAD_RANGE[0]} max={SPREAD_RANGE[1]} step={SPREAD_STEP} value={spread} onChange={e => { if (active) setSpread(Number(e.target.value)); }} />{spread}</label> : null}
          <button type="button" aria-label="처음 모양으로" disabled={!lift} onClick={live(() => setLift(null))} style={{ ...BUTTON, ...(lift ? {} : { opacity: DIM, cursor: "default" }) }}>처음 모양으로</button>
        </div>
      ) : null}
      <div style={{ flex: 1, minHeight: 0 }}>
      <div ref={box} title={HELP_3D} style={{ ...scaledBox(s), overflow: "hidden", userSelect: "none", touchAction: active ? "none" : undefined, cursor: active ? "grab" : undefined }}
        onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onClickCapture={afterDrag} onDoubleClick={() => setView(START_VIEW)}>
        <svg width={w} height={h} role="img" aria-label="3차원 그래프" style={{ display: "block" }}>
          {drawn.map(({ shape, xy }, i) => {
            if (shape.kind === "poly") return <polygon key={i} points={pts(xy)} fill={shape.fill} stroke={shape.stroke} strokeWidth={0.5} strokeLinejoin="round">{shape.title ? <title>{shape.title}</title> : null}</polygon>;
            if (shape.kind === "line") return <polyline key={i} points={pts(xy)} fill="none" stroke={shape.stroke} strokeWidth={shape.width} strokeLinecap="round" strokeLinejoin="round" />;
            if (shape.kind === "dot") return <circle key={i} cx={xy[0][0]} cy={xy[0][1]} r={shape.r} fill={shape.fill} fillOpacity={0.8} data-grip={shape.grip?.join()} style={shape.grip && active ? { cursor: "ns-resize" } : undefined}>{shape.title ? <title>{shape.title}</title> : null}</circle>;
            return <text key={i} x={xy[0][0] + (shape.dx ?? 0)} y={xy[0][1] + (shape.dy ?? 0)} textAnchor={shape.align} fontSize={LABEL_FONT} fontWeight={shape.bold ? 600 : undefined} fill="var(--muted)" pointerEvents="none">{shape.text}</text>;
          })}
          {made && lift ? <text x={NOTE_AT.x} y={NOTE_AT.y} fontSize={LABEL_FONT} fontWeight={600} fill="var(--text)" pointerEvents="none">{SHAPED_NOTE}</text> : null}
        </svg>
      </div>
      </div>
    </div>
  );
}
