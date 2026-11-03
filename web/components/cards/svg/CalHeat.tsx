"use client";
// calheat(P14): 날마다 값(data.values.days = [{d, v}], 보는 기간 ctx.range 안만)을 달력 칸 색으로. 열 = 주, 줄 = 요일(상수 week_start), 진하기는 fillLevels, 값 없는 날은 선 색
import { showValue } from "@/lib/calc";
import { useConstants } from "@/lib/constants";
import { calendarCells, cardFormat, fillLevels } from "./__pure__";
import { Box, Frame, NOTHING, Say, more, tone, type P } from "./common";

const S = 13, G = 2, LW = 22, HH = 16;

export function CalHeat(p: P) {
  const sunday = useConstants()?.week_start === "sun", r = p.range;
  const days = (more(p).days ?? []).filter(x => (!r?.start || x.d >= r.start) && (!r?.end || x.d <= r.end)).map(x => ({ day: x.d, v: x.v }));  // 정렬·빠진 날 채우기는 calendarCells
  if (!days.some(d => d.v != null)) return <Say text={NOTHING} />;
  const { cells, weeks, months } = calendarCells(days, sunday), levels = fillLevels(cells.map(d => d.v)), c = tone(p, "주 색", 0), fmt = cardFormat(p.card);
  const names = sunday ? ["일", "", "화", "", "목", "", "토"] : ["월", "", "수", "", "금", "", "일"];
  return (
    <Box cls="calheat">
      <Frame w={LW + weeks * (S + G)} h={HH + 7 * (S + G)} label={p.card.title}>
        {months.map(m => <text key={m.col} x={LW + m.col * (S + G)} y={HH - 5} fontSize={10} fill="var(--muted)">{m.label}</text>)}
        {names.map((n, r) => n && <text key={r} x={LW - 4} y={HH + r * (S + G) + S / 2} fontSize={9} textAnchor="end" dominantBaseline="middle" fill="var(--muted)">{n}</text>)}
        {cells.map((d, i) => (
          <rect key={d.day} x={LW + d.col * (S + G)} y={HH + d.row * (S + G)} width={S} height={S} rx={2} fill={levels[i] == null ? "var(--line)" : c} fillOpacity={levels[i] ?? 1}>
            <title>{`${d.day}: ${d.v == null ? "-" : showValue(d.v, fmt, p.card.view?.unit_text)}`}</title>
          </rect>
        ))}
      </Frame>
    </Box>
  );
}
