"use client";
// 차트 카드의 그림(Recharts 3): 옛 dash.js chartConfig(11종 + 계열마다 정한 막대·선 겹치기)·pointsConfig(산점도·버블)·anaConfig(분석 그림 7종)를 옮겼고, P14 새 계열 종류 17개를 더했다.
// 색은 토큰 그대로(var(--c1)… · --muted · --line · --text · --card)라 화면 모드가 바뀌면 따라 바뀐다. 투명도는 alpha() 대신 fill-opacity로(변수 색에도 된다).
// 공통 모양은 옛 common.js chartLook: 12px 글자, 값 축 가로 격자만, 축 선 없음, 위쪽만 둥근 막대(4px, 최대 44px), 범례는 위 왼쪽 원 표시, 1만 이상 눈금은 짧게(1.2만).
// 애니메이션은 끈다(사진 비교·다시 그릴 때 움직이지 않게).
import type { ReactNode } from "react";
import {
  Area, Bar, CartesianGrid, Cell, ComposedChart, Line, Pie, PieChart, PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, RadialBar, RadialBarChart, Sector,
  Customized, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, usePlotArea, useXAxisScale, useYAxisScale, type PieSectorShapeProps,
} from "recharts";
import { decimals, pointGroups, shownum } from "@/lib/calc";
import { pickColor } from "@/lib/series";
import { UNIT_NAMES, type SCard, type Series, type SeriesKit, type ThemeColors } from "./series";
import { FIRST_ONLY, ROUND_TYPES, fitLine, paretoRows, ringRadii, waterfallRows } from "./shapes";

const compact = new Intl.NumberFormat("ko", { notation: "compact", maximumFractionDigits: 1 }), sig3 = new Intl.NumberFormat(undefined, { maximumSignificantDigits: 3 });
/** 값 축 눈금: 1만 미만은 쉼표, 넘으면 1.2만처럼 */
/** 축 눈금(Recharts가 고른 깔끔한 값): 1만 이상은 짧게, 아주 작은 값(1천분의 1 미만)은 유효숫자 3, 나머지는 소수 넷째 자리까지 그대로(좁은 범위 눈금이 같은 글자로 겹치지 않게).
 *  두 번째 인자(Recharts가 주는 눈금 번호)는 쓰지 않는다 */
export const tickNum = (v: unknown) => typeof v !== "number" ? shownum(v) : Math.abs(v) >= 10000 ? compact.format(v) : v !== 0 && Math.abs(v) < 1e-3 ? sig3.format(v) : shownum(v);
/** 손수 그린 눈금(간격 step, 예: 최소~최대의 3눈금)의 글자: 간격에 맞는 소수 자릿수(2,995.3815 → 2,995). 1만 이상·아주 작은 값은 tickNum */
export const tickBy = (step: number) => (v: number) => !(step > 0) || Math.abs(v) >= 10000 || (v !== 0 && Math.abs(v) < 1e-3) ? tickNum(v) : decimals(Math.max(0, -Math.floor(Math.log10(step)))).format(v);
const TICK = { fill: "var(--muted)", fontSize: 12 };
export const AXIS = { tick: TICK, axisLine: false, tickLine: false } as const;
export const TIP = { contentStyle: { background: "var(--card)", border: "1px solid var(--line)", borderRadius: 8, color: "var(--text)", fontSize: 12 }, labelStyle: { fontWeight: 600 },
  isAnimationActive: false, formatter: (v: unknown) => shownum(v) } as const;
const xTitle = (text?: string) => text ? { value: text, position: "insideBottom" as const, offset: -4, fill: "var(--muted)", fontSize: 12 } : undefined;
const yTitle = (text?: string) => text ? { value: text, angle: -90, position: "insideLeft" as const, fill: "var(--muted)", fontSize: 12, style: { textAnchor: "middle" as const } } : undefined;
export const MARGIN = { top: 4, right: 8, bottom: 4, left: 0 };
const TITLED = { top: 4, right: 8, bottom: 16, left: 8 };  // 축 제목이 있는 그림

export type Key = { name: string; color: string; opacity?: number };
/** 범례: 넣은 순서 그대로(Recharts 기본은 이름순이라 쓰지 않는다), 원 표시 8px */
export function Keys({ items }: { items: Key[] }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "2px 14px", fontSize: 12, color: "var(--muted)", padding: "0 0 4px" }}>
      {items.map((k, i) => (
        <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <i style={{ width: 8, height: 8, borderRadius: "50%", background: k.color, opacity: k.opacity, flex: "none" }} />{k.name}
        </span>
      ))}
    </div>
  );
}

