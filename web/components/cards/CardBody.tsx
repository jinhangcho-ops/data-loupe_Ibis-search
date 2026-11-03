"use client";
// 카드 몸통: 종류(kind)로 차트 카드(N1)와 SVG/HTML 카드(N2) 중 하나를 고른다. 값은 구한 값이 있으면 그것, 없으면 저장된 값.
import type { Card, ComputeResult, Ctx } from "@/lib/types";
import { CHART_KINDS, ChartCard } from "./ChartCard";
import { SvgCard } from "./SvgCards";

export function CardBody({ card, result, palette, ctx }: { card: Card; result?: ComputeResult; palette: string[]; ctx: Ctx | null }) {
  const props = { card, data: result?.data ?? card.data ?? {}, result, palette, ctx };
  return CHART_KINDS.includes(card.kind) ? <ChartCard {...props} /> : <SvgCard {...props} />;
}

