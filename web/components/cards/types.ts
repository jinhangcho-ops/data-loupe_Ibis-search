// 카드 그리기 컴포넌트(ChartCard·SvgCard)가 함께 받는 props.
import type { Card, CardData, ComputeResult, Ctx, MarkValue, PickedFilter } from "@/lib/types";

export type CardProps = {
  card: Card;
  /** 그릴 값: 실시간·기간으로 구한 값이 있으면 그것, 없으면 저장된 card.data(CardBody가 고른다) */
  data: CardData;
  /** 이번에 구한 결과 그대로(오류 문장 error 포함, 아직 안 구했으면 undefined) */
  result?: ComputeResult;
  /** 대시보드 팔레트(비었으면 화면 모드 색) */
  palette: string[];
  ctx: Ctx | null;
  /** 누르면 거르기(view.cross): 막대·조각을 누르면 (컬럼, 값)으로 부른다. 없으면 누르기를 받지 않는다(만드는 곳, 연결 없음, 크게 보기) */
  onPick?: (column: string, value: string) => void;
  /** (B3) 이 카드가 보낸 값 필터(고른 줄들, 없으면 undefined) */
  pressed?: PickedFilter;
  /** (B3) 목록 줄 고르기: 거를 컬럼과 새로 고른 값들(빈 목록 = 풂). 없으면 고르기를 받지 않는다 */
  onPress?: (column: string, values: string[]) => void;
  /** (B9) 목록 줄 표시 바꾸기: 줄 열쇠와 바꾼 칸(칸 id → 값). 없으면 표시 입력을 그리지 않는다 */ onMark?: (key: string, values: Record<string, MarkValue>) => void;
  /** 값 대신 비율(%)로 그린다(카드 머리의 % 단추, 계열 차트만. 저장하지 않는다) */
  share?: boolean;
};
