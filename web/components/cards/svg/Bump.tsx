"use client";
// bump(P14): 기간마다 대상 순위(data.values = {labels: 기간, series: [{name: 대상, values: 값, ranks: 순위(1 = 1등)}]}, 서버 rank 창). 1등이 위(세로 축 뒤집음), 정수 눈금, 툴팁에 순위와 값
import { ComposedChart, Line, Tooltip, XAxis, YAxis } from "recharts";
import { shownum } from "@/lib/calc";
import { AXIS, Frame, MARGIN, TIP } from "../chart/Charts";
import { Box, NOTHING, Say, more, tone, type P } from "./common";

export function Bump(p: P) {
  const { labels, series } = more(p);
  if (!labels?.length || !series?.length) return <Say text={NOTHING} />;
  const rows = labels.map((label, j) => Object.fromEntries([["label", label], ...series.flatMap((d, i) => [[`s${i}`, d.ranks?.[j] ?? null], [`v${i}`, d.values[j]]])]));
  const worst = Math.max(1, ...series.flatMap(d => (d.ranks ?? []).filter((r): r is number => r != null))), color = series.map((d, i) => tone(p, d.name, i));
  return (
    <Box cls="bump">
      <div style={{ flex: 1, minHeight: 0 }}>
        <Frame keys={series.map((d, i) => ({ name: d.name, color: color[i] }))}>
          <ComposedChart data={rows} margin={MARGIN}>
            <XAxis dataKey="label" {...AXIS} />
            <YAxis {...AXIS} reversed domain={[1, worst]} allowDecimals={false} width={32} tickFormatter={(v: unknown) => `${v}위`} />
            {series.map((d, i) => <Line key={i} dataKey={`s${i}`} name={d.name} type="linear" stroke={color[i]} strokeWidth={3} dot={{ r: 4, strokeWidth: 0, fill: color[i] }} connectNulls isAnimationActive={false} />)}
            <Tooltip {...TIP} formatter={(v: unknown, _n: unknown, item: { dataKey?: unknown; payload?: Record<string, unknown> }) => `${v}위 (${shownum(item.payload?.[String(item.dataKey).replace("s", "v")])})`} />
          </ComposedChart>
        </Frame>
      </div>
    </Box>
  );
}
