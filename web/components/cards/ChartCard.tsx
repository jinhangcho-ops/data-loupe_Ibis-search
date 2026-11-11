"use client";
// 차트 카드(chart·analysis·metric)의 몸통: 옛 board.js cardBox + dash.js chartConfig를 Recharts로(그림은 chart/Charts.tsx, 값 → 모양은 chart/series.ts).
// 분석 카드가 종류를 정하지 않았으면 분석 그림(AnaChart), 산점도·버블은 서버가 구한 점(data.points), 나머지는 계열 차트. 값이 없으면 옛 안내 문장.
// 색: 카드 지정 색 → 대시보드 팔레트(비었으면 없음) → 화면 모드 계열 색(var(--c1)…, 어두운 모드에선 다른 색).
import { Component, useMemo, type ReactNode } from "react";
import { anaView, isPoints, shownum } from "@/lib/calc";
import { useConstants } from "@/lib/constants";
import type { CardProps } from "./types";
import { AnaChart, PointsChart, SeriesChart, type Ana, type Points } from "./chart/Charts";
import { pickColor } from "@/lib/series";
import { seriesKit, type SCard, type Series, type SeriesKit, type ThemeColors } from "./chart/series";
import { useZoomX } from "./useZoomX";
import { useWidth } from "./useWidth";
import { Box } from "./svg/common";
import { zoomAxis } from "./svg/__pure__";
import { autoEnv, autoParams, autoSeries, autoUnit, scatterTier, seriesTable, type ChartTable, type Tier } from "@/lib/autoviz";
import type { Constants, Unit } from "@/lib/types";

/** 이 컴포넌트가 맡는 카드 종류 */
export const CHART_KINDS: string[] = ["chart", "analysis", "metric"];
/** 화면 모드의 계열 색(common.css --c1~--c8, 테마 따라 바뀐다) */
export const THEME_SERIES = [1, 2, 3, 4, 5, 6, 7, 8].map(i => `var(--c${i})`);
/** 값이 아직 없는 카드의 안내(옛 dash.js needsValues) */
export const NO_VALUES = "아직 구한 값이 없어요. 연결한 뒤 [다시 구하기]를 누르세요.";
/** 상수는 받았지만 자동 규칙 표(dash.auto)가 없을 때의 안내 */
export const NO_AUTO = "자동 규칙 설정을 받지 못해 규칙 없이 그렸어요.";
/** [표로 보기]를 켰지만 표로 만들 수 없는 그림의 안내 */
export const NO_TABLE = "이 그림은 표로 볼 수 없어요.";
/** 데이터 표 열 이름: 값에 X·Y 이름이 없을 때, 산점도의 크기·그룹 열 */
export const X_NAME = "X", Y_NAME = "Y", SIZE_NAME = "크기", GROUP_NAME = "그룹";
/** 분석 히스토그램 데이터 표의 열(AnaChart hist의 막대 이름·값 이름과 같다) */
export const HIST_COLUMNS = ["구간", "행 수"];
/** 날짜 X축 종류 중 막대만 그리는 것(자동 묶기 판정, 서버 TIME_TYPES 중 선이 없는 것) */
export const ROLLUP_BARS = ["bar", "stacked", "waterfall"];
/** 그림 : 데이터 표의 높이 몫(표는 1/3) */
export const CHART_FLEX = 2, TABLE_FLEX = 1;

/** autoDraw 결과: 그릴 카드·계열, 안내 문장, 데이터 표, 산점도 점 단계, 히스토그램 꼬리 이름 */
export type Drawn = { c: SCard; s: Series | null; notes: string[]; table: ChartTable | null; tier: Tier | null; tail: [number, number] | undefined };

/** [SPECIFICATION] plans/phase-9.md#autoDraw
 *  자동 묶기 → autoSeries(또는 점·분석의 tier·tail) → 데이터 표까지 한 번에(ChartCard와 Report CSV가 같이 쓴다). */
