"use client";
// blocks: 칸 하나 = view.size. 실제(채움) · 목표까지(점선 윤곽) · 목표를 넘은 칸(진한 색)(옛 paintBlocks)
import { blockCells, darken, showValue } from "@/lib/calc";
import { pickColor } from "@/lib/series";
import { cardFormat, cardTarget, cardValue } from "./__pure__";
import { Frame, Say, type P } from "./common";

export function Blocks({ card, colors, range, merge }: P) {
  const v = cardValue(card, 0, range, merge) ?? 0;
  if (v < 0) return <Say text="값이 음수라 블록으로 그릴 수 없어요" />;
  const target = cardTarget(card), fmt = cardFormat(card), size = card.view?.size || Math.max(+(Math.max(v, target ?? 0) / 20).toPrecision(1), 1e-9);
  const b = blockCells(v, target, size), per = 20, W = 260, cell = W / per - 3, color = pickColor(card, "series", card.title, colors, 0), total = b.actual + b.toTarget;
  const H = Math.ceil(total / per) * (cell + 3) + 28;
  return (
    <Frame w={W} h={H} label={card.title}>
      {Array.from({ length: total }, (_, i) => (
        <rect key={i} x={(i % per) * (cell + 3)} y={Math.floor(i / per) * (cell + 3)} width={cell} height={cell} rx={2}
          fill={i < b.actual - b.over ? color : i < b.actual ? darken(color) : "none"} stroke={i < b.actual ? "none" : color} strokeDasharray={i < b.actual ? undefined : "3 2"} />
      ))}
      <text x={0} y={H - 8} fontSize={11} fill={colors.text}>{`${showValue(v, fmt)}${target != null ? ` / 목표 ${showValue(target, fmt)}` : ""} (한 칸 ${showValue(size * b.scale, fmt)})`}</text>
    </Frame>
  );
}