/** 범례·제목 줄 + 남은 높이를 채우는 그림(카드 크기를 따라간다) */
export function Frame({ keys, title, children }: { keys?: Key[]; title?: string; children: ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", fontSize: 12 }}>
      {title ? <div style={{ textAlign: "center", color: "var(--muted)", fontWeight: 600, padding: "0 0 4px" }}>{title}</div> : null}
      {keys?.length ? <Keys items={keys} /> : null}
      <div style={{ flex: 1, minHeight: 0 }}>
        <ResponsiveContainer width="100%" height="100%" minHeight={60}>{children as React.ReactElement}</ResponsiveContainer>
      </div>
    </div>
  );
}

const domainOf = (r?: { min?: number; max?: number }): [number | "auto", number | "auto"] => [r?.min ?? 0, r?.max ?? "auto"];
const ranged = (r?: { min?: number; max?: number }) => r?.min != null || r?.max != null;

const HBARS = ["hbar", "hstack", "hstack100", "butterfly"], STACKS = ["stacked", "stack100", "hstack", "hstack100", "sarea", "sarea100", "butterfly"], FULL = ["stack100", "hstack100", "sarea100"];
const AREAS = ["area", "sarea", "sarea100"], LINES = ["line", "polyline", "step", "slope", ...AREAS];
const pct100 = (v: unknown) => `${Math.round(Number(v) * 100)}%`;
const absTick = (v: unknown) => tickNum(Math.abs(Number(v)));
type Row = Record<string, string | number | null>;

/** 계열 차트(chartConfig): 막대·가로 막대·누적 막대·라인·꺾인 선·영역형·레이더, 종류가 비면 계열마다 정한 대로(막대·선 겹치기, 점선, 오른쪽 축).
 *  P14 새 종류: 묶은 막대(group), 100%·가로 누적(stack100·hstack·hstack100, stackOffset expand), 누적 영역(sarea·sarea100), 계단 선(step), 막대+선(combo, 둘째 계열부터 오른쪽 축 선),
 *  범위 막대(range, 두 계열 [첫째, 둘째]), 기울기(slope, 처음·끝 두 시점), 나비(butterfly, 첫 계열을 왼쪽 음수 방향). 도넛·파이·방사형·장미는 RoundChart, 폭포·파레토·추세선·롤리팝은 OneSeries.
 *  percent면 왼쪽 값 축 눈금에 %(옛 쿼리 화면 tuneChart, 비율 지표) */
