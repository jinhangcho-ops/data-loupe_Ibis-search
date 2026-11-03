// 카드 그리기 컴포넌트(ChartCard·SvgCard)가 함께 받는 props.
import type { Card, CardData, ComputeResult, Ctx } from "@/lib/types";

export type CardProps = {
  card: Card;
  /** 그릴 값: 실시간·기간으로 구한 값이 있으면 그것, 없으면 저장된 card.data(CardBody가 고른다) */
  data: CardData;
  /** 이번에 구한 결과 그대로(오류 문장 error 포함, 아직 안 구했으면 undefined) */
  result?: ComputeResult;
  /** 대시보드 팔레트(비었으면 화면 모드 색) */
  palette: string[];
  ctx: Ctx | null;
};