export function autoDraw(card: SCard, kit: SeriesKit, consts: Constants, width: number, all: boolean): Drawn {
  let v: SCard = all ? { ...card, view: { ...card.view, all_data: true } } : card;
  const full = v.view.all_data === true;
  let notes: string[] = [];
  let c: SCard, s: Series | null, tier: Tier | null, tail: [number, number] | undefined;
  if (consts.dash.auto == null) {
    c = v; s = kit.cardSeries(v); notes = [NO_AUTO]; tier = null; tail = undefined;
    if (!s) return { c, s, notes, table: null, tier, tail };
  } else {
    const p = autoParams(v.view.auto, consts.dash.auto);
    const by = v.data?.by_unit;
    const day = by?.day ? kit.cardSeries(v) : null;
    const type = v.view.type || "";
    if (by?.day && day && p.resample && (v.view.unit || "day") === "day" && !full && width > 0
      && (p.resample.mode === "rollup" || (type === "" ? day.datasets.every(d => d.type !== "line") : ROLLUP_BARS.includes(type)))) {
      const units = Object.keys(consts.dash.units) as Unit[];
      const counts = Object.fromEntries(units.map(u => [u, kit.byUnit(by as Parameters<SeriesKit["byUnit"]>[0], u, v.range).labels.length]));
      const unit = autoUnit(counts, width, p.resample.px as number);
      if (unit !== "day") {
        v = { ...v, view: { ...v.view, unit } };
        notes.push(`점이 많아 ${consts.dash.units[unit]} 단위로 묶어 그렸어요.`);
      }
    }
    const s0 = kit.cardSeries(v);
    if (!s0) return { c: v, s: null, notes, table: null, tier: null, tail: undefined };
    if (kit.usesAna(v)) {
      c = v; s = s0;
      tail = p.tail ? [p.tail.lo as number, p.tail.hi as number] : undefined;
      if (anaView(v) === "scatter") {
        const d = v.data.values as Ana;
        tier = scatterTier(d.n ?? (d.points ?? []).length, full ? null : p.density);
      } else tier = null;
    } else if (isPoints(v)) {
      c = v; s = s0;
      const pts = v.data.points as unknown as Points;
      tier = scatterTier(pts.total ?? pts.n ?? pts.points.length, full ? null : p.density);
      tail = undefined;
    } else {
      const env = autoEnv(v, width, consts.dash.auto, consts.week_start ?? "mon", consts.dash.viz.words);
      const a = autoSeries(v, s0, env);
      c = a.card; s = a.s; notes.push(...a.notes); tier = null; tail = undefined;
    }
  }
  let table: ChartTable | null;
  if (kit.usesAna(c)) {
    const d = c.data.values as Ana, view = anaView(c);
    if (view === "hist") {
      const e = d.histogram!.edges, { under = 0, over = 0 } = d.histogram!;
      const rows: Record<string, unknown>[] = d.histogram!.counts.map((n, i) => ({ [HIST_COLUMNS[0]]: `${shownum(e[i])}–${shownum(e[i + 1])}`, [HIST_COLUMNS[1]]: n }));
      if (tail && under > 0) rows.unshift({ [HIST_COLUMNS[0]]: `P${tail[0] * 100} 미만(${under}건)`, [HIST_COLUMNS[1]]: under });
      if (tail && over > 0) rows.push({ [HIST_COLUMNS[0]]: `P${tail[1] * 100} 초과(${over}건)`, [HIST_COLUMNS[1]]: over });
      table = { columns: HIST_COLUMNS, rows } as ChartTable;
    } else if (view === "scatter") {
      const columns = [d.x ?? X_NAME, d.y ?? Y_NAME];
      table = { columns, rows: (d.points ?? []).map(([x, y]) => ({ [columns[0]]: x, [columns[1]]: y })) } as ChartTable;
    } else table = s.chartable && s.datasets.length ? seriesTable(c.data.x ?? X_NAME, s) : null;
  } else if (isPoints(c)) {
    const pts = c.data.points as unknown as Points;
    const size = pts.points.some(r => r.length - (pts.groups.length ? 1 : 0) > 2);
    const columns = [pts.x, pts.y, ...(size ? [c.view.points?.size ?? SIZE_NAME] : []), ...(pts.groups.length ? [GROUP_NAME] : [])];
    table = { columns, rows: pts.points.map(r => Object.fromEntries(columns.map((k, i) => [k, i === columns.length - 1 && pts.groups.length ? r[r.length - 1] : r[i]]))) } as ChartTable;
  } else table = seriesTable(c.data.x ?? X_NAME, s);
  return { c, s, notes, table, tier, tail };
}