export function SeriesChart({ card, s, colors, percent }: { card: SCard; s: Series; colors: ThemeColors; percent?: boolean }) {
  const view = card.view ?? {}, type = view.type || "", ranges = view.ranges ?? { y: {}, y1: {} };
  if (ROUND_TYPES.includes(type)) return <RoundChart card={card} s={s} colors={colors} />;
  if (FIRST_ONLY.includes(type)) return <OneSeries card={card} s={s} colors={colors} />;
  const hbar = HBARS.includes(type), stacked = STACKS.includes(type), full = FULL.includes(type), butterfly = type === "butterfly", combo = type === "combo", range = type === "range" && s.datasets.length > 1;
  const shown = butterfly || range ? s.datasets.slice(0, 2) : s.datasets;
  const right = combo ? shown.length > 1 : !type && s.datasets.some(d => d.axis === "right");
  const all: Row[] = s.labels.map((label, j) => Object.fromEntries([["label", label], ...shown.map((d, i) => [`s${i}`, butterfly && i === 0 && d.values[j] != null ? -Number(d.values[j]) : d.values[j]])]));
  const rows = type === "slope" && all.length > 2 ? [all[0], all[all.length - 1]] : all;  // 기울기: 처음과 끝 두 시점
  const dot = rows.length > 24 ? false : { r: type === "slope" ? 4 : 3, strokeWidth: 0 };
  const color = shown.map((d, i) => pickColor(card, "series", d.label, colors, i));
  const keys = shown.length > 1 && !range ? shown.map((d, i) => ({ name: d.label, color: color[i] })) : undefined;  // 계열이 하나면 범례 없음
  if (type === "radar") {
    return (
      <Frame keys={keys}>
        <RadarChart data={rows} margin={MARGIN} outerRadius="80%">
          <PolarGrid stroke="var(--line)" />
          <PolarAngleAxis dataKey="label" tick={TICK} />
          <PolarRadiusAxis tick={TICK} axisLine={false} tickFormatter={tickNum} />
          {shown.map((d, i) => <Radar key={i} dataKey={`s${i}`} name={d.label} stroke={color[i]} strokeWidth={2} fill={color[i]} fillOpacity={0.14} dot={dot} isAnimationActive={false} />)}
          <Tooltip {...TIP} />
        </RadarChart>
      </Frame>
    );
  }
  const isLine = (i: number) => LINES.includes(type) || (!type && shown[i].type === "line") || (combo && i > 0);
  const bar = (i: number) => {
    if (butterfly) return 0;
    const top = !stacked || i === shown.length - 1;  // 누적 막대는 맨 위 조각만 둥글게
    return top ? (hbar ? [0, 4, 4, 0] : [4, 4, 0, 0]) as [number, number, number, number] : 0;
  };
  const curve = type === "polyline" || type === "slope" ? "linear" : type === "step" ? "step" : "monotone";
  const series = range ? [<Bar key="r" dataKey={(r: Row) => [r.s0, r.s1]} name={`${shown[0].label} ~ ${shown[1].label}`} yAxisId="y" fill={color[0]} radius={4} maxBarSize={44} isAnimationActive={false} />]
    : shown.map((d, i) => {
      const c = color[i], key = `s${i}`, yAxisId = (d.axis === "right" && !type) || (combo && i > 0) ? "y1" : "y";
      if (!isLine(i)) return <Bar key={i} dataKey={key} name={d.label} yAxisId={hbar ? undefined : yAxisId} fill={c} radius={bar(i)} maxBarSize={44} stackId={stacked ? "a" : undefined} isAnimationActive={false} />;
      const dash = d.dash === "dashed" ? "6 4" : undefined;
      if (AREAS.includes(type)) return <Area key={i} dataKey={key} name={d.label} yAxisId="y" type={curve} stroke={c} strokeWidth={2} fill={c} fillOpacity={stacked ? 0.45 : 0.14} dot={stacked ? false : dot} stackId={stacked ? "a" : undefined} connectNulls isAnimationActive={false} />;
      return <Line key={i} dataKey={key} name={d.label} yAxisId={yAxisId} type={curve} stroke={c} strokeWidth={2} strokeDasharray={dash} dot={dot ? { ...dot, fill: c } : false} connectNulls isAnimationActive={false} />;
    });
  const lines = range ? [] : series.filter((_, i) => isLine(i)), bars = series.filter(x => !lines.includes(x));  // 선은 막대 앞에
  const valueTick = full ? pct100 : butterfly ? absTick : percent ? (v: unknown) => `${Number(v).toLocaleString()}%` : tickNum;
  return (
    <Frame keys={keys}>
      <ComposedChart data={rows} layout={hbar ? "vertical" : "horizontal"} margin={MARGIN} barCategoryGap="28%" stackOffset={full ? "expand" : butterfly ? "sign" : undefined}>
        <CartesianGrid stroke="var(--line)" vertical={hbar} horizontal={!hbar} />
        {hbar ? <XAxis type="number" {...AXIS} tickFormatter={valueTick} domain={full ? [0, 1] : domainOf(ranges.y)} allowDataOverflow={ranged(ranges.y)} />
          : <XAxis dataKey="label" {...AXIS} />}
        {hbar ? <YAxis type="category" dataKey="label" {...AXIS} width={80} />
          : <YAxis yAxisId="y" {...AXIS} tickFormatter={valueTick} domain={full ? [0, 1] : domainOf(ranges.y)} allowDataOverflow={ranged(ranges.y)} width={48} />}
        {right ? <YAxis yAxisId="y1" orientation="right" {...AXIS} tickFormatter={tickNum} domain={domainOf(ranges.y1)} allowDataOverflow={ranged(ranges.y1)} width={48} /> : null}
        {bars}
        {lines}
        <Tooltip {...TIP} formatter={butterfly ? (v: unknown) => shownum(Math.abs(Number(v))) : TIP.formatter} cursor={{ fill: "var(--line)", fillOpacity: 0.4 }} />
      </ComposedChart>
    </Frame>
  );
}

