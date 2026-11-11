"use client";
// 차트 섬(/chart, iframe): 쿼리 화면 차트·지표 탭과 분석 화면이 postMessage(lib/island 규약)로 보낸 값을 대시보드와 같은 Recharts 그림(Charts.tsx)으로 그린다.
// 너비는 iframe을 따르고 높이는 너비/비율. 그린 뒤 필요한 높이를 부모에 알리고({type:"size"}), {type:"picture"}를 받으면 그림 파일(report/picture)로 답한다.
// 계열 그림은 대시보드와 같은 차례(그룹 고르기 applyGroups → 라벨 바꾸기 → 자동 규칙 autoSeries)로 그리고, 보이는 값·모든 데이터 값의 표를 부모에 보낸다({type:"shown"}).
import { Component, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { NO_AUTO, THEME_SERIES } from "@/components/cards/ChartCard";
import { AnaChart, PointsChart, SeriesChart } from "@/components/cards/chart/Charts";
import { seriesKit, type Dataset, type SCard, type Series, type SeriesKit, type ThemeColors } from "@/components/cards/chart/series";
import { useTheme } from "@/components/cards/svg/common";
import { Funnel } from "@/components/cards/svg/Funnel";
import { cardPicture, inMode } from "@/components/report/picture";
import { autoParams, autoSeries, scatterTier, seriesTable, type AutoEnv, type ChartTable } from "@/lib/autoviz";
import { ConstantsProvider, useConstants } from "@/lib/constants";
import { applyGroups } from "@/lib/groups";
import { autoRatio, fromParent, relabel, toParent, type Draw, type SeriesData } from "@/lib/island";
import type { Card, CardData, Constants } from "@/lib/types";

const NO_CONSTANTS = { agg_names: {}, merge: [], dash: { types: {}, time_types: [] } } as unknown as Constants;  // 상수를 못 받아도 집계 이름은 id로 그린다
const SHOT_FRAMES = 2;  // 모든 데이터로 다시 그린 뒤 찍기 전에 기다리는 프레임 수(Recharts가 크기를 잰다)
const POINTS_RATIO = 16 / 9;  // 점·분석 그림의 기본 가로/세로

/** 그릴 것: 그림, 안내 문장, 가로/세로(null이면 높이 auto), 계열 그림의 보이는 값·모든 데이터 값 표 */
type Painted = { chart: ReactNode; notes: (string | undefined)[]; ratio: number | null; shown: { table: ChartTable; full: ChartTable } | null };

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

const card = (view: Record<string, unknown>, data: Record<string, unknown> = {}) => ({ kind: "chart", view, data }) as unknown as SCard;

/** 받은 메시지를 그릴 것으로(던지면 Guard가 보인다). all: 모든 데이터로(opts.all 또는 picture all) */
function paint(m: Draw, consts: Constants, kit: SeriesKit, width: number, all: boolean): Painted {
  const colors: ThemeColors = { palette: m.palette?.length ? m.palette : undefined, series: THEME_SERIES };
  const table = consts.dash.auto;  // 상수를 못 받았거나 규칙 표가 없으면 규칙 없이
  const p = table ? autoParams(m.opts?.auto, table) : null;
  const noAuto = table ? [] : [NO_AUTO];
  if (m.kind === "series") {
    const o = m.opts ?? {};
    const view = { type: o.type ?? "", ranges: { y: o.ranges?.y ?? {}, y1: o.ranges?.y1 ?? {} }, scale: o.scale, auto: o.auto, groups: o.groups, all_data: all };
    let picked: SeriesData = m.payload;
    if (o.groups) {  // 그룹 고르기는 원래 이름으로 고른 것이라 라벨 바꾸기 앞
      const shaped = { x_kind: o.x_kind, values: { labels: picked.labels, series: picked.datasets.map(ds => ({ ...ds, name: ds.label })), merged: o.merged } };
      const got = applyGroups(card(view), shaped as unknown as CardData).values as { labels: string[]; series: (Dataset & { name: string })[] };
      picked = { labels: got.labels, datasets: got.series.map(({ name, ...rest }) => ({ ...rest, label: name })) };
    }
    const s: Series = { ...relabel(picked, o.relabel), time: false, chartable: true };
    let c = card(view), shown = s, full = s, notes: string[] = noAuto;
    if (table) {
      const env: AutoEnv = {
        width, table, aggs: o.aggs ?? [], rest: o.rest ?? null, truncated: o.rest != null, xKind: o.x_kind, scale: o.x_scale, levels: o.x_levels,
        order: o.order ?? "x", weekStart: consts.week_start ?? "mon", words: consts.dash.viz.words,
      };
      const a = autoSeries(c, s, env);
      full = autoSeries(card({ ...view, all_data: true }), s, env).s;
      c = a.card; shown = a.s; notes = a.notes;
    }
    return {
      chart: <SeriesChart card={c} s={shown} colors={colors} percent={o.percent} />, notes,
      ratio: o.ratio || (c.view.type === "hbar" ? 1 : autoRatio(shown.labels.length)),
      shown: { table: seriesTable(o.x ?? "", shown), full: seriesTable(o.x ?? "", full) },
    };
  }
  if (m.kind === "points") {
    const o = m.opts ?? {}, pts = m.payload;
    const tier = p ? scatterTier(pts.total ?? pts.n ?? pts.points.length, all ? null : p.density) : null;
    return {
      chart: <PointsChart card={card({ type: o.type ?? "scatter", trend: o.trend, show_r: o.show_r, ranges: o.ranges }, { points: pts })} colors={colors} tier={tier} />,
      notes: [pts.note, ...noAuto], ratio: o.ratio || POINTS_RATIO, shown: null,  // 점을 줄여 그렸으면 서버 문장
    };
  }
  if (m.kind === "ana") {
    if (m.opts.view === "funnel") return { chart: <FunnelBody d={m.payload} />, notes: [], ratio: null, shown: null };
    const d = m.payload;
    const tail: [number, number] | undefined = p && !all && p.tail ? [Number(p.tail.lo), Number(p.tail.hi)] : undefined;
    const tier = p && m.opts.view === "scatter" ? scatterTier(d.n ?? (d.points ?? []).length, all ? null : p.density) : null;
    return {
      chart: <AnaChart d={d} view={m.opts.view} main={colors.palette?.[0] ?? THEME_SERIES[0]} second={colors.palette?.[1] ?? THEME_SERIES[1]} kit={kit} tail={tail} tier={tier} />,
      notes: noAuto, ratio: m.opts.ratio || POINTS_RATIO, shown: null,
    };
  }
  throw new Error(`모르는 그림 종류예요: ${String((m as { kind?: unknown }).kind)}`);
}

function Body({ m, width, shotAll }: { m: Draw; width: number; shotAll: boolean }) {
  const consts = useConstants(), kit = useMemo(() => seriesKit(consts ?? NO_CONSTANTS), [consts]);
  const all = !!m.opts?.all || shotAll;
  const out = useMemo(() => paint(m, consts ?? NO_CONSTANTS, kit, width, all), [m, consts, kit, width, all]);
  // 모든 데이터로 찍는 동안에는 보내지 않는다(부모 표가 흔들리지 않게)
  useEffect(() => { if (out.shown && !shotAll) toParent({ type: "shown", ...out.shown }); }, [out, shotAll]);
  const note = out.notes.filter(Boolean).join(" ");
  return (
    <>
      <div style={{ height: out.ratio === null ? "auto" : Math.round(width / out.ratio) }}>{out.chart}</div>
      {note ? <div className="hint">{note}</div> : null}
    </>
  );
}

function Island() {
  const box = useRef<HTMLDivElement>(null);
  const [drawn, setDrawn] = useState<{ m: Draw; n: number } | null>(null);
  const [width, setWidth] = useState(0);
  const [shotAll, setShotAll] = useState(false);  // picture all: 모든 데이터로 다시 그려 찍는 동안
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
          if (m.all) {
            setShotAll(true);
            for (let i = 0; i < SHOT_FRAMES; i++) await new Promise(r => requestAnimationFrame(r));
          }
          const mode = m.theme ?? document.documentElement.dataset.theme ?? "light";
          toParent({ type: "png", dataUrl: await inMode(mode, () => cardPicture(box.current!, "", mode, m.format ?? "png")) });
        } catch (err) { toParent({ type: "error", message: (err as Error).message }); } finally { if (m.all) setShotAll(false); }
      }
    };
    const ro = new ResizeObserver(([e]) => setWidth(Math.round(e.contentRect.width)));
    ro.observe(box.current!);
    addEventListener("message", onMessage);
    toParent({ type: "ready" });
    return () => { ro.disconnect(); removeEventListener("message", onMessage); };
  }, []);
  const m = drawn?.m;
  // 그린 뒤(Recharts가 크기를 잰 다음 프레임) 필요한 높이를 알린다. 모든 데이터로 찍는 동안에는 알리지 않는다
  useEffect(() => {
    if (!drawn || !width || shotAll) return;
    let live = true;
    const size = () => { if (live && box.current) toParent({ type: "size", height: Math.ceil(box.current.getBoundingClientRect().height) }); };
    const id = requestAnimationFrame(() => requestAnimationFrame(size));
    document.fonts?.ready.then(() => requestAnimationFrame(size));  // 글꼴이 늦게 오면 글줄 높이가 바뀐다: 받은 뒤 한 번 더
    return () => { live = false; cancelAnimationFrame(id); };
  }, [drawn, width, shotAll]);
  return (
    <div ref={box} style={{ width: "100%" }}>
      {m && width ? <Guard key={drawn!.n}><Body m={m} width={width} shotAll={shotAll} /></Guard> : null}
    </div>
  );
}

export default function ChartPage() {
  return <ConstantsProvider><Island /></ConstantsProvider>;
}
