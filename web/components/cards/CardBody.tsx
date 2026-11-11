"use client";
// 카드 몸통: 종류(kind)로 차트 카드(N1)와 SVG/HTML 카드(N2) 중 하나를 고른다. 값은 구한 값이 있으면 그것, 없으면 저장된 값.
import { memo } from "react";
import type { Card, ComputeResult, Ctx } from "@/lib/types";
import { applyGroups, renameColors } from "@/lib/groups";
import { CHART_KINDS, ChartCard, Guard } from "./ChartCard";
import { SvgCard } from "./SvgCards";

type BodyProps = { card: Card; result?: ComputeResult; palette: string[]; ctx: Ctx | null };

// 표시할 그룹 고르기·이름 바꾸기를 입힌 값으로 그린다. applyGroups가 던지면(MERGE_DATE) 바깥 Guard가 보인다.
function Shown({ card, result, palette, ctx }: BodyProps) {
  const raw = result?.data ?? card.data ?? {};
  const data = applyGroups(card, raw);
  const colors = renameColors(card.colors, card.view.groups);
  const shown = data === raw && colors === card.colors ? card : { ...card, data, colors };
  const props = { card: shown, data, result, palette, ctx };
  return CHART_KINDS.includes(card.kind) ? <ChartCard {...props} /> : <SvgCard {...props} />;
}

// memo: 한 카드를 고치거나 고를 때 다른 카드는 다시 그리지 않는다(props가 같으면)
export const CardBody = memo(function CardBody(props: BodyProps) {
  const { card, result } = props;
  return <Guard key={JSON.stringify(card.view) + (result?.data?.computed_at ?? card.data?.computed_at ?? "")}><Shown {...props} /></Guard>;
});

