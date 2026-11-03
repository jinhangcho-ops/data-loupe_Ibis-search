"use client";
// kpi: 아이콘 · 큰 값 · 비교 문장(화면 틀 useCompare) · 스파크라인(일 값 곡선, 카드 높이를 채움) · 비율이면 짝 절대량(옛 paintKpi)
import type { CSSProperties } from "react";
import { alpha, showValue, smoothPath, sparkPoints } from "@/lib/calc";
import { useCompare } from "@/components/shell/ShellProvider";
import { pickColor } from "@/lib/series";
import { cardDays, cardFormat, cardValue } from "./__pure__";
import { Box, Icon, type P } from "./common";

type Pair = { num_label: string; num: number | null; den_label: string; den: number | null };

export function Kpi({ card, colors, range, merge }: P) {
  const v = cardValue(card, 0, range, merge), fmt = cardFormat(card), days = cardDays(card, 0, range, merge).map(d => d.v), pair = card.data?.pair as Pair | undefined;
  const color = pickColor(card, "series", card.data?.by_unit?.day?.series[0]?.name ?? (card.data?.values?.names as string[] | undefined)?.[0] ?? card.title, colors, 0);
  const pts = days.filter(x => x != null).length > 1 ? sparkPoints(days, 120, 30) : "", curve = pts && smoothPath(pts), xs = pts ? pts.split(" ").map(s => s.split(",")[0]) : [];
  const text = showValue(v, fmt, card.view?.unit_text), said = useCompare(card.id);
  return (
    <Box cls="kpi">
      <div className="kpi-top">
        <span className="kpi-icon" style={{ color }}><Icon name={card.view?.icon} /></span>
        <div className="kpi-val" style={{ "--n": text.length } as CSSProperties}>{text}</div>
      </div>
      {said && <div className={"bcmp " + said.tone} title={said.delta_text}>{said.text}</div>}
      {pts && (
        <svg viewBox="0 0 120 30" className="kpi-spark" preserveAspectRatio="none" aria-hidden="true">
          <path d={`${curve} L${xs.at(-1)},30 L${xs[0]},30 Z`} fill={alpha(color, 0.12)} stroke="none" />
          <path d={curve} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
        </svg>
      )}
      {pair && <div className="hint kpi-pair">{`${pair.num_label} ${showValue(pair.num)} · ${pair.den_label} ${showValue(pair.den)}`}</div>}
    </Box>
  );
}
