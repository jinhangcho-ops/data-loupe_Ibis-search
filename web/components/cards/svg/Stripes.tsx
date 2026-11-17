"use client";
// stripes: 칸 수 N, 채운 칸 = round(비율 × N), 목표 칸 강조. 비율 = 값 / 가득 찬 값(view.full, 없으면 목표, 비율 값이면 1)(옛 paintStripes)
import { showValue, stripeCells } from "@/lib/calc";
import { pickColor } from "@/lib/series";
import { useRootScale } from "../chart/scale";
import { cardFormat, cardTarget, cardValue, growFont } from "./__pure__";
import { Frame, type P } from "./common";

const FONT = 12, TEXT_TOP = 70, FOOT = 8;  // 아래 글: 기준 글자 크기, 글자 윗자리(칸 아래), 기준선 아래 여백

export function Stripes({ card, colors, range, merge }: P) {
  const s = useRootScale(), n = card.view?.cells || 10, v = cardValue(card, 0, range, merge) ?? 0, target = cardTarget(card), fmt = cardFormat(card), full = card.view?.full || target || (fmt === "percent" ? 1 : Math.max(v, 1));
  const cells = stripeCells(v / full, n, target == null ? null : target / full), W = 260, gap = 4, w = (W - gap * (n - 1)) / n, color = pickColor(card, "series", card.title, colors, 0);
  const says = `${showValue(v, fmt, card.view?.unit_text)}${target != null ? ` / 목표 ${showValue(target, fmt)}` : ""}`, font = growFont(says, W, FONT, s), y = TEXT_TOP + font;  // 글자가 커지면 그만큼 그림을 아래로 늘린다
  return (
    <Frame w={W} h={y + FOOT} label={card.title}>
      {Array.from({ length: n }, (_, i) => <rect key={i} x={i * (w + gap)} y={20} width={w} height={40} rx={3} fill={i < cells.filled ? color : colors.line} stroke={cells.target === i + 1 ? colors.ink : "none"} strokeWidth={2} />)}
      <text x={0} y={y} fontSize={font} fill={colors.text}>{says}</text>
    </Frame>
  );
}
