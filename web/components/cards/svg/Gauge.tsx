"use client";
// gauge: 항목 몫으로 나눈 호(간격 0.06rad), 가장 큰 항목은 굵게, 오른쪽에 범례 8개까지(옛 paintGauge)
import { arcPath, gaugeSegments, showValue } from "@/lib/calc";
import { pickColor } from "@/lib/series";
import { useRootScale } from "../chart/scale";
import { cardFormat, growFont } from "./__pure__";
import { Frame, type P } from "./common";

// 범례(배율 1): 글자 크기, 줄 간격, 줄 수, 첫 줄 위치, 견본 칸, 견본 왼쪽 자리, 글자 자리 폭, 줄 간격 ÷ 글자 크기의 하한. 가운데 글자의 기준 크기와 자리 폭
const FONT = 11, PITCH = 18, ROWS = 8, TOP = 40, SWATCH = 10, LEFT = 170, TEXT_W = 75, LINE = 1.2, MAIN = 16, HOLE = 100;

export function Gauge({ card, colors }: P) {
  const scale = useRootScale(), v = card.data?.values, labels = v?.labels ?? [], vals = (v?.series?.[0]?.values ?? []) as (number | null)[], segs = gaugeSegments(vals);
  const total = vals.filter((x): x is number => x != null && x > 0).reduce((a, b) => a + b, 0), cx = 90, r = 62, fmt = cardFormat(card);
  const pitch = Math.max(PITCH, FONT * scale * LINE), H = TOP + ROWS * pitch + 16, W = LEFT + (SWATCH + 5 + TEXT_W) * scale, cy = H / 2, km = growFont(showValue(total, fmt), HOLE, MAIN, scale) / MAIN;  // 범례가 커지는 만큼 그림 상자를 늘린다
  const color = (i: number) => pickColor(card, "parts", labels[i], colors, i);
  return (
    <Frame w={W} h={H} label={card.title}>
      {segs.map(s => (
        <path key={s.index} d={arcPath(cx, cy, r, s.a0, s.a1)} fill="none" stroke={color(s.index)} strokeWidth={s.max ? 24 : 14}>
          <title>{`${labels[s.index]}: ${showValue(vals[s.index], fmt)} (${+(s.share * 100).toFixed(1)}%)`}</title>
        </path>
      ))}
      <text x={cx} y={cy + 6 * km} textAnchor="middle" fontSize={MAIN * km} fontWeight={700} fill={colors.ink}>{showValue(total, fmt)}</text>
      {segs.slice(0, ROWS).map((s, k) => (
        <g key={"l" + s.index}>
          <rect x={LEFT} y={TOP + k * pitch} width={SWATCH * scale} height={SWATCH * scale} rx={2} fill={color(s.index)} />
          <text x={LEFT + (SWATCH + 5) * scale} y={TOP + k * pitch + 9 * scale} fontSize={FONT * scale} fill={colors.text}>{`${labels[s.index]} ${+(s.share * 100).toFixed(1)}%`}</text>
        </g>
      ))}
    </Frame>
  );
}
