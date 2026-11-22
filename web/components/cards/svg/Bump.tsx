"use client";
// bump(P14): 기간마다 대상 순위(data.values = {labels: 기간, series: [{name: 대상, values: 값, ranks: 순위(1 = 1등)}]}, 서버 rank 창). 1등이 위(세로 축 뒤집음), 정수 눈금, 툴팁에 순위와 값
import { ComposedChart, Line, Tooltip, XAxis, YAxis } from "recharts";
import { shownum } from "@/lib/calc";
import { AXIS, Frame, MARGIN, TIP } from "../chart/Charts";
import { MIN_POINTS, sliceWindow } from "./__pure__";
import { useZoomX } from "../useZoomX";
import { Box, Empty, more, tone, type P } from "./common";

const LINE_W = 4, DOT_R = 9, DOT_FONT = 9.5, DOT_TEXT_DY = 3.5;  // 선 굵기, 점 반지름, 점 안 첫 글자 크기·아래 내림(예시 10-bump)

export function Bump(p: P) {
  const { labels, series } = more(p);
  const z = useZoomX(labels && labels.length > MIN_POINTS ? { kind: "index", count: labels.length } : null);
  if (!labels?.length || !series?.length) return <Empty />;
  const rows = labels.map((label, j) => Object.fromEntries([["label", label], ...series.flatMap((d, i) => [[`s${i}`, d.ranks?.[j] ?? null], [`v${i}`, d.values[j]]])]));
  const worst = Math.max(1, ...series.flatMap(d => (d.ranks ?? []).filter((r): r is number => r != null))), color = series.map((d, i) => tone(p, d.name, i));
  return (
    <Box cls="bump nm-well">
      <div {...z.bind}>
      <div className="flex-1 min-h-0 h-full">
        <Frame keys={series.map((d, i) => ({ name: d.name, color: color[i] }))}>
          <ComposedChart data={sliceWindow(rows, z.view)} margin={MARGIN}>
            <XAxis dataKey="label" {...AXIS} />
            <YAxis {...AXIS} reversed domain={[1, worst]} allowDecimals={false} width={32} tickFormatter={(v: unknown) => `${v}위`} />
            {series.map((d, i) => <Line key={i} dataKey={`s${i}`} name={d.name} type="monotone" stroke={color[i]} strokeWidth={LINE_W} strokeLinecap="round" dot={({ cx, cy, index }: { cx?: number; cy?: number; index?: number }) => <g key={index}>{cx != null && cy != null && <><circle className="nm-raise" cx={cx} cy={cy} r={DOT_R} fill={color[i]} /><text x={cx} y={cy + DOT_TEXT_DY} textAnchor="middle" fontSize={DOT_FONT} fontWeight={700} fill="var(--card)" pointerEvents="none">{d.name.slice(0, 1)}</text></>}</g>} connectNulls isAnimationActive={false} />)}
            <Tooltip {...TIP} formatter={(v: unknown, _n: unknown, item: { dataKey?: unknown; payload?: Record<string, unknown> }) => `${v}위 (${shownum(item.payload?.[String(item.dataKey).replace("s", "v")])})`} />
          </ComposedChart>
        </Frame>
      </div>
      </div>
    </Box>
  );
}
