"use client";
// histogram(P14): 서버가 구한 구간(data.bins = {edges, counts}, 구간 수는 view.bins ≤ 100)을 분석 그림 hist(Charts.AnaChart)로 그린다
import { useMemo } from "react";
import { AnaChart } from "../chart/Charts";
import { seriesKit } from "../chart/series";
import { useConstants } from "@/lib/constants";
import { autoParams } from "@/lib/autoviz";
import { NOTHING, Say, tone, type P } from "./common";

export function Histogram(p: P) {
  const consts = useConstants(), kit = useMemo(() => consts && seriesKit(consts), [consts]), b = p.card.data.bins;
  if (!consts || !kit) return null;
  if (!b?.counts?.length || b.edges?.length !== b.counts.length + 1) return <Say text={NOTHING} />;
  const t = autoParams(p.card.view.auto, consts.dash.auto).tail;
  return <AnaChart d={{ histogram: b, column: p.card.source?.body?.series?.[0]?.y }} view="hist" main={tone(p, "주 색", 0)} second={tone(p, "보조 색", 1)} kit={kit} tail={t ? [Number(t.lo), Number(t.hi)] : undefined} />;
}
