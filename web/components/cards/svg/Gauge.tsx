"use client";
// gauge: 항목 몫으로 나눈 호(간격 0.06rad, 모두 같은 굵기·그림자 없음), 오른쪽에 범례 8개까지(옛 paintGauge)
import { arcPath, gaugeSegments, showValue } from "@/lib/calc";
import { pickColor } from "@/lib/series";
import { useRootScale } from "../chart/scale";
import { cardFormat, growFont } from "./__pure__";
import { Box, Frame, type P } from "./common";

// 범례(배율 1): 글자 크기, 줄 간격, 줄 수, 첫 줄 위치, 견본 칸, 견본 왼쪽 자리, 글자 자리 폭, 줄 간격 ÷ 글자 크기의 하한. 가운데 글자의 기준 크기와 자리 폭
const FONT = 11, PITCH = 18, ROWS = 8, TOP = 40, SWATCH = 12, LEFT = 170, TEXT_W = 75, LINE = 1.2, MAIN = 16, HOLE = 100;

const THICK = 22, SWATCH_RX = 4, SWATCH_TEXT_DY = 10;  // 74-gauge: 호·트랙 굵기. 와플의 범례 견본(12칸, 모서리 4)과 견본 옆 글 기준선

export function Gauge({ card, colors }: P) {
  const scale = useRootScale(), v = card.data?.values, labels = v?.labels ?? [], vals = (v?.series?.[0]?.values ?? []) as (number | null)[], segs = gaugeSegments(vals);
  const total = vals.filter((x): x is number => x != null && x > 0).reduce((a, b) => a + b, 0), cx = 90, r = 62, fmt = cardFormat(card);
  const pitch = Math.max(PITCH, FONT * scale * LINE), H = TOP + ROWS * pitch + 16, W = LEFT + (SWATCH + 5 + TEXT_W) * scale, cy = H / 2, km = growFont(showValue(total, fmt), HOLE, MAIN, scale) / MAIN;  // 범례가 커지는 만큼 그림 상자를 늘린다
  const color = (i: number) => pickColor(card, "parts", labels[i], colors, i);
  return (
    <Box cls="nm-well"><Frame w={W} h={H} label={card.title}>
      <circle cx={cx} cy={cy} r={r} fill="none" stroke={colors.line} strokeWidth={THICK} />{/* 호 아래 트랙(그림자 없음) */}
      {segs.map(s => (
        <path key={s.index} d={arcPath(cx, cy, r, s.a0, s.a1)} fill="none" stroke={color(s.index)} strokeWidth={THICK}>
          <title>{`${labels[s.index]}: ${showValue(vals[s.index], fmt)} (${+(s.share * 100).toFixed(1)}%)`}</title>
        </path>
      ))}
      <text x={cx} y={cy + 6 * km} textAnchor="middle" fontSize={MAIN * km} fontWeight={700} fill={colors.ink}>{showValue(total, fmt)}</text>
      {segs.slice(0, ROWS).map((s, k) => (
        <g key={"l" + s.index}>
          <rect x={LEFT} y={TOP + k * pitch} width={SWATCH * scale} height={SWATCH * scale} rx={SWATCH_RX} fill={color(s.index)} />
          <text x={LEFT + (SWATCH + 5) * scale} y={TOP + k * pitch + SWATCH_TEXT_DY * scale} fontSize={FONT * scale} fill={colors.text}>{`${labels[s.index]} ${+(s.share * 100).toFixed(1)}%`}</text>
        </g>
      ))}
    </Frame></Box>
  );
}
