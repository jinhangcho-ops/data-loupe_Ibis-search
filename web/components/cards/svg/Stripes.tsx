"use client";
// stripes: 칸 수 N, 채운 칸 = round(비율 × N), 목표 칸 강조. 비율 = 값 / 가득 찬 값(view.full, 없으면 목표, 비율 값이면 1)(옛 paintStripes)
import { showValue, stripeCells } from "@/lib/calc";
import { pickColor } from "@/lib/series";
import { useRootScale } from "../chart/scale";
import { cardFormat, cardTarget, cardValue, growFont } from "./__pure__";
import { Frame, type P } from "./common";

const FONT = 12, TEXT_TOP = 50, FOOT = 8;  // 아래 글: 기준 글자 크기, 글자 윗자리(칸 아래), 기준선 아래 여백
const CELL_H = 14, CELL_RX = 7;  // 76-progress: 칸 높이와 모서리(막대 14에 rx 7). 채운 칸만 솟는다

export function Stripes({ card, colors, range, merge }: P) {
  const s = useRootScale(), n = card.view?.cells || 10, v = cardValue(card, 0, range, merge) ?? 0, target = cardTarget(card), fmt = cardFormat(card), full = card.view?.full || target || (fmt === "percent" ? 1 : Math.max(v, 1));
  const cells = stripeCells(v / full, n, target == null ? null : target / full), W = 260, gap = 4, w = (W - gap * (n - 1)) / n, color = pickColor(card, "series", card.title, colors, 0);
  const says = `${showValue(v, fmt, card.view?.unit_text, card.view)}${target != null ? ` / 목표 ${showValue(target, fmt, "", card.view)}` : ""}`, font = growFont(says, W, FONT, s), y = TEXT_TOP + font;  // 글자가 커지면 그만큼 그림을 아래로 늘린다
  return (
    <div className="nm-well h-full">
    <Frame w={W} h={y + FOOT} label={card.title}>
      {Array.from({ length: n }, (_, i) => <rect key={i} className={i < cells.filled ? "nm-raise" : undefined} x={i * (w + gap)} y={20} width={w} height={CELL_H} rx={CELL_RX} fill={i < cells.filled ? color : colors.line} stroke={cells.target === i + 1 ? colors.ink : "none"} strokeWidth={2} />)}
      <text x={0} y={y} fontSize={font} fill={colors.text}>{says}</text>
    </Frame>
    </div>
  );
}
