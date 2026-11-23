"use client";
// target_trend: 일 값 선 + 목표 가로 점선 + 목표 아래 구간 채움 + "목표보다 1.7%p 낮아요. 목표 아래로 N번 내려갔어요"(옛 paintTargetTrend)
import { belowPolygons, crossDown, overTarget, showGap, showValue, underTarget, zeroWhenEmpty } from "@/lib/calc";
import { pickColor } from "@/lib/series";
import { useRootScale } from "../chart/scale";
import { MIN_POINTS, cardDays, cardFormat, cardTarget, cardValue, growFont, sliceWindow } from "./__pure__";
import { useZoomX } from "../useZoomX";
import { Empty, Frame, type P } from "./common";

const FONT = 11;  // 아래 한 줄 글의 기준 글자 크기(화면 크기 배율 1)
const LINE_W = 2, TARGET_W = 1.5, TARGET_DASH = "5 4", DOT_R = 3, DOT_MAX = 24;  // 예시 08-control: 선 굵기, 목표(관리 한계) 점선 굵기·모양, 점 반지름. 점을 그리는 최대 점 수(넘으면 선만)

export function TargetTrend({ card, colors, range, merge }: P) {
  const s = useRootScale(), dated = cardDays(card, 0, range, merge), vs = dated.map(d => d.v), target = cardTarget(card), fmt = cardFormat(card), W = 300, H = 160, pad = 12, color = pickColor(card, "series", card.title, colors, 0);
  const real = vs.filter((x): x is number => x != null);
  const z = useZoomX(vs.length > MIN_POINTS ? { kind: "index", count: vs.length } : null), vsV = sliceWindow(vs, z.view);
  if (!real.length) return <Empty />;
  const lo = Math.min(...vsV.filter((x): x is number => x != null), target ?? Infinity), hi = Math.max(...vsV.filter((x): x is number => x != null), target ?? -Infinity);
  const xs = vsV.map((_, i) => pad + (W - 2 * pad) * (vs.length < 2 ? 0.5 : i / (vs.length - 1)));
  const step = (W - 2 * pad) / Math.max(1, vs.length - 1);  // 값 풍선 자리의 너비(점 사이 간격)
  const daily = zeroWhenEmpty(card.data?.by_unit?.day?.series?.[0]?.agg ?? ""), total = cardValue(card, 0, range, merge);  // 합계·개수의 기간 값은 날들의 합이라 하루 목표와 견줄 수 없다: 선에 그린 날들의 평균과 견준다
  const now = total != null && daily ? real.reduce((a, b) => a + b, 0) / real.length : total;
  const says = target == null ? ((card.data?.target as { error?: string } | undefined)?.error || "목표를 아직 정하지 않았어요") : now == null ? "—"
    : `${daily ? "일 평균이 " : ""}목표보다 ${showGap(now - target, fmt, card.view)} ${underTarget(now, target) ? "낮아요" : overTarget(now, target) ? "높아요" : "같아요"}. 목표 아래로 ${crossDown(vs, target)}번 내려갔어요`;
  const k = growFont(says, W - 2 * pad, FONT, s) / FONT, foot = 24 * k, yOf = (v: number) => H - foot - (H - foot - pad) * (hi === lo ? 0.5 : (v - lo) / (hi - lo));  // 글 줄의 자리(선 아래)도 글자만큼 키운다
  return (
    <div {...z.bind} className="nm-well">
    <Frame w={W} h={H} label={card.title}>
      {target != null && belowPolygons(xs, vsV, target, yOf).map((p, k) => <polygon key={k} points={p.map(q => q.map(n => n.toFixed(1)).join(",")).join(" ")} fill={colors.series[1]} opacity={0.3} />)}
      {target != null && <line x1={pad} x2={W - pad} y1={yOf(target)} y2={yOf(target)} stroke={colors.ink} strokeWidth={TARGET_W} strokeDasharray={TARGET_DASH} />}
      <polyline points={vsV.map((v, i) => v == null ? null : `${xs[i].toFixed(1)},${yOf(v).toFixed(1)}`).filter(Boolean).join(" ")} fill="none" stroke={color} strokeWidth={LINE_W} strokeLinejoin="round" />
      {vsV.length <= DOT_MAX && vsV.map((v, i) => v == null ? null : <circle key={i} cx={xs[i]} cy={yOf(v)} r={DOT_R} fill={color} />)}
      {sliceWindow(dated, z.view).map((d, i) => d.v == null ? null : <rect key={d.day} x={xs[i] - step / 2} y={pad} width={step} height={H - foot - pad} fill="transparent"><title>{`${d.day}: ${showValue(d.v, fmt, "", card.view)}`}</title></rect>)}{/* 마우스를 올리면 그날 값 */}
      <text x={pad} y={H - 6 * k} fontSize={FONT * k} fill={colors.text}>{says}</text>
    </Frame>
    </div>
  );
}
