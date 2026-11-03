"use client";
// gauge: 항목 몫으로 나눈 호(간격 0.06rad), 가장 큰 항목은 굵게, 오른쪽에 범례 8개까지(옛 paintGauge)
import { arcPath, gaugeSegments, showValue } from "@/lib/calc";
import { pickColor } from "@/lib/series";
import { cardFormat } from "./__pure__";
import { Frame, type P } from "./common";

export function Gauge({ card, colors }: P) {
  const v = card.data?.values, labels = v?.labels ?? [], vals = (v?.series?.[0]?.values ?? []) as (number | null)[], segs = gaugeSegments(vals);
  const total = vals.filter((x): x is number => x != null && x > 0).reduce((a, b) => a + b, 0), cx = 90, cy = 100, r = 62, fmt = cardFormat(card);
  const color = (i: number) => pickColor(card, "parts", labels[i], colors, i);
  return (
    <Frame w={260} h={200} label={card.title}>
      {segs.map(s => (
        <path key={s.index} d={arcPath(cx, cy, r, s.a0, s.a1)} fill="none" stroke={color(s.index)} strokeWidth={s.max ? 24 : 14}>
          <title>{`${labels[s.index]}: ${showValue(vals[s.index], fmt)} (${+(s.share * 100).toFixed(1)}%)`}</title>
        </path>
      ))}
      <text x={cx} y={cy + 6} textAnchor="middle" fontSize={16} fontWeight={700} fill={colors.ink}>{showValue(total, fmt)}</text>
      {segs.slice(0, 8).map((s, k) => (
        <g key={"l" + s.index}>
          <rect x={170} y={40 + k * 18} width={10} height={10} rx={2} fill={color(s.index)} />
          <text x={185} y={49 + k * 18} fontSize={11} fill={colors.text}>{`${labels[s.index]} ${+(s.share * 100).toFixed(1)}%`}</text>
        </g>
      ))}
    </Frame>
  );
}
