"use client";
// deviation: 엔티티 × 시간대 표. 칸 = 값, 기준(view.baseline, 없으면 그 엔티티 평균) 대비 ±. 허용 폭(view.threshold, 없으면 기준의 20%)을 넘는 칸을 강조(옛 paintDeviation)
import { alpha, deviationCells, showValue } from "@/lib/calc";
import { cardFormat } from "./__pure__";
import { Box, Say, type P } from "./common";

export function Deviation({ card, colors }: P) {
  const v = card.data?.values as { entities: { name: string; values: (number | null)[] }[]; buckets: string[] } | undefined, view = card.view, fmt = cardFormat(card);
  if (!v?.entities?.length) return <Say cls="alerts" text="값이 없어요" />;
  const label = (b: string) => String(b).replace(" 00:00:00", "").slice(0, view.bucket === "hour" || !view.bucket ? 16 : 10);
  return (
    <Box cls="deviation">
      <table>
        <thead><tr><th></th><th>기준</th>{v.buckets.map((b, i) => <th key={i}>{label(b)}</th>)}</tr></thead>
        <tbody>{v.entities.map(e => {
          const d = deviationCells(e.values, view.baseline, view.threshold);
          return (
            <tr key={e.name}>
              <th>{e.name}</th>
              <td className="num">{showValue(d.base, fmt)}</td>
              {d.cells.map((c, i) => (
                <td key={i} className="num" style={c.over ? { background: alpha(c.dev! > 0 ? colors.err : colors.series[0], 0.3) } : undefined}>
                  {c.v == null ? "" : showValue(c.v, fmt)}
                  {c.dev == null ? null : <div className="hint">{(c.dev > 0 ? "+" : c.dev < 0 ? "−" : "±") + showValue(Math.abs(c.dev), fmt)}</div>}
                </td>
              ))}
            </tr>
          );
        })}</tbody>
      </table>
    </Box>
  );
}
