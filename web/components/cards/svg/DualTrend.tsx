"use client";
// dual_trend: 두 계열 선, 마우스를 올리면 그날 값 상자, 오늘 이후는 빗금, 아래 구간 막대를 끌어 보는 범위(view.window)를 정한다(옛 paintDual).
// 끌기는 포인터 이벤트(포인터 캡처), 놓으면 dispatch(card)로 view.window를 바꾼다. 한 번 누르면(시작 = 끝) 구간을 푼다.
import { useRef, useState } from "react";
import { clamp, showValue } from "@/lib/calc";
import { useDash } from "@/lib/store";
import type { CardView } from "@/lib/types";
import { pickColor } from "@/lib/series";
import { MIN_POINTS, cardDays, cardFormat, sliceWindow } from "./__pure__";
import { useZoomX } from "../useZoomX";
import { Frame, Say, type P } from "./common";

export function DualTrend({ card, colors, range, merge }: P) {
  const { dispatch } = useDash();
  const [tipAt, setTip] = useState<number | null>(null);
  const [drag, setDrag] = useState<[number, number] | null>(null);
  const grab = useRef<{ start: number; rect: DOMRect } | null>(null);
  const noWin = { ...card, view: { ...card.view, window: undefined } };
  const a = cardDays(noWin, 0, range, merge), whole = a.map(d => d.day), win = card.view?.window, W = 320, H = 190, pad = 14, top = 10, plot = 118, brushY = 150;
  const picked = cardDays(card, 0, range, merge), keep = !!picked.length && whole.includes(picked[0].day) && whole.includes(picked[picked.length - 1].day);  // 구간의 처음·끝이 지금 기간 안일 때만
  const view = keep ? card : noWin;  // 저장한 구간이 지금 기간 밖이면 구간 없이 그린다
  const fullRows = [cardDays(view, 0, range, merge), cardDays(view, 1, range, merge)], fullShown = fullRows[0].map(d => d.day);
  const z = useZoomX(fullShown.length > MIN_POINTS ? { kind: "index", count: fullShown.length } : null);
  const rows = fullRows.map(r => sliceWindow(r, z.view)), shown = sliceWindow(fullShown, z.view), from = shown[0], to = shown[shown.length - 1];
  if (!whole.length) return <Say text="이 기간의 값이 없어요" />;
  const all = rows.flat().map(d => d.v).filter((v): v is number => v != null), lo = Math.min(...all), hi = Math.max(...all);
  const xOf = (i: number) => pad + (W - 2 * pad) * (shown.length < 2 ? 0.5 : i / (shown.length - 1)), yOf = (v: number) => top + plot - plot * (hi === lo ? 0.5 : (v - lo) / (hi - lo));
  const names = [0, 1].map(i => card.data?.by_unit?.day?.series[i]?.name ?? `계열 ${i + 1}`), cols = names.map((n, i) => pickColor(card, "series", n, colors, i)), today = new Date().toLocaleDateString("sv");
  const future = shown.findIndex(d => d > today), fmt = cardFormat(card), hatch = "hatch" + card.id;
  const bx = (i: number) => pad + (W - 2 * pad) * (whole.length < 2 ? 0.5 : i / (whole.length - 1));
  const i0 = drag ? Math.min(...drag) : whole.indexOf(from), i1 = drag ? Math.max(...drag) : whole.indexOf(to);
  const at = (x: number, rect: DOMRect) => clamp(Math.round((x - rect.left) / rect.width * (whole.length - 1)), 0, whole.length - 1);
  const down = (e: React.PointerEvent<SVGRectElement>) => {  // 아래 구간 막대를 끌어 보는 범위를 고른다
    const rect = e.currentTarget.getBoundingClientRect(), start = at(e.clientX, rect);
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    grab.current = { start, rect };
    setDrag([start, start]);
  };
  const move = (e: React.PointerEvent<SVGRectElement>) => { e.stopPropagation(); const g = grab.current; if (g) setDrag([g.start, at(e.clientX, g.rect)]); };
  const up = (e: React.PointerEvent<SVGRectElement>) => {
    e.stopPropagation();
    const g = grab.current;
    if (!g) return;
    const k = at(e.clientX, g.rect), next: CardView = { ...card.view };
    if (g.start === k) delete next.window; else next.window = { from: whole[Math.min(g.start, k)], to: whole[Math.max(g.start, k)] };
    grab.current = null;
    setDrag(null);
    dispatch({ type: "card", id: card.id, patch: { view: next } });
  };
  const tip = (e: React.PointerEvent<SVGRectElement>) => { const p = e.currentTarget.getBoundingClientRect(); setTip(clamp(Math.round((e.clientX - p.left) / p.width * (shown.length - 1)), 0, shown.length - 1)); };
  return (
    <div {...z.bind}>
    <Frame w={W} h={H} label={card.title}>
      <defs>
        <pattern id={hatch} width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1={0} y1={0} x2={0} y2={6} stroke={colors.text} strokeWidth={1.5} /></pattern>
      </defs>
      {future >= 0 && <rect x={xOf(Math.max(0, future - 1))} y={top} width={W - pad - xOf(Math.max(0, future - 1))} height={plot} fill={`url(#${hatch})`} opacity={0.35} />}
      {rows.map((r, k) => <polyline key={k} points={r.map((d, i) => d.v == null ? null : `${xOf(i).toFixed(1)},${yOf(d.v).toFixed(1)}`).filter(Boolean).join(" ")} fill="none" stroke={cols[k]} strokeWidth={2} />)}
      <rect x={pad} y={top} width={W - 2 * pad} height={plot} fill="transparent" onPointerMove={tip} onPointerLeave={() => setTip(null)} />
      {tipAt != null && (
        <g transform={`translate(${clamp(xOf(tipAt) + 8, 0, W - 152)} ${top})`} pointerEvents="none">
          <rect width={150} height={52} rx={6} fill={colors.card} stroke={colors.line} />
          <text x={8} y={15} fontSize={11} fill={colors.text}>{shown[tipAt]}</text>
          <text x={8} y={30} fontSize={11} fill={cols[0]}>{`${names[0]} ${showValue(rows[0][tipAt]?.v, fmt)}`}</text>
          <text x={8} y={45} fontSize={11} fill={cols[1]}>{`${names[1]} ${showValue(rows[1][tipAt]?.v, fmt)}`}</text>
        </g>
      )}
      {a.map((d, i) => { const h = d.v == null ? 0 : 28 * (d.v - lo) / ((hi - lo) || 1); return <rect key={d.day} x={bx(i) - 1} y={brushY + 28 - h} width={2} height={d.v == null ? 0 : h + 1} fill={colors.text} />; })}
      <rect x={bx(i0)} y={brushY - 2} width={Math.max(4, bx(i1) - bx(i0))} height={34} fill={colors.series[0]} opacity={0.25} stroke={colors.series[0]} />
      <rect x={pad} y={brushY - 2} width={W - 2 * pad} height={34} fill="transparent" style={{ cursor: "col-resize", touchAction: "none" }} onPointerDown={down} onPointerMove={move} onPointerUp={up} />
      <text x={pad} y={H - 2} fontSize={10} fill={colors.text}>{names.join(" · ") + (win && keep ? "  (구간을 골랐어요. 한 번 누르면 풀려요)" : "")}</text>
    </Frame>
    </div>
  );
}