/** 겹친 고리 수 상한(링이 너무 얇아지지 않게) */
const MAX_RINGS = 6;
type Slice = { label: string; v: number; real: number; ring: string; share: number };
/** 링·조각 툴팁: "링 · 조각"과 값(비중 %) */
const sliceTip = { ...TIP, formatter: (v: unknown, _n: unknown, item: { payload?: Slice }) => {
  const p = item.payload;
  return p ? [`${shownum(p.real)} (${(p.share * 100).toFixed(1)}%)`, p.ring ? `${p.ring} · ${p.label}` : p.label] : shownum(v);
} };

/** 둥근 차트: 도넛·파이·방사형 막대(radial)·장미(polar). view.rings면 계열마다 링(안쪽부터, 같은 조각은 같은 색에 바깥 링일수록 옅게), 아니면 첫 계열만 */
function RoundChart({ card, s, colors }: { card: SCard; s: Series; colors: ThemeColors }) {
  const type = card.view.type, sets = card.view.rings && s.datasets.length > 1 ? s.datasets.slice(0, MAX_RINGS) : s.datasets.slice(0, 1), cut = card.view.rings && s.datasets.length > MAX_RINGS;
  const parts = s.labels.map((l, j) => pickColor(card, "parts", l, colors, j)), keys = s.labels.map((l, j) => ({ name: l, color: parts[j] }));
  const many = sets.length > 1, title = many ? `안쪽부터 ${sets.map(d => d.label).join(" · ")}${cut ? ` (앞 ${MAX_RINGS}개)` : ""}` : undefined;
  const fade = (i: number) => (many ? 1 - (0.45 * i) / (sets.length - 1) : 1);
  const slices = sets.map(d => {
    const vals = s.labels.map((_, j) => Math.max(0, Number(d.values[j]) || 0)), total = vals.reduce((a, v) => a + v, 0);
    return s.labels.map((label, j): Slice => ({ label, v: vals[j], real: vals[j], ring: many ? d.label : "", share: total ? vals[j] / total : 0 }));
  });
  if (type === "radial") {
    // 링이면 줄 하나 = 계열 하나(조각을 이어 쌓는다), 아니면 조각마다 한 줄
    const data = many ? slices.map((sl, i) => ({ ring: sets[i].label, ...Object.fromEntries(sl.map((x, j) => [`p${j}`, x.v])) })) : (slices[0] ?? []).map((x, j) => ({ ...x, fill: parts[j] }));
    const max = Math.max(0, ...(many ? slices.map(sl => sl.reduce((a, x) => a + x.v, 0)) : (slices[0] ?? []).map(x => x.v))) || 1;  // 값이 1보다 작아도 꽉 차게, 모두 0이면 1
    return (
      <Frame keys={keys} title={title}>
        <RadialBarChart data={data} innerRadius="18%" outerRadius="95%" startAngle={90} endAngle={-270} margin={MARGIN}>
          <PolarAngleAxis type="number" domain={[0, max]} tick={false} axisLine={false} />
          {many ? s.labels.map((l, j) => <RadialBar key={j} dataKey={`p${j}`} name={l} stackId="a" fill={parts[j]} isAnimationActive={false} />)
            : <RadialBar dataKey="v" name={sets[0]?.label} background={{ fill: "var(--line)", fillOpacity: 0.35 }} cornerRadius={4} isAnimationActive={false}>{parts.map((c, j) => <Cell key={j} fill={c} />)}</RadialBar>}
          <Tooltip {...TIP} />
        </RadialBarChart>
      </Frame>
    );
  }
  const radii = ringRadii(sets.length, type === "doughnut" ? 30 : 0, 95), top = Math.max(0, ...slices.flat().map(x => x.v)) || 1;
  return (
    <Frame keys={keys} title={title}>
      <PieChart margin={MARGIN}>
        {slices.map((sl, i) => {
          const inner = `${type === "doughnut" && !many ? 59 : radii[i].inner}%`, outer = `${radii[i].outer}%`;
          if (type === "polar") {  // 장미: 칸 각도는 같고 반지름이 값에 비례
            return (
              <Pie key={i} data={sl.map(x => ({ ...x, v: 1 }))} dataKey="v" nameKey="label" innerRadius={inner} outerRadius={outer} stroke="var(--card)" strokeWidth={2} isAnimationActive={false}
                shape={(p: PieSectorShapeProps) => <Sector {...p} outerRadius={p.innerRadius + ((p.outerRadius - p.innerRadius) * (p.payload as Slice).real) / top} />}>
                {parts.map((c, j) => <Cell key={j} fill={c} fillOpacity={fade(i)} />)}
              </Pie>
            );
          }
          return (
            <Pie key={i} data={sl} dataKey="v" nameKey="label" innerRadius={inner} outerRadius={outer} cornerRadius={type === "doughnut" ? 4 : 0} stroke="var(--card)" strokeWidth={2} isAnimationActive={false}>
              {parts.map((c, j) => <Cell key={j} fill={c} fillOpacity={fade(i)} />)}
            </Pie>
          );
        })}
        <Tooltip {...sliceTip} />
      </PieChart>
    </Frame>
  );
}

