"use client";
// calheat(P14): 날마다 값(data.values.days = [{d, v}], 보는 기간 ctx.range 안만)을 달력 칸 색으로. 열 = 주, 줄 = 요일(상수 week_start), 진하기는 fillLevels, 값 없는 날은 선 색
import { showValue } from "@/lib/calc";
import { useConstants } from "@/lib/constants";
import { useRootScale } from "../chart/scale";
import { calendarCells, cardFormat, fillLevels } from "./__pure__";
import { Box, Frame, NOTHING, Say, more, tone, type P } from "./common";

const S = 13, G = 2, LW0 = 22, HH0 = 16;  // 칸·간격, 요일 이름 폭·달 이름 높이(화면 크기 배율 1)
const RX = 3;  // 칸 모서리 둥글기(예시 14-calendar: 칸 10.6에 rx 2.5 = 칸 13에 약 3)

export function CalHeat(p: P) {
  const s = useRootScale(), LW = LW0 * s, HH = HH0 * s;  // 글자 자리는 글자와 같이 커진다
  const sunday = useConstants()?.week_start === "sun", r = p.range;
  const days = (more(p).days ?? []).filter(x => (!r?.start || x.d >= r.start) && (!r?.end || x.d <= r.end)).map(x => ({ day: x.d, v: x.v }));  // 정렬·빠진 날 채우기는 calendarCells
  if (!days.some(d => d.v != null)) return <Say text={NOTHING} />;
  const { cells, weeks, months } = calendarCells(days, sunday), levels = fillLevels(cells.map(d => d.v)), c = tone(p, "주 색", 0), fmt = cardFormat(p.card);
  const names = sunday ? ["일", "", "화", "", "목", "", "토"] : ["월", "", "수", "", "금", "", "일"];
  return (
    <Box cls="calheat nm-well">
      <Frame w={LW + weeks * (S + G)} h={HH + 7 * (S + G)} label={p.card.title}>
        {months.map(m => <text key={m.col} x={LW + m.col * (S + G)} y={HH - 5 * s} fontSize={10 * s} fill="var(--muted)">{m.label}</text>)}
        {names.map((n, r) => n && <text key={r} x={LW - 4} y={HH + r * (S + G) + S / 2} fontSize={9 * s} textAnchor="end" dominantBaseline="middle" fill="var(--muted)">{n}</text>)}
        {cells.map((d, i) => (
          <rect key={d.day} x={LW + d.col * (S + G)} y={HH + d.row * (S + G)} width={S} height={S} rx={RX} fill={levels[i] == null ? "var(--line)" : c} fillOpacity={levels[i] ?? 1}>
            <title>{`${d.day}: ${d.v == null ? "-" : showValue(d.v, fmt, p.card.view?.unit_text)}`}</title>
          </rect>
        ))}
      </Frame>
    </Box>
  );
}
