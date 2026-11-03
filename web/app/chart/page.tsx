"use client";
// 차트 섬(/chart, iframe): 쿼리 화면 차트·지표 탭과 분석 화면이 postMessage(lib/island 규약)로 보낸 값을 대시보드와 같은 Recharts 그림(Charts.tsx)으로 그린다.
// 너비는 iframe을 따르고 높이는 너비/비율. 그린 뒤 필요한 높이를 부모에 알리고({type:"size"}), {type:"picture"}를 받으면 그림 파일(report/picture)로 답한다.
import { Component, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { THEME_SERIES } from "@/components/cards/ChartCard";
import { AnaChart, PointsChart, SeriesChart } from "@/components/cards/chart/Charts";
import { seriesKit, type SCard, type ThemeColors } from "@/components/cards/chart/series";
import { useTheme } from "@/components/cards/svg/common";
import { Funnel } from "@/components/cards/svg/Funnel";
import { cardPicture, inMode } from "@/components/report/picture";
import { ConstantsProvider, useConstants } from "@/lib/constants";
import { autoRatio, fromParent, relabel, toParent, type Draw } from "@/lib/island";
import type { Card, Constants } from "@/lib/types";

const NO_CONSTANTS = { agg_names: {}, merge: [], dash: { types: {}, time_types: [] } } as unknown as Constants;  // 상수를 못 받아도 집계 이름은 id로 그린다

/** 그리다 던지면 부모에 이유를 알린다(같은 문장을 섬에도 보인다) */
class Guard extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error) { toParent({ type: "error", message: `차트를 그리지 못했어요: ${error.message}` }); }
  render() { return this.state.error ? <div className="hint err">{`차트를 그리지 못했어요: ${this.state.error.message}`}</div> : this.props.children; }
}

function FunnelBody({ d }: { d: unknown }) {
  return <Funnel card={{ data: { values: d } } as unknown as Card} colors={useTheme()} range={undefined} merge={[]} />;
}

function Body({ m }: { m: Draw }) {
  const consts = useConstants(), kit = useMemo(() => seriesKit(consts ?? NO_CONSTANTS), [consts]);
  const colors: ThemeColors = { palette: m.palette?.length ? m.palette : undefined, series: THEME_SERIES };
  const card = (view: Record<string, unknown>, data: Record<string, unknown> = {}) => ({ kind: "chart", view, data }) as unknown as SCard;
  if (m.kind === "series") {
    const o = m.opts ?? {}, d = relabel(m.payload, o.relabel);
    return <SeriesChart card={card({ type: o.type ?? "", ranges: { y: o.ranges?.y ?? {}, y1: o.ranges?.y1 ?? {} } })} s={{ ...d, time: false, chartable: true }} colors={colors} percent={o.percent} />;
  }
  if (m.kind === "points") {
    const o = m.opts ?? {};
    return <PointsChart card={card({ type: o.type ?? "scatter", trend: o.trend, show_r: o.show_r, ranges: o.ranges }, { points: m.payload })} colors={colors} />;
  }
  if (m.kind === "ana") {
    if (m.opts.view === "funnel") return <FunnelBody d={m.payload} />;
    return <AnaChart d={m.payload} view={m.opts.view} main={colors.palette?.[0] ?? THEME_SERIES[0]} second={colors.palette?.[1] ?? THEME_SERIES[1]} kit={kit} />;
  }
  throw new Error(`모르는 그림 종류예요: ${String((m as { kind?: unknown }).kind)}`);
}

function Island() {
  const box = useRef<HTMLDivElement>(null);
  const [drawn, setDrawn] = useState<{ m: Draw; n: number } | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    // .app(100vh)을 풀어 그림 높이만큼. 스크롤 막대는 숨긴다(높이는 부모가 size로 맞추고, 막대가 생겼다 사라지며 너비가 바뀌면 size가 계속 오간다)
    document.documentElement.style.overflow = "hidden";
    Object.assign(document.body.style, { height: "auto", overflow: "hidden", background: "transparent" });
    const onMessage = async (e: MessageEvent) => {
      const m = fromParent(e);
      if (!m) return;
      if (m.type === "draw") {
        if (m.theme) document.documentElement.dataset.theme = m.theme;
        setDrawn(d => ({ m, n: (d?.n ?? 0) + 1 }));
      } else if (m.type === "picture") {
        try {
          if (!box.current?.querySelector("svg")) throw new Error("먼저 차트를 그리세요.");
          const mode = m.theme ?? document.documentElement.dataset.theme ?? "light";
          toParent({ type: "png", dataUrl: await inMode(mode, () => cardPicture(box.current!, "", mode, m.format ?? "png")) });
        } catch (err) { toParent({ type: "error", message: (err as Error).message }); }
      }
    };
    const ro = new ResizeObserver(([e]) => setWidth(Math.round(e.contentRect.width)));
    ro.observe(box.current!);
    addEventListener("message", onMessage);
    toParent({ type: "ready" });
    return () => { ro.disconnect(); removeEventListener("message", onMessage); };
  }, []);
  const m = drawn?.m, groups = m?.kind === "series" ? m.payload.labels.length : 25;
  const ratio = m?.opts?.ratio || (m?.kind === "series" ? autoRatio(groups) : 16 / 9);
  const note = m?.kind === "points" ? m.payload.note : undefined;  // 점을 줄여 그렸으면 서버 문장
  // 그린 뒤(Recharts가 크기를 잰 다음 프레임) 필요한 높이를 알린다
  useEffect(() => {
    if (!drawn || !width) return;
    let live = true;
    const size = () => { if (live && box.current) toParent({ type: "size", height: Math.ceil(box.current.getBoundingClientRect().height) }); };
    const id = requestAnimationFrame(() => requestAnimationFrame(size));
    document.fonts?.ready.then(() => requestAnimationFrame(size));  // 글꼴이 늦게 오면 글줄 높이가 바뀐다: 받은 뒤 한 번 더
    return () => { live = false; cancelAnimationFrame(id); };
  }, [drawn, width]);
  return (
    <div ref={box} style={{ width: "100%" }}>
      {m && width ? (
        <Guard key={drawn!.n}>
          <div style={{ height: m.kind === "ana" && m.opts.view === "funnel" ? "auto" : Math.round(width / ratio) }}><Body m={m} /></div>
          {note ? <div className="hint">{note}</div> : null}
        </Guard>
      ) : null}
    </div>
  );
}

export default function ChartPage() {
  return <ConstantsProvider><Island /></ConstantsProvider>;
}