/** 첫 계열 하나로 그리는 것: 폭포(받침부터 증감 막대, 오름 --ok·내림 --err), 파레토(큰 값부터 막대 + 누적 % 선, 오른쪽 축 0~100%),
 *  추세선(점 + 최소제곱 직선), 롤리팝(가는 막대 + 점) */
function OneSeries({ card, s, colors }: { card: SCard; s: Series; colors: ThemeColors }) {
  const type = card.view.type, d = s.datasets[0];
  if (!d) return null;
  const c = pickColor(card, "series", d.label, colors, 0), bar = { yAxisId: "y", maxBarSize: 44, isAnimationActive: false } as const;
  let rows: Row[], body: ReactNode, tip: object = TIP, right = false;
  if (type === "waterfall") {
    const w = waterfallRows(d.values);
    rows = s.labels.map((label, j) => ({ label, lo: w[j].base, hi: w[j].base + w[j].size, v: Number(d.values[j]) || 0, end: w[j].end }));
    tip = { ...TIP, formatter: (_v: unknown, _n: unknown, item: { payload?: Row }) => [`${shownum(item.payload?.v)} (누적 ${shownum(item.payload?.end)})`, d.label] };
    body = <Bar dataKey={(r: Row) => [r.lo, r.hi]} name={d.label} radius={2} {...bar}>{w.map((x, j) => <Cell key={j} fill={x.up ? "var(--ok)" : "var(--err)"} />)}</Bar>;
  } else if (type === "pareto") {
    rows = paretoRows(s.labels, d.values);
    right = true;
    body = [<Bar key="b" dataKey="v" name={d.label} fill={c} radius={[4, 4, 0, 0]} {...bar} />,
      <Line key="l" dataKey="pct" yAxisId="y1" name="누적 %" type="linear" stroke="var(--muted)" strokeWidth={2} dot={{ r: 3, strokeWidth: 0, fill: "var(--muted)" }} isAnimationActive={false} />];
  } else if (type === "trendline") {
    const f = fitLine(d.values);
    rows = s.labels.map((label, j) => ({ label, v: d.values[j], fit: f ? f.a + f.b * j : null }));
    body = [<Scatter key="p" dataKey="v" name={d.label} yAxisId="y" fill={c} isAnimationActive={false} />,
      f ? <Line key="f" dataKey="fit" name="추세선" yAxisId="y" type="linear" stroke="var(--muted)" strokeWidth={2} strokeDasharray="6 4" dot={false} isAnimationActive={false} /> : null];
  } else {
    rows = s.labels.map((label, j) => ({ label, v: d.values[j] }));
    body = [<Bar key="s" dataKey="v" name={d.label} fill={c} barSize={2} {...bar} />, <Scatter key="d" dataKey="v" name={d.label} yAxisId="y" fill={c} isAnimationActive={false} />];
  }
  return (
    <Frame keys={right ? [{ name: d.label, color: c }, { name: "누적 %", color: "var(--muted)" }] : undefined}>
      <ComposedChart data={rows} margin={MARGIN} barCategoryGap="28%">
        <CartesianGrid stroke="var(--line)" vertical={false} />
        <XAxis dataKey="label" {...AXIS} />
        <YAxis yAxisId="y" {...AXIS} tickFormatter={tickNum} width={48} />
        {right ? <YAxis yAxisId="y1" orientation="right" {...AXIS} domain={[0, 100]} tickFormatter={(v: unknown) => `${v}%`} width={44} /> : null}
        {body}
        <Tooltip {...tip} cursor={{ fill: "var(--line)", fillOpacity: 0.4 }} />
      </ComposedChart>
    </Frame>
  );
}