/** 카드 하나가 그리다 던져도 나머지 카드는 그린다(옛 Board.errorBox와 같은 문장) */
export class Guard extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    return this.state.error ? <div className="hint bnote err">{`이 카드를 그리지 못했어요: ${this.state.error.message}`}</div> : this.props.children;
  }
}

export function ChartCard({ card, data, palette, ctx }: CardProps) {
  const consts = useConstants();
  const kit = useMemo(() => consts && seriesKit(consts), [consts]);
  const { ref, width } = useWidth();
  // 카드 상자와 .bbody(격자 칸 높이)는 Grid가 준다. 여기는 그 높이를 그림과 안내 줄로 나눈다.
  const box = (body: ReactNode, note?: string, table?: ReactNode) => (
    <div ref={ref} style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ flex: CHART_FLEX, minHeight: 0 }}>{body}</div>
      {table ? <div style={{ flex: TABLE_FLEX, minHeight: 0, overflow: "auto" }}>{table}</div> : null}
      {note ? <div className="hint">{note}</div> : null}
    </div>
  );
  const c0: SCard = { ...card, data, range: ctx?.range };
  const d = kit && consts ? autoDraw(c0, kit, consts, width, false) : null;
  const c = d ? d.c : c0, s = d ? d.s : null;
  const ana = !!kit && !!s && kit.usesAna(c);
  const z = useZoomX(kit ? zoomAxis(c, s, ana) : null);  // 훅이라 early return 앞에서 부른다
  if (!d) return box(null);
  if (!s) return box(<div className="hint bnote">{NO_VALUES}</div>);
  const colors: ThemeColors = { palette: palette.length ? palette : undefined, series: THEME_SERIES };
  let body: ReactNode;
  if (ana) {
    const main = pickColor(c, "series", "주 색", colors, 0), second = pickColor(c, "series", "보조 색", colors, 1);
    body = <AnaChart d={data.values as never} view={anaView(c)} main={main} second={second} kit={kit!} win={z.view} tail={d.tail} tier={d.tier} />;
  } else if (isPoints(c)) body = <PointsChart card={c} colors={colors} xWin={z.view} tier={d.tier} />;
  else body = <SeriesChart card={c} s={s} colors={colors} win={z.view} />;
  // 점이 max_points를 넘어 줄여 그렸으면 서버 문장, 그 뒤에 자동 규칙 문장
  const note = [isPoints(c) ? (data.points as { note?: string }).note : undefined, ...d.notes].filter(Boolean).join(" ");
  const table = card.view.data_table ? (d.table ? (
    <Box cls="table"><table>
      <thead><tr>{d.table.columns.map(k => <th key={k}>{k}</th>)}</tr></thead>
      <tbody>{d.table.rows.map((r, i) => <tr key={i}>{d.table!.columns.map(k => <td key={k} className={typeof r[k] === "number" ? "num" : undefined}>{r[k] == null ? "" : typeof r[k] === "number" ? shownum(r[k]) : String(r[k])}</td>)}</tr>)}</tbody>
    </table></Box>
  ) : <div className="hint">{NO_TABLE}</div>) : null;
  return box(<div {...z.bind}><Guard key={JSON.stringify(card.view) + (data.computed_at ?? "")}>{body}</Guard></div>, note || undefined, table);
}
