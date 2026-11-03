"use client";
// 차트 카드(chart·analysis·metric)의 몸통: 옛 board.js cardBox + dash.js chartConfig를 Recharts로(그림은 chart/Charts.tsx, 값 → 모양은 chart/series.ts).
// 분석 카드가 종류를 정하지 않았으면 분석 그림(AnaChart), 산점도·버블은 서버가 구한 점(data.points), 나머지는 계열 차트. 값이 없으면 옛 안내 문장.
// 색: 카드 지정 색 → 대시보드 팔레트(비었으면 없음) → 화면 모드 계열 색(var(--c1)…, 어두운 모드에선 다른 색).
import { Component, useMemo, type ReactNode } from "react";
import { anaView, isPoints } from "@/lib/calc";
import { useConstants } from "@/lib/constants";
import type { CardProps } from "./types";
import { AnaChart, PointsChart, SeriesChart } from "./chart/Charts";
import { pickColor } from "@/lib/series";
import { seriesKit, type SCard, type ThemeColors } from "./chart/series";

/** 이 컴포넌트가 맡는 카드 종류 */
export const CHART_KINDS: string[] = ["chart", "analysis", "metric"];
/** 화면 모드의 계열 색(common.css --c1~--c8, 테마 따라 바뀐다) */
export const THEME_SERIES = [1, 2, 3, 4, 5, 6, 7, 8].map(i => `var(--c${i})`);
/** 값이 아직 없는 카드의 안내(옛 dash.js needsValues) */
export const NO_VALUES = "아직 구한 값이 없어요. 연결한 뒤 [다시 구하기]를 누르세요.";

/** 카드 하나가 그리다 던져도 나머지 카드는 그린다(옛 Board.errorBox와 같은 문장) */
class Guard extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    return this.state.error ? <div className="hint bnote err">{`이 카드를 그리지 못했어요: ${this.state.error.message}`}</div> : this.props.children;
  }
}

export function ChartCard({ card, data, palette, ctx }: CardProps) {
  const consts = useConstants();
  const kit = useMemo(() => consts && seriesKit(consts), [consts]);
  // 카드 상자와 .bbody(격자 칸 높이)는 Grid가 준다. 여기는 그 높이를 그림과 안내 줄로 나눈다.
  const box = (body: ReactNode, note?: string) => (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ flex: 1, minHeight: 0 }}>{body}</div>
      {note ? <div className="hint">{note}</div> : null}
    </div>
  );
  if (!kit) return box(null);
  const c: SCard = { ...card, data, range: ctx?.range };
  const s = kit.cardSeries(c);
  if (!s) return box(<div className="hint bnote">{NO_VALUES}</div>);
  const colors: ThemeColors = { palette: palette.length ? palette : undefined, series: THEME_SERIES };
  let body: ReactNode;
  if (kit.usesAna(c)) {
    const main = pickColor(c, "series", "주 색", colors, 0), second = pickColor(c, "series", "보조 색", colors, 1);
    body = <AnaChart d={data.values as never} view={anaView(c)} main={main} second={second} kit={kit} />;
  } else if (isPoints(c)) body = <PointsChart card={c} colors={colors} />;
  else body = <SeriesChart card={c} s={s} colors={colors} />;
  const note = isPoints(c) ? (data.points as { note?: string }).note : undefined;  // 점이 max_points를 넘어 줄여 그렸으면 서버 문장
  return box(<Guard key={JSON.stringify(card.view) + (data.computed_at ?? "")}>{body}</Guard>, note);
}