export type Points = { x: string; y: string; points: (number | string)[][]; groups: string[]; size_range?: [number, number] | null; a?: number | null; b?: number | null; r?: number | null; n?: number; note?: string };
/** 산점도·버블(pointsConfig). 점 하나는 [x, y, (크기), (그룹)]. 버블 반지름 = 3 + 17·√((v−min)/(max−min)) px. 추세선 y = a + b·x와 r은 서버가 전체 행으로 구한 값 */
export function PointsChart({ card, colors }: { card: SCard; colors: ThemeColors }) {
  const p = card.data.points as unknown as Points, view = card.view, bubble = view.type === "bubble";
  const [lo, hi] = p.size_range ?? [0, 0], radius = (v: number) => hi > lo ? 3 + 17 * Math.sqrt((v - lo) / (hi - lo)) : 10;
  const groups = pointGroups(p).map((name, i) => {
    const c = pickColor(card, "series", name, colors, i), rows = p.points.filter(r => !p.groups.length || r[r.length - 1] === name);
    return { name, c, data: rows.map(r => ({ x: r[0], y: r[1], r: bubble ? radius(r[2] as number) : 3 })) };
  });
  const xs = p.points.map(r => r[0] as number), trend = view.trend && p.b != null && xs.length ? ([Math.min(...xs), Math.max(...xs)].map(x => ({ x, y: p.a! + p.b! * x })) as [{ x: number; y: number }, { x: number; y: number }]) : null;
  const keys: Key[] = [...groups.map(g => ({ name: g.name, color: g.c })), ...(trend ? [{ name: "추세선", color: "var(--muted)" }] : [])];
  const title = view.show_r && p.r != null ? `r = ${p.r.toFixed(3)} (${(p.n ?? 0).toLocaleString()}개)` : undefined;
  const shape = (o: number, stroke: boolean) => function Dot(props: { cx?: number; cy?: number; payload?: { r: number }; fill?: string }) {
    return <circle cx={props.cx} cy={props.cy} r={props.payload?.r ?? 3} fill={props.fill} fillOpacity={o} stroke={stroke ? props.fill : "none"} />;
  };
  return (
    <Frame keys={keys.length > 1 ? keys : undefined} title={title}>
      <ScatterChart margin={TITLED}>
        <CartesianGrid stroke="var(--line)" vertical={false} />
        <XAxis type="number" dataKey="x" name={p.x} {...AXIS} tickFormatter={tickNum} domain={["auto", "auto"]} label={xTitle(p.x)} />
        <YAxis type="number" dataKey="y" name={p.y} {...AXIS} tickFormatter={tickNum} domain={view.ranges?.y ? domainOf(view.ranges.y) : ["auto", "auto"]} allowDataOverflow={ranged(view.ranges?.y)} label={yTitle(p.y)} width={56} />
        {groups.map((g, i) => <Scatter key={i} name={g.name} data={g.data} fill={g.c} shape={shape(bubble ? 0.55 : 0.7, bubble)} isAnimationActive={false} />)}
        {trend ? <ReferenceLine segment={trend} stroke="var(--muted)" strokeDasharray="6 4" strokeWidth={2} ifOverflow="extendDomain" /> : null}
        <Tooltip {...TIP} cursor={{ strokeDasharray: "3 3" }} />
      </ScatterChart>
    </Frame>
  );
}

export type Ana = {
  column?: string; histogram?: { edges: number[]; counts: number[] }; box?: { q1: number; median: number; q3: number; low: number | null; high: number | null };
  values?: { labels: string[]; counts: number[]; nulls: number; other: number } | (number | null)[]; columns?: string[]; matrix?: (number | null)[][];
  points?: [number, number][]; x?: string; y?: string; labels?: string[]; agg?: string; by?: string; value?: string; unit?: string;
};

/** 상자 그림: 수염(1.5×IQR 안 최소~최대)은 가는 선, Q1~Q3은 상자, 중앙값은 굵은 선. 축 눈금은 Recharts, 상자는 그 축의 눈금 함수로 직접 그린다 */
function BoxShape({ b, main, second }: { b: NonNullable<Ana["box"]>; main: string; second: string }) {
  const x = useXAxisScale(), area = usePlotArea();
  if (!x || !area) return null;
  const at = (v: number) => x(v) ?? 0, cy = area.y + area.height / 2, h = Math.min(56, area.height * 0.8);
  const low = b.low ?? b.q1, high = b.high ?? b.q3;
  return (
    <g>
      <rect x={at(low)} y={cy - 1} width={Math.max(1, at(high) - at(low))} height={2} fill="var(--muted)"><title>{`수염: ${shownum(low)} ~ ${shownum(high)}`}</title></rect>
      <rect x={at(b.q1)} y={cy - h / 2} width={Math.max(1, at(b.q3) - at(b.q1))} height={h} fill={main} fillOpacity={0.85} stroke={main}><title>{`Q1~Q3: ${shownum(b.q1)} ~ ${shownum(b.q3)}`}</title></rect>
      <rect x={at(b.median) - 1.5} y={cy - h / 2} width={3} height={h} fill={second}><title>{`중앙값: ${shownum(b.median)}`}</title></rect>
    </g>
  );
}

