"use client";
// kpi(B10b, 그림 1): 왼쪽에 큰 값(+작은 단위)·비교 한 줄(useCompare → kpiDelta)·비율이면 짝 절대량, 오른쪽 절반에 추이선.
// 추이선: 직전 기간 회색 선·옅은 바탕 + 이번 기간 계열 색 선·바탕(kpi_trend), 가로 점선 눈금, 끝 점, 목표(cardTarget)가 있으면 점선과 값 색(--ok, view.better).
import type { CSSProperties } from "react";
import { overTarget, showValue, underTarget } from "@/lib/calc";
import { compareItem } from "@/lib/series";
import { useCompare } from "@/components/shell/ShellProvider";
import { cardFormat, cardTarget, cardValue, sparkY, textUnits } from "./__pure__";
import { Box, Empty, type P } from "./common";
import { GRID_DASH, NOW_FILL, PREV_FILL, dayTrend, kpiColor, kpiDelta, sparkParts, trendSpans } from "./kpi_trend";

const SPARK_W = 2.5, END_DOT = 7, GRID_W = 1;  // 77-kpi: 추이선 굵기, 끝 점 지름(r 3.5), 점선 눈금 굵기
const BOX = { w: 120, h: 30 };  // 추이선 viewBox(늘어나는 그림)

type Pair = { num_label: string; num: number | null; den_label: string; den: number | null };

export function Kpi({ card, colors, range, merge, prev, mode = "prev" }: P) {
  const v = cardValue(card, 0, range, merge), fmt = cardFormat(card), pair = card.data?.pair as Pair | undefined;
  const view = card.view ?? {}, unit = view.unit_text ?? "", num = showValue(v, fmt, "", view), said = useCompare(card.id), t = cardTarget(card), color = kpiColor(card, colors);
  const trend = dayTrend(card, trendSpans(card, range, prev, mode), merge), all = [...trend.prev, ...trend.now], parts = sparkParts(trend, BOX.w, BOX.h);
  const met = v != null && t != null && (view.better === "down" ? !overTarget(v, t) : !underTarget(v, t));
  const goal = t == null ? undefined : `목표 ${showValue(t, fmt, unit, view)}`, ty = t != null && parts ? sparkY(all.map(p => p.v), t, BOX.h) : null;
  const label = compareItem(card, range, prev, merge, mode)?.label ?? "";
  if (v == null) return <Box cls="kpi"><Empty /></Box>;
  const spark = parts && (
    <svg viewBox={`0 0 ${BOX.w} ${BOX.h}`} className="kpi-spark" preserveAspectRatio="none" aria-hidden="true">
      {parts.grid.map(y => <line key={y} x1={0} x2={BOX.w} y1={y} y2={y} stroke="var(--line)" strokeWidth={GRID_W} strokeDasharray={GRID_DASH} vectorEffect="non-scaling-stroke" />)}
      {parts.prev && <><path d={parts.prevArea} fill="var(--muted)" fillOpacity={PREV_FILL} /><path d={parts.prev} fill="none" stroke="var(--muted)" strokeWidth={SPARK_W} strokeLinecap="round" vectorEffect="non-scaling-stroke" /></>}
      {parts.now && <><path d={parts.nowArea} fill={color} fillOpacity={NOW_FILL} /><path d={parts.now} fill="none" stroke={color} strokeWidth={SPARK_W} strokeLinecap="round" vectorEffect="non-scaling-stroke" /></>}
      <path d={`M${parts.end} h0`} stroke={parts.now ? color : "var(--muted)"} strokeWidth={END_DOT} strokeLinecap="round" vectorEffect="non-scaling-stroke" />{/* 끝 점: 늘어나는 그림(preserveAspectRatio none)에서도 둥글게 길이 0 선의 둥근 끝으로 */}
      {ty != null && <line x1={0} x2={BOX.w} y1={ty} y2={ty} stroke="var(--muted)" strokeWidth={1.5} strokeDasharray="4 3" vectorEffect="non-scaling-stroke"><title>{goal}</title></line>}
      {all.map((p, i) => p.v != null && <rect key={p.key} x={BOX.w * (i - 0.5) / (all.length - 1)} width={BOX.w / (all.length - 1)} height={BOX.h} fill="transparent"><title>{`${p.key}: ${showValue(p.v, fmt, unit, view)}`}</title></rect>)}{/* 마우스를 올리면 그날 값 */}
    </svg>
  );
  return (
    <Box cls="kpi">
      <div className="kpi-text">
        <div className="kpi-val" title={goal} style={{ "--n": textUnits(num + unit).toFixed(2), color: met ? "var(--ok)" : undefined } as CSSProperties}>{num}{unit && <span className="kpi-unit">{unit}</span>}</div>
        {said && <div className={"bcmp kpi-cmp " + said.tone} title={said.text}>{kpiDelta(said, label, fmt)}</div>}
        {pair && <div className="hint kpi-pair">{`${pair.num_label} ${showValue(pair.num)} · ${pair.den_label} ${showValue(pair.den)}`}</div>}
      </div>
      {spark}
    </Box>
  );
}
