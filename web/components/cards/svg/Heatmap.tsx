"use client";
// heatmap(P14): 두 그룹 컬럼의 값 조합마다 칸(data.values = {rows, cols, cells[행][열]}, 셀 상한은 서버). 진하기는 값의 최소~최대(fillLevels), 빈 칸은 선 색. 칸이 100개 이하면 값 글자도
import { shownum } from "@/lib/calc";
import { tickBy } from "../chart/Charts";
import { fillLevels, fitText } from "./__pure__";
import { Box, Frame, NOTHING, Say, more, tone, type P } from "./common";

const CW = 44, CH = 24, LW = 90, HH = 22;

export function Heatmap(p: P) {
  const { rows, cols, cells } = more(p);
  if (!rows?.length || !cols?.length || !cells) return <Say text={NOTHING} />;
  const levels = fillLevels(rows.flatMap((_, i) => cols.map((_, j) => cells[i]?.[j]))), c = tone(p, "주 색", 0), words = rows.length * cols.length <= 100;
  const real = cells.flat().filter((x): x is number => typeof x === "number"), short = tickBy((Math.max(...real) - Math.min(...real)) / 100);  // 칸 숫자: 값 범위의 1/100까지(툴팁은 전체)
  return (
    <Box cls="heatmap">
      <Frame w={LW + cols.length * CW} h={HH + rows.length * CH} label={p.card.title}>
        {cols.map((name, j) => <text key={j} x={LW + j * CW + CW / 2} y={HH - 6} fontSize={11} textAnchor="middle" fill="var(--muted)">{fitText(name, CW - 4, 11)}</text>)}
        {rows.map((name, i) => (
          <g key={i}>
            <text x={LW - 6} y={HH + i * CH + CH / 2} fontSize={11} textAnchor="end" dominantBaseline="middle" fill="var(--muted)">{fitText(name, LW - 10, 11)}</text>
            {cols.map((col, j) => {
              const v = cells[i]?.[j], o = levels[i * cols.length + j];
              return (
                <g key={j}>
                  <rect x={LW + j * CW + 1} y={HH + i * CH + 1} width={CW - 2} height={CH - 2} rx={3} fill={o == null ? "var(--line)" : c} fillOpacity={o ?? 1}><title>{`${name} × ${col}: ${v == null ? "-" : shownum(v)}`}</title></rect>
                  {words && v != null && <text x={LW + j * CW + CW / 2} y={HH + i * CH + CH / 2} fontSize={10} textAnchor="middle" dominantBaseline="middle" fill="var(--text)" stroke="var(--card)" strokeWidth={3} paintOrder="stroke" pointerEvents="none">{short(v)}</text>}
                </g>
              );
            })}
          </g>
        ))}
      </Frame>
    </Box>
  );
}