/** 상관 히트맵: 칸 색은 r ≥ 0이면 주 색, 아니면 보조 색, 진하기 0.1 + 0.9·|r|. 값이 없으면 선 색과 "-" */
function HeatCells({ d, main, second }: { d: Ana; main: string; second: string }) {
  const x = useXAxisScale(), y = useYAxisScale();
  if (!x || !y) return null;
  const cols = d.columns!, w = Math.abs((x(1) ?? 0) - (x(0) ?? 0)), h = Math.abs((y(1) ?? 0) - (y(0) ?? 0));
  const font = Math.max(9, Math.min(14, w / 4.5));
  return (
    <g>
      {d.matrix!.flatMap((row, i) => row.map((r, j) => {
        const cx = x(j) ?? 0, cy = y(i) ?? 0;
        return (
          <g key={`${i}-${j}`}>
            <rect x={cx - w / 2 + 1} y={cy - h / 2 + 1} width={Math.max(0, w - 2)} height={Math.max(0, h - 2)} fill={r == null ? "var(--line)" : r >= 0 ? main : second} fillOpacity={r == null ? 1 : 0.1 + 0.9 * Math.abs(r)}>
              <title>{`${cols[i]} × ${cols[j]}: ${r == null ? "-" : r.toFixed(3)}`}</title>
            </rect>
            <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle" fontSize={font} fill={r != null && Math.abs(r) > 0.55 ? "#fff" : "var(--text)"} pointerEvents="none">
              {r == null ? "-" : (Math.abs(r) < 0.005 ? 0 : r).toFixed(2)}{/* -0.00으로 보이지 않게 */}
            </text>
          </g>
        );
      }))}
    </g>
  );
}

