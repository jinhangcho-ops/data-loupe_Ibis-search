"use client";
// N2 — 차트가 아닌 카드(SVG/HTML)를 그린다: kind → 종류별 컴포넌트(cards/svg/*) 표(옛 static/dashboard/dash_cards.js의 PAINTERS·paintCard).
// 계산은 lib/calc.ts와 svg/__pure__.ts만 쓴다. 색은 화면 모드의 CSS 변수(svg/common.useTheme) + 대시보드 팔레트, 집계 묶기는 상수 merge.
// geomap은 React.lazy로 처음 그릴 때만 조각을 받는다(못 받으면 옛 문장).
import { Component, Suspense, lazy, useMemo, type ComponentType, type ReactNode } from "react";
import { useConstants } from "@/lib/constants";
import type { CardProps } from "./types";
import { RawValue } from "./RawValue";
import { Blocks } from "./svg/Blocks";
import { Box, Say, useTheme, type P } from "./svg/common";
import { Deviation } from "./svg/Deviation";
import { Donut } from "./svg/Donut";
import { DualTrend } from "./svg/DualTrend";
import { Funnel } from "./svg/Funnel";
import { Gauge } from "./svg/Gauge";
import { Kpi } from "./svg/Kpi";
import { MapCard } from "./svg/MapCard";
import { RecentRows, Table } from "./svg/Rows";
import { Sentence } from "./svg/Sentence";
import { Alerts, Status } from "./svg/Status";
import { Stripes } from "./svg/Stripes";
import { Summary } from "./svg/Summary";
import { TargetTrend } from "./svg/TargetTrend";

const GeoMap = lazy(() => import("./svg/GeoMap"));

// 지도 조각을 못 받았을 때(오프라인·새 빌드 뒤 옛 조각) 옛 화면과 같은 안내
class GeoFailed extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <Say text="지도를 불러오지 못했어요. 화면을 새로 고쳐 보세요" /> : this.props.children; }
}
const Geo = (p: P) => (
  <GeoFailed><Suspense fallback={<Say text="지도를 불러오는 중…" />}><GeoMap {...p} /></Suspense></GeoFailed>
);

/** 이 컴포넌트가 그리는 카드 종류 → 컴포넌트(옛 SVG_KINDS) */
export const PAINTERS: Record<string, ComponentType<P>> = {
  kpi: Kpi, donut_kpi: Donut, gauge: Gauge, summary: Summary, recent_rows: RecentRows, dual_trend: DualTrend, stripes: Stripes, blocks: Blocks, target_trend: TargetTrend,
  table: Table, funnel: Funnel, status: Status, sentence: Sentence, alerts: Alerts, map: MapCard, deviation: Deviation, geomap: Geo,
};

export function SvgCard({ card, data, result, palette, ctx }: CardProps) {
  const theme = useTheme(), merge = useConstants()?.merge;
  const colors = useMemo(() => ({ ...theme, palette }), [theme, palette]);
  const Paint = PAINTERS[card.kind];
  if (!Paint) return <RawValue data={data} result={result} />;
  if (!(data.by_unit || data.values || data.columns)) return <Box cls="summary"><div className="sum-text">아직 구한 값이 없어요</div></Box>;
  return <Paint card={{ ...card, data }} colors={colors} range={ctx?.range} merge={merge ?? []} />;
}
