"use client";
// blocks: 칸 하나 = view.size. 실제(채움) · 목표까지(트랙 색) · 목표를 넘은 칸(진한 색)(옛 paintBlocks)
import { blockCells, darken, showValue } from "@/lib/calc";
import { pickColor } from "@/lib/series";
import { useRootScale } from "../chart/scale";
import { cardFormat, cardTarget, cardValue, growFont } from "./__pure__";
import { Frame, Say, type P } from "./common";

const FONT = 11;  // 아래 한 줄 글의 기준 글자 크기(화면 크기 배율 1)
const CELL_ROUND = 0.25, CELL_GAP = 2.5 / 16;  // 51-waffle: 칸 모서리·칸 사이 틈 = 칸 한 변의 1/4·2.5/16(16칸에 rx 4, 틈 2.5). 칸은 그림자 없이 평평하다

export function Blocks({ card, colors, range, merge }: P) {
  const s = useRootScale(), v = cardValue(card, 0, range, merge) ?? 0;
  if (v < 0) return <Say text="값이 음수라 블록으로 그릴 수 없어요" />;
  const target = cardTarget(card), fmt = cardFormat(card), size = card.view?.size || Math.max(+(Math.max(v, target ?? 0) / 20).toPrecision(1), 1e-9);
  const b = blockCells(v, target, size), per = 20, W = 260, cell = W / (per + (per - 1) * CELL_GAP), pitch = cell * (1 + CELL_GAP), color = pickColor(card, "series", card.title, colors, 0), total = b.actual + b.toTarget;
  const says = `${showValue(v, fmt, "", card.view)}${target != null ? ` / 목표 ${showValue(target, fmt, "", card.view)}` : ""} (한 칸 ${showValue(size * b.scale, fmt, "", card.view)})`, k = growFont(says, W, FONT, s) / FONT;  // 글 줄의 자리도 글자만큼 키운다
  const H = Math.ceil(total / per) * pitch + 28 * k;
  return (
    <div className="nm-well h-full">
    <Frame w={W} h={H} label={card.title}>
      {Array.from({ length: total }, (_, i) => (
        <rect key={i} x={(i % per) * pitch} y={Math.floor(i / per) * pitch} width={cell} height={cell} rx={cell * CELL_ROUND}
          fill={i < b.actual - b.over ? color : i < b.actual ? darken(color) : colors.line} />
      ))}
      <text x={0} y={H - 8 * k} fontSize={FONT * k} fill={colors.text}>{says}</text>
    </Frame>
    </div>
  );
}
