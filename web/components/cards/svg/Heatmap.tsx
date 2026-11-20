"use client";
// heatmap(P14): 두 그룹 컬럼의 값 조합마다 칸(data.values = {rows, cols, cells[행][열]}, 셀 상한은 서버). 진하기는 값의 최소~최대(fillLevels), 빈 칸은 선 색. 칸이 100개 이하면 값 글자도
import { shownum } from "@/lib/calc";
import { tickBy } from "../chart/Charts";
import { useRootScale } from "../chart/scale";
import { fillLevels, fitText } from "./__pure__";
import { Box, Frame, NOTHING, Say, more, tone, type P } from "./common";

const CW0 = 44, CH = 24, LW0 = 90, HH = 22, FONT = 11;  // 칸 폭·높이, 줄 이름 폭, 머리글 높이, 이름 글자 크기(화면 크기 배율 1)
const GAP = 3, RX = 4;  // 칸 사이 틈, 칸 모서리 둥글기(예시 42-heatmap: 칸 22x19, 간격 25x22, rx 4)

export function Heatmap(p: P) {
  const s = useRootScale(), CW = CW0 * s, LW = LW0 * s, font = FONT * s;  // 글자가 가로로 들어갈 자리(칸 폭·줄 이름 폭)는 글자와 같이 커진다
  const { rows, cols, cells } = more(p);
  if (!rows?.length || !cols?.length || !cells) return <Say text={NOTHING} />;
  const levels = fillLevels(rows.flatMap((_, i) => cols.map((_, j) => cells[i]?.[j]))), c = tone(p, "주 색", 0), words = rows.length * cols.length <= 100;
  const real = cells.flat().filter((x): x is number => typeof x === "number"), short = tickBy((Math.max(...real) - Math.min(...real)) / 100);  // 칸 숫자: 값 범위의 1/100까지(툴팁은 전체)
  return (
    <Box cls="heatmap nm-well">
      <Frame w={LW + cols.length * CW} h={HH + rows.length * CH} label={p.card.title}>
        {cols.map((name, j) => <text key={j} x={LW + j * CW + CW / 2} y={HH - 6} fontSize={font} textAnchor="middle" fill="var(--muted)">{fitText(name, CW - 4, font)}</text>)}
        {rows.map((name, i) => (
          <g key={i}>
            <text x={LW - 6} y={HH + i * CH + CH / 2} fontSize={font} textAnchor="end" dominantBaseline="middle" fill="var(--muted)">{fitText(name, LW - 10, font)}</text>
            {cols.map((col, j) => {
              const v = cells[i]?.[j], o = levels[i * cols.length + j];
              return (
                <g key={j}>
                  <rect x={LW + j * CW + GAP / 2} y={HH + i * CH + GAP / 2} width={CW - GAP} height={CH - GAP} rx={RX} fill={o == null ? "var(--line)" : c} fillOpacity={o ?? 1}><title>{`${name} × ${col}: ${v == null ? "-" : shownum(v)}`}</title></rect>
                  {words && v != null && <text x={LW + j * CW + CW / 2} y={HH + i * CH + CH / 2} fontSize={10 * s} textAnchor="middle" dominantBaseline="middle" fill="var(--text)" stroke="var(--card)" strokeWidth={3} paintOrder="stroke" pointerEvents="none">{short(v)}</text>}
                </g>
              );
            })}
          </g>
        ))}
      </Frame>
    </Box>
  );
}