/** 분석 그림 하나(anaConfig): hist·box·values·heat·scatter·groups·trend. colors.series[0]은 주 색, [1]은 보조 색 */
export function AnaChart({ d, view, main, second, kit }: { d: Ana; view?: string; main: string; second: string; kit: SeriesKit }) {
  const yName = d.agg === "count" ? "개수" : `${d.value} ${kit.aggName(d.agg ?? "")}`;
  if (view === "hist") {
    const e = d.histogram!.edges, rows = d.histogram!.counts.map((n, i) => ({ label: `${shownum(e[i])}–${shownum(e[i + 1])}`, n }));
    return (
      <Frame>
        <ComposedChart data={rows} margin={TITLED} barCategoryGap="4%" barGap={0}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="label" {...AXIS} label={xTitle(d.column)} />
          <YAxis {...AXIS} tickFormatter={tickNum} label={yTitle("행 수")} width={56} />
          <Bar dataKey="n" name="행 수" fill={main} radius={[2, 2, 0, 0]} maxBarSize={120} isAnimationActive={false} />
          <Tooltip {...TIP} cursor={{ fill: "var(--line)", fillOpacity: 0.4 }} />
        </ComposedChart>
      </Frame>
    );
  }
  if (view === "box") {
    const b = d.box!, nums = [b.low, b.high, b.q1, b.q3, b.median].filter((v): v is number => v != null);
    return (
      <Frame keys={[{ name: "수염 (1.5×IQR 안의 최소~최대)", color: "var(--muted)" }, { name: "Q1~Q3", color: main, opacity: 0.85 }, { name: "중앙값", color: second }]}>
        <ComposedChart data={[{ label: d.column }]} layout="vertical" margin={MARGIN}>
          <CartesianGrid stroke="var(--line)" horizontal={false} />
          <XAxis type="number" {...AXIS} tickFormatter={tickNum} domain={[Math.min(...nums), Math.max(...nums)]} />
          <YAxis type="category" dataKey="label" {...AXIS} width={80} />
          <Bar dataKey={() => [Math.min(...nums), Math.max(...nums)]} fill="none" isAnimationActive={false} />{/* 보이지 않는 막대: 축 눈금을 만들려고(그림 요소가 없으면 축이 그려지지 않는다) */}
          <Customized component={<BoxShape b={b} main={main} second={second} />} />
        </ComposedChart>
      </Frame>
    );
  }
  if (view === "values") {
    const v = d.values as { labels: string[]; counts: number[]; nulls: number; other: number };
    const labels = [...v.labels, ...(v.nulls ? ["(빈 값)"] : []), ...(v.other ? ["그 밖"] : [])], data = [...v.counts, ...(v.nulls ? [v.nulls] : []), ...(v.other ? [v.other] : [])];
    return (
      <Frame>
        <ComposedChart data={labels.map((label, i) => ({ label, n: data[i] }))} margin={TITLED} barCategoryGap="28%">
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="label" {...AXIS} />
          <YAxis {...AXIS} tickFormatter={tickNum} label={yTitle("행 수")} width={56} />
          <Bar dataKey="n" name="행 수" radius={[4, 4, 0, 0]} maxBarSize={44} isAnimationActive={false}>
            {labels.map((l, i) => <Cell key={i} fill={l === "그 밖" || l === "(빈 값)" ? "var(--line)" : main} />)}
          </Bar>
          <Tooltip {...TIP} cursor={{ fill: "var(--line)", fillOpacity: 0.4 }} />
        </ComposedChart>
      </Frame>
    );
  }
  if (view === "heat") {
    const k = d.columns!.length, ticks = d.columns!.map((_, i) => i), name = (v: number) => d.columns![v] ?? "";
    return (
      <Frame>
        <ComposedChart margin={MARGIN}>
          <XAxis type="number" dataKey="x" {...AXIS} domain={[-0.5, k - 0.5]} ticks={ticks} tickFormatter={name} interval={0} />
          <YAxis type="number" dataKey="y" {...AXIS} domain={[-0.5, k - 0.5]} ticks={ticks} tickFormatter={name} interval={0} reversed width={80} />
          <Scatter data={ticks.flatMap(y => ticks.map(x => ({ x, y })))} shape={() => <g />} isAnimationActive={false} />{/* 보이지 않는 점: 축 눈금을 만들려고 */}
          <Customized component={<HeatCells d={d} main={main} second={second} />} />
        </ComposedChart>
      </Frame>
    );
  }
  if (view === "scatter") {
    return (
      <Frame>
        <ScatterChart margin={TITLED}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis type="number" dataKey="x" name={d.x} {...AXIS} tickFormatter={tickNum} domain={["auto", "auto"]} label={xTitle(d.x)} />
          <YAxis type="number" dataKey="y" name={d.y} {...AXIS} tickFormatter={tickNum} domain={["auto", "auto"]} label={yTitle(d.y)} width={56} />
          <Scatter data={d.points!.map(([x, y]) => ({ x, y }))} fill={main} fillOpacity={0.55} shape={(p: { cx?: number; cy?: number }) => <circle cx={p.cx} cy={p.cy} r={2.5} fill={main} fillOpacity={0.55} />} isAnimationActive={false} />
          <Tooltip {...TIP} cursor={{ strokeDasharray: "3 3" }} />
        </ScatterChart>
      </Frame>
    );
  }
  const rows = (d.labels ?? []).map((label, i) => ({ label, v: (d.values as (number | null)[])[i] }));
  if (view === "groups") {
    return (
      <Frame>
        <ComposedChart data={rows} margin={TITLED} barCategoryGap="28%">
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="label" {...AXIS} label={xTitle(d.by)} />
          <YAxis {...AXIS} tickFormatter={tickNum} label={yTitle(yName)} width={56} />
          <Bar dataKey="v" name={kit.aggName(d.agg ?? "")} fill={main} radius={[4, 4, 0, 0]} maxBarSize={44} isAnimationActive={false} />
          <Tooltip {...TIP} cursor={{ fill: "var(--line)", fillOpacity: 0.4 }} />
        </ComposedChart>
      </Frame>
    );
  }
  return (
    <Frame>
      <ComposedChart data={rows} margin={TITLED}>
        <CartesianGrid stroke="var(--line)" vertical={false} />
        <XAxis dataKey="label" {...AXIS} label={xTitle(UNIT_NAMES[d.unit ?? ""])} />
        <YAxis {...AXIS} tickFormatter={tickNum} label={yTitle(yName)} width={56} />
        <Line dataKey="v" name={kit.aggName(d.agg ?? "")} type="monotone" stroke={main} strokeWidth={2} dot={rows.length > 24 ? false : { r: 3, strokeWidth: 0, fill: main }} connectNulls isAnimationActive={false} />
        <Tooltip {...TIP} />
      </ComposedChart>
    </Frame>
  );
}
