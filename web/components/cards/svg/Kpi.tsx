"use client";
// kpi: 아이콘 · 큰 값 · 스파크라인(일 값 곡선) · 아래에 비교 문장(화면 틀 useCompare)과 비율이면 짝 절대량(옛 paintKpi).
// P15: 큰 값은 상자 크기(컨테이너 쿼리, dashboard.css)에 맞춰 가로·세로 가운데, view.fit(fill = 꽉 채우기, center = 가운데 기본).
// P16: view.layout(below = 추이선이 값 아래 기본, right = 값 오른쪽), 목표(cardTarget)가 있으면 추이선에 점선과 값 색(목표를 채우면 --ok, 좋은 방향 view.better를 따른다)
import type { CSSProperties } from "react";
import { alpha, overTarget, showValue, smoothPath, sparkPoints, underTarget } from "@/lib/calc";
import { useCompare } from "@/components/shell/ShellProvider";
import { pickColor } from "@/lib/series";
import { cardDays, cardFormat, cardTarget, cardValue, sparkY, textUnits } from "./__pure__";
import { Box, Icon, type P } from "./common";

type Pair = { num_label: string; num: number | null; den_label: string; den: number | null };

export function Kpi({ card, colors, range, merge }: P) {
  const v = cardValue(card, 0, range, merge), fmt = cardFormat(card), days = cardDays(card, 0, range, merge).map(d => d.v), pair = card.data?.pair as Pair | undefined;
  const color = pickColor(card, "series", card.data?.by_unit?.day?.series[0]?.name ?? (card.data?.values?.names as string[] | undefined)?.[0] ?? card.title, colors, 0);
  const pts = days.filter(x => x != null).length > 1 ? sparkPoints(days, 120, 30) : "", curve = pts && smoothPath(pts), xs = pts ? pts.split(" ").map(s => s.split(",")[0]) : [];
  const view = card.view ?? {}, unit = view.unit_text, text = showValue(v, fmt, unit), said = useCompare(card.id), t = cardTarget(card);
  const layout = view.layout === "right" ? "right" : "below", fit = view.fit === "fill" ? "fill" : "center";
  const met = v != null && t != null && (view.better === "down" ? !overTarget(v, t) : !underTarget(v, t));
  const goal = t == null ? undefined : `목표 ${showValue(t, fmt, unit)}`, ty = t != null && pts ? sparkY(days, t, 30) : null;
  const spark = pts && (
    <svg viewBox="0 0 120 30" className="kpi-spark" preserveAspectRatio="none" aria-hidden="true">
      <path d={`${curve} L${xs.at(-1)},30 L${xs[0]},30 Z`} fill={alpha(color, 0.12)} stroke="none" />
      <path d={curve} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      {ty != null && <line x1={0} x2={120} y1={ty} y2={ty} stroke="var(--muted)" strokeWidth={1.5} strokeDasharray="4 3" vectorEffect="non-scaling-stroke"><title>{goal}</title></line>}
    </svg>
  );
  return (
    <Box cls={`kpi fit-${fit} lay-${layout}`}>
      <div className="kpi-main">
        <div className="kpi-top">
          <span className="kpi-icon" style={{ color }}><Icon name={view.icon} /></span>
          <div className="kpi-val" title={goal} style={{ "--n": textUnits(text).toFixed(2), color: met ? "var(--ok)" : undefined } as CSSProperties}>{text}</div>
        </div>
        {layout === "right" && spark}
      </div>
      {layout === "below" && spark}
      <div className="kpi-foot">
        {said && <div className={"bcmp " + said.tone} title={said.delta_text}>{said.text}</div>}
        {pair && <div className="hint kpi-pair">{`${pair.num_label} ${showValue(pair.num)} · ${pair.den_label} ${showValue(pair.den)}`}</div>}
      </div>
    </Box>
  );
}
