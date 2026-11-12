"use client";
// 카드 설정 패널의 [모양] 줄(옛 dash.js tools·svgTools·loadPoints·colorEditor): 차트 종류(바꿀 수 없는 종류는 흐리게, 누르면 이유 = seriesKit.typeReason)·시간 단위,
// 산점도·버블 점 받기(/api/chart/points)와 [크기]·추세선·r, 목표 칸(값 모양이 있는 SVG 카드), 값 모양(형식·단위·좋은 방향), 비교 방식, 규칙 편집(N6 RulesEditor), 색 편집기(이름마다 HEX).
// 바꾸면 onChange({view}) 한 번. 점은 카드 값(data.points)에 두고, 기간·실시간으로 구한 값이 보이는 중이면 그 값에도 붙인다(dispatch values).
// (Phase 9) GroupPicker 아래 [자동 규칙](규칙 켜고 끄기·수치, 이름은 서버 consts.dash.auto_names)·[값 축]·[정렬](source.body.order)·[모든 데이터]·[표로 보기]·값 축 범위(view.ranges). 다시 구해야 하는 변경은 안내만.
import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import { CHART_KINDS } from "@/components/cards/ChartCard";
import { seriesKit } from "@/components/cards/chart/series";
import { useTheme } from "@/components/cards/svg/common";
import { funnelColors, flowDropout, GAP_STATS } from "@/components/cards/svg/__pure__";
import { cardRequest, chartPoints, computeCard, labels as fetchLabels } from "@/lib/api";
import { anaView, HEX } from "@/lib/calc";
import { useConstants } from "@/lib/constants";
import { pickColor } from "@/lib/series";
import { computedUpdate, useDash } from "@/lib/store";
import type { Card, CardData, CardView, ChartType } from "@/lib/types";
import { autoParams, percentDomain, type AutoRule, type AutoView, type AxisScale, type Order, type ParamSpec } from "@/lib/autoviz";
import { RulesEditor } from "../RulesEditor";
import { Check } from "../menu/Fields";
import { DATE_X, WATCH } from "../menu/kinds";
import { GroupPicker } from "./GroupPicker";
import { parseBands } from "../menu/build";
import { ROUND_TYPES } from "@/components/cards/chart/shapes";

const FORMATS: [string, string][] = [["number", "숫자"], ["percent", "%"], ["currency", "₩"]];
// 목표·값 모양이 없는 SVG 카드(옛 svgTools plain)
const RINGS: [string, string][] = [["stack", "한 고리 누적"], ["double", "겹친 두 고리"], ["half", "반원 게이지"], ["ticks", "고리와 목표 눈금"]];
const PLAIN = ["recent_rows", "gauge", "status", "alerts", "map", "deviation", "table", "sentence", "funnel", "geomap",
  "histogram", "boxplot", "heatmap", "calheat", "treemap", "sunburst", "bump", "sankey", "cohort", "candle", "wordcloud"];
// P16 KPI 아이콘(svg/common Icon)·P15 큰 값 맞춤·추이선 자리, P14 겹친 도넛을 쓸 수 있는 차트 종류
const ICONS: [string, string][] = [["chart", "막대"], ["up", "오름"], ["user", "사람"], ["cart", "장바구니"], ["coin", "동전"], ["clock", "시계"]];
const FUNNEL_SHAPES: [string, string][] = [["flow", "흐름"], ["ladder", "사다리꼴"]];
const GAP_NAMES: Record<string, string> = { mean: "평균", median: "중앙값", trimmed: "절사평균" };
// 워드클라우드 방식(view.cloud, 서버 dash_store.check_cloud): 방식·뽑을 행·맞춤·품사의 보이는 이름
type Cloud = NonNullable<CardView["cloud"]>;
const CLOUD_MODES: [Cloud["mode"], string][] = [["values", "컬럼 값"], ["labels", "라벨 이름"], ["words", "글 속 단어"]];
const CLOUD_ROWS: [string, string][] = [["all", "전체 행"], ["tagged", "고른 태그의 행"]];
const CLOUD_MATCH: [string, string][] = [["any", "하나라도"], ["all", "모두"]];
const CLOUD_POS: [NonNullable<Cloud["pos"]>[number], string][] = [["noun", "명사"], ["verb", "동사"], ["adj", "형용사"], ["foreign", "외국어"]];
const DEFAULT_POS = "noun";  // 품사를 비우면 서버 기본(명사)
// (Phase 9) 자동 규칙 칸. 규칙·정렬의 보이는 이름은 서버 상수(consts.dash.auto_names·order_names), 수치 범위·기본값은 consts.dash.auto
type Look = "series" | "points" | "scatter" | "ana" | "hist" | "box";
const SERVER_AUTO: AutoRule[] = ["bins", "tail", "density"];  // 값을 다시 구해야 하는 규칙(서버 viz_rules.SERVER_RULES). 계열 차트에는 이 밖의 규칙만
const LOOK_RULES: Record<Exclude<Look, "series">, AutoRule[]> = { points: ["density"], scatter: ["density"], ana: [], hist: ["bins", "tail"], box: ["tail"] };
const PARAM_NAMES: Record<string, string> = {  // 규칙.수치 → 칸 이름
  "sort.group_by": "기준 계열", "topn.v": "세로 N", "topn.h": "가로 N", "round.max": "최대 조각", "round.keep": "남길 조각", "horizontal.from": "기준 개수",
  "spaghetti.max": "최대 선", "spaghetti.keep": "색 칠할 선", "spaghetti.mode": "방식", "yzoom.pad": "여백", "log_hint.ratio": "배수", "log_hint.skew": "왜도",
  "resample.mode": "방식", "resample.px": "점 간격(px)", "density.faint": "옅게 그릴 점 수", "density.hex": "육각 칸 점 수", "density.alpha": "투명도",
  "density.faint_alpha": "옅을 때 투명도", "density.cols": "육각 칸 가로 수", "num.style": "방식", "refs.lines": "선", "bins.rule": "규칙", "bins.min": "최소 구간",
  "bins.max": "최대 구간", "tail.lo": "아래", "tail.hi": "위",
};
const CHOICE_NAMES: Record<string, string> = { gray: "회색 선", small: "따로 그리기", lttb: "LTTB", rollup: "묶기", ko: "한국식", en: "영어식", mean: "평균", median: "중앙값", p80: "파레토 80%", fd: "FD", sturges: "Sturges" };  // 고를 값의 보이는 이름
const PCT_PARAMS = ["yzoom.pad", "tail.lo", "tail.hi"];  // 저장은 비율(0.1), 칸은 %(10)
const PCT = 100, PCT_PRECISION = 12;  // % 칸의 보이는 값 = 저장값 × 100, 부동소수 끝자리(0.07×100 = 7.000000000000001)를 12자리로 다듬는다
const SCALE_NAMES: [string, string][] = [["", "보통"], ["log", "로그"], ["symlog", "SymLog"]];  // 값 축 눈금(view.scale, 서버 viz_rules.SCALES, 보통은 키 없음)
const SIDES = { y: "왼쪽", y1: "오른쪽" } as const;  // 값 축 범위 오류 글의 축 이름(쿼리 화면 rangeError와 같은 글)
const LEGACY_ORDER: Order = "x";  // body.order가 없는 기존 카드의 정렬(서버 viz_rules.LEGACY_ORDER, autoEnv와 같다)
const RECOMPUTE = "바꾸면 [다시 구하기]를 누르세요. 새 값으로 다시 구해요.";  // 서버가 값을 다시 구해야 하는 변경
const TEMPLATE_EMPTY = "문장 틀을 쓰세요. 예: 오늘 매출은 {매출}이에요";  // build.ts의 문구와 같은 글자
const ORDER_REFETCH ="그룹이 많아 서버가 고른 값이에요. 바꾸면 [다시 구하기]를 누르세요. 서버가 고르는 그룹이 달라져요.";  // 잘린 카드의 정렬 변경

export function Tools({ card, columns, onChange }: { card: Card; columns: string[]; onChange: (patch: Partial<Card>) => void }) {
  const consts = useConstants();
  const { state, dispatch } = useDash();
  const theme = useTheme();
  const kit = useMemo(() => consts && seriesKit(consts), [consts]);
  const [msg, setMsg] = useState("");
  const [paint, setPaint] = useState(false);
  const [autoOpen, setAutoOpen] = useState(false);
  const [rangeErr, setRangeErr] = useState("");
  if (!consts || !kit) return null;
  const live = state.values[card.id]?.data, shown: Card = { ...card, data: live ?? card.data };
  const view = card.view, setView = (p: Partial<CardView>, extra: Partial<Card> = {}) => onChange({ view: { ...view, ...p }, ...extra });
  // 정의(def_hash)가 바뀐 카드를 다시 구한다(setDate와 같은 길): 워드클라우드 방식, 서버 범위 묶기(GroupPicker)
  const recompute = async (next: Card) => {
    const r = await computeCard(cardRequest({ ...next, data: {} }), state.ctx);
    const u = computedUpdate(card.id, r.data, state.ctx, { view: next.view, source: { ...next.source, ...r.source } });
    onChange(u.patch);
    dispatch({ type: "values", results: [u.result] });
  };
  // 컬럼 값 방식이면 cloud 키를 지운다(기본)
  const setCloud = (cloud: Cloud) => {
    const v = { ...view };
    if (cloud.mode === "values") delete v.cloud; else v.cloud = cloud;
    const next = { ...card, view: v };
    onChange({ view: v });
    recompute(next).then(() => setMsg(""), e => setMsg((e as Error).message));
  };
  const byUnit = shown.data?.by_unit, timed = !!byUnit?.day && card.kind !== "dual_trend";
  const pointy = ["scatter", "bubble"].includes(view.type);
  // 점을 카드 값에 넣거나 뺀다(보이는 구한 값에도)
  const withPoints = (data: CardData | undefined, points: CardData["points"]) => { const d = { ...(data ?? {}) }; if (points) d.points = points; else delete d.points; return d; };
  const putPoints = (next: Partial<CardView>, points: CardData["points"]) => {
    setView(next, { data: withPoints(card.data, points) });
    if (live) dispatch({ type: "values", results: [{ id: card.id, data: withPoints(live, points) }] });
  };
  // 산점도·버블이면 서버에서 점을 받는다(연결 필요, 다시 구하기와 같은 기간). 다른 종류면 점은 버린다(카드 크기)
  const loadPoints = async (next: Partial<CardView>) => {
    const v = { ...view, ...next };
    if (!["scatter", "bubble"].includes(v.type)) return putPoints(next, undefined);
    const body = (card.source.body ?? {}) as Record<string, unknown> & { series?: { y?: string }[] };
    const { points } = await chartPoints({ ...body, y: body.series?.[0]?.y, size: v.type === "bubble" ? v.points?.size ?? "" : "", source_key: card.source.key, date: card.source.date, ctx: state.ctx });
    putPoints(next, points);
  };
  const tryPoints = (next: Partial<CardView>, ok = "") => loadPoints(next).then(() => setMsg(ok), e => setMsg((e as Error).message));  // ok: 바꾼 뒤 보일 안내(첫 계열만 그림 등)
  const typeBtn = ([id, name]: [string, string]) => {
    const why = kit.typeReason(shown, id as Exclude<ChartType, "">);
    return (
      <button key={id} type="button" className={`btype${view.type === id ? " on" : ""}${why ? " off" : ""}`} title={why || undefined} aria-pressed={view.type === id} onClick={() => {
        if (why) return setMsg(why);
        const next = view.type === id ? "" : (id as CardView["type"]);  // 한 번 더 누르면 계열마다 정한 대로
        tryPoints({ type: next }, next ? kit.typeNote(shown, next) : "");
      }}>{name}</button>
    );
  };
  const unitText = ["kpi", "donut_kpi", "stripes", "bullet", "calheat"].includes(card.kind);
  const hasRules = card.kind === "table" || card.kind === "sentence" || WATCH.includes(card.kind);
  const withTarget = !CHART_KINDS.includes(card.kind) && !PLAIN.includes(card.kind);
  const target = view.target as { value?: number; name?: string } | undefined, targetErr = (card.data?.target as { error?: string } | undefined)?.error;
  const setTarget = (raw: string) => {
    if (raw === "" ? target?.value == null : +raw === target?.value) return;
    if (raw === "") { const rest = { ...view }; delete rest.target; return onChange({ view: rest, data: { ...card.data, target: null } }); }
    onChange({ view: { ...view, target: { value: +raw } }, data: { ...card.data, target: { value: +raw, label: "", error: "" } } });
  };
  // 종류별 칸(옛 svgTools): 숫자 칸은 비우면 지우고, 칸 수·행 수는 서버 범위(1~100)로 맞춘다
  const num = (key: "cells" | "full" | "size" | "rows" | "bins", label: string, width: number, min?: number, max?: number) => (
    <label className="hint bset">{label}
      <input type="number" aria-label={label} min={min} max={max} step={min === 1 ? 1 : undefined} style={{ width }} key={String(view[key] ?? "")} defaultValue={view[key] ?? ""}
        onBlur={e => {
          if (e.target.value === "") { if (view[key] == null) return; const rest = { ...view }; delete rest[key]; return onChange({ view: rest }); }
          const v = Math.min(max ?? Infinity, Math.max(min ?? -Infinity, min === 1 ? Math.round(+e.target.value) : +e.target.value));  // 칸 수·행 수·구간 수는 정수
          if (v !== view[key]) setView({ [key]: v });
        }} onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }} />
    </label>
  );
  const tiles = !!(view.geo as { tiles?: boolean } | undefined)?.tiles;
  const without = (key: keyof CardView) => { const rest = { ...view }; delete rest[key]; onChange({ view: rest }); };  // 키를 지워 기본으로
  const gapPicked = view.gap_stats ?? [];
  const autoFlow = (card.source.analysis as { mode?: string } | undefined)?.mode === "auto";
  const kindFields = {
    funnel: (<>
      <label className="hint bset">그림 모양<select className="bunit" style={{ flexBasis: "auto" }} aria-label="그림 모양" value={view.funnel_shape ?? "flow"}
          onChange={e => e.target.value === "flow" ? without("funnel_shape") : setView({ funnel_shape: e.target.value as CardView["funnel_shape"] })}>
          {FUNNEL_SHAPES.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
        </select>
      </label>
      {view.funnel_shape === "ladder" && (
        <label className="hint bset">
          <input type="checkbox" aria-label="인원 비례" checked={view.ladder_scale === true} onChange={e => e.target.checked ? setView({ ladder_scale: true }) : without("ladder_scale")} />인원 비례
        </label>
      )}
      <span className="hint">걸린 시간</span>
      {GAP_STATS.map(s => (
        <label key={s} className="hint bset"><input type="checkbox" aria-label={`걸린 시간 ${GAP_NAMES[s]}`} checked={gapPicked.includes(s)}
            onChange={e => setView({ gap_stats: GAP_STATS.filter(g => g === s ? e.target.checked : gapPicked.includes(g)) })} />{GAP_NAMES[s]}
        </label>
      ))}
    </>),
    sankey: autoFlow ? (<>
      <label className="hint bset">이탈 칸
        <input type="checkbox" aria-label="이탈 칸" checked={flowDropout(card)} onChange={e => setView({ flow_dropout: e.target.checked })} />
      </label>
      <label className="hint bset">상한 없음
        <input type="checkbox" aria-label="상한 없음" checked={view.flow_unlimited === true} onChange={e => setView({ flow_unlimited: e.target.checked })} />
      </label>
      <span className="hint">바꾸면 [다시 구하기]를 누르세요. 새 값으로 다시 구해요.</span>
    </>) : null,
    geomap: (<>
      <label className="hint bset">지도 배경(OpenStreetMap)
        <input type="checkbox" aria-label="지도 배경" checked={tiles} title="켜면 지금 보는 지도 범위가 OpenStreetMap 서버(tile.openstreetmap.org)로 전송돼요. 끄면 경계만 그리고 아무것도 보내지 않아요."
          onChange={e => setView({ geo: { ...view.geo, tiles: e.target.checked } })} />
      </label>
      <span className="hint">{tiles ? "보는 범위가 tile.openstreetmap.org로 전송돼요." : ""}</span>
    </>),
    donut_kpi: (
      <label className="hint bset">고리
        <select className="bunit" style={{ flexBasis: "auto" }} aria-label="고리" value={view.ring || "stack"} onChange={e => setView({ ring: e.target.value as CardView["ring"] })}>
          {RINGS.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
        </select>
      </label>
    ),
    wordcloud: <CloudFields cloud={view.cloud ?? { mode: "values" }} schema={card.source.schema ?? ""} table={card.source.table ?? ""} texts={columns} full tagged={!!card.source.body?.labels} onChange={c => setCloud(c)} />,
    sentence: (
      <label className="hint bset" style={{ flexBasis: "100%" }}>문장 틀
        <textarea aria-label="문장 틀" rows={2} style={{ width: "100%", fontSize: 12 }} key={view.template ?? ""} defaultValue={view.template ?? ""}
          onBlur={e => {
            const v = e.target.value.trim();
            if (!v) { e.target.value = view.template ?? ""; return setMsg(TEMPLATE_EMPTY); }
            if (v !== view.template) { setView({ template: v }); setMsg(RECOMPUTE); }
          }} />
      </label>
    ),
    stripes: <>{num("cells", "칸 수", 50, 1, 100)}{num("full", "가득 찬 값", 80)}</>,
    blocks: num("size", "한 칸 크기", 80),
    recent_rows: num("rows", "행 수", 50, 1, 100),
    histogram: <>{num("bins", "구간 수", 50, 1, consts.dash.max_bins ?? 100)}<span className="hint">바꾸면 [다시 구하기]를 누르세요. 새 구간으로 다시 구해요.</span></>,
    bullet: (
      <label className="hint bset">구간
        <input aria-label="구간" placeholder="예: 50, 80, 100" title="바탕 띠의 경계 값. 작은 것부터 쉼표로 1~6개, 비우면 띠 없음" style={{ width: 120 }} key={(view.bands ?? []).join(",")} defaultValue={(view.bands ?? []).join(", ")}
          onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }} onBlur={e => {
            if (!e.target.value.trim()) { if (!view.bands) return; const rest = { ...view }; delete rest.bands; return onChange({ view: rest }); }
            try { const bands = parseBands(e.target.value, consts.dash.max_bands); setMsg(""); if (bands.join() !== (view.bands ?? []).join()) setView({ bands }); } catch (err) { setMsg((err as Error).message); }
          }} />
      </label>
    ),
    kpi: (<>
      <label className="hint bset">아이콘<select className="bunit" style={{ flexBasis: "auto" }} aria-label="아이콘" title="큰 값 옆 아이콘" value={view.icon ?? "chart"} onChange={e => setView({ icon: e.target.value as CardView["icon"] })}>
          {ICONS.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
        </select>
      </label>
      <label className="hint bset">큰 값 크기<select className="bunit" style={{ flexBasis: "auto" }} aria-label="큰 값 크기" value={view.fit ?? "center"} onChange={e => setView({ fit: e.target.value as CardView["fit"] })}>
          <option value="center">가운데</option><option value="fill">꽉 채우기</option>
        </select>
      </label>
      <label className="hint bset">추이선 자리<select className="bunit" style={{ flexBasis: "auto" }} aria-label="추이선 자리" value={view.layout ?? "below"} onChange={e => setView({ layout: e.target.value as CardView["layout"] })}>
          <option value="below">아래</option><option value="right">오른쪽</option>
        </select>
      </label>
    </>),
  }[card.kind as string] ?? null;
  // 순위·캔들·코호트는 기간 단위를 다시 고를 수 있다(서버가 그 단위로 다시 구한다)
  const unitSel = ["bump", "candle", "cohort"].includes(card.kind) && (
    <label className="hint bset">기간 단위<select className="bunit" style={{ flexBasis: "auto" }} aria-label="기간 단위" title="바꾸면 [다시 구하기]로 그 단위의 값을 구해요" value={view.unit || "month"} onChange={e => setView({ unit: e.target.value as CardView["unit"] })}>
        {Object.entries(consts.dash.units).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
      </select>
    </label>
  );
  // 날짜 X축이 아닌 카드에 대시보드의 기간·기준일을 걸 날짜 컬럼(옛 dateSelect). 고르면 그 기간으로 다시 구한다
  const dateCols = shown.data?.date_cols ?? [];
  const setDate = async (value: string) => {
    const source = { ...card.source }, was = card.source.date;
    if (value) source.date = value; else delete source.date;
    try {  // 비울 때도 다시 구한다(기간을 걸지 않은 값으로)
      const r = await computeCard(cardRequest({ ...card, source, data: {} }), state.ctx);
      setMsg("");
      const u = computedUpdate(card.id, r.data, state.ctx, { source: { ...source, ...r.source } });  // 기간이 걸려 있으면 화면 값에
      onChange(u.patch);
      dispatch({ type: "values", results: [u.result] });
    } catch (e) { setMsg(`${(e as Error).message} 기간 기준 날짜는 ${was ? `그대로(${was})` : "그대로(없음)"}예요.`); }
  };
  const dateSel = dateCols.length > 0 && card.kind !== "sankey" && card.kind !== "cohort" && (
    <label className="hint bset">기간 기준<select className="bunit" style={{ flexBasis: "auto" }} aria-label="기간 기준 날짜" title="대시보드 기간과 기준일을 이 날짜 컬럼에 걸어 다시 구해요" value={card.source.date ?? ""} onChange={e => setDate(e.target.value)}>
        <option value="">{DATE_X.includes(card.kind) ? `가로축 날짜${card.source.body?.x ? ` (${String(card.source.body.x)})` : ""}` : "없음"}</option>{/* 날짜가 가로축이면 비워도 서버가 가로축 날짜에 건다 */}
        {dateCols.map(c => <option key={c} value={c}>{c}</option>)}
      </select>
    </label>
  );
  const table = consts.dash.auto;
  const look: Look | null = card.kind === "histogram" ? "hist" : card.kind === "boxplot" ? "box" : !CHART_KINDS.includes(card.kind) ? null
    : kit.usesAna(shown) ? (anaView(shown) === "scatter" ? "scatter" : "ana") : card.kind === "chart" && pointy ? "points" : "series";
  const rules = look && table ? (Object.keys(table) as AutoRule[]).filter(r => (look === "series" ? !SERVER_AUTO.includes(r) : LOOK_RULES[look].includes(r))) : [];
  const p = table ? autoParams(view.auto, table) : null;
  const s = look === "series" ? kit.cardSeries(shown) : null;
  const vtype = view.type || "";
  const right = s ? (vtype === "combo" ? s.datasets.slice(1) : vtype === "" ? s.datasets.filter(d => d.axis === "right") : []) : [];
  const pct = !!p?.sync && right.length > 0 && percentDomain(right.flatMap(d => d.values)) !== null;  // 보조 축 범위 칸을 %로 읽는가(y1Domain과 같은 조건)
  const ordered = look === "series" && (card.kind === "chart" || card.kind === "metric") && !!card.source.body;
  const truncated = Array.isArray(shown.data?.values?.rest);  // 서버가 그룹을 잘랐다(autoEnv와 같은 판정)
  // 규칙 하나를 바꾼다: undefined면 키를 지워 기본 켬, false면 끔, 객체면 수치(비면 키를 지움). auto가 비면 view에서 지운다
  const putAuto = (rule: AutoRule, next: false | Record<string, number | string | string[]> | undefined) => {
    const auto: AutoView = { ...view.auto };
    if (next === undefined || (next !== false && !Object.keys(next).length)) delete auto[rule]; else auto[rule] = next;
    if (Object.keys(auto).length) setView({ auto }); else without("auto");
    setMsg(SERVER_AUTO.includes(rule) && (look === "hist" || look === "box" || look === "points") ? RECOMPUTE : "");
  };
  // 수치 하나: 기본값과 같으면 그 키를 지운다(목록은 글자로 비교, 고를 값 차례로 만들므로 같은 차례)
  const setParam = (rule: AutoRule, name: string, value: number | string | string[]) => {
    const obj = { ...(view.auto?.[rule] || {}) };
    if (String(value) === String(table![rule][name][0])) delete obj[name]; else obj[name] = value;
    putAuto(rule, obj);
  };
  // 켜진 규칙의 수치 칸 하나(ParamSpec 모양별: 숫자·여러 개 고르기·자유 글자·하나 고르기)
  const ruleField = (rule: AutoRule, name: string, spec: ParamSpec): ReactNode => {
    const key = `${rule}.${name}`, label = PARAM_NAMES[key], aria = `${consts.dash.auto_names[rule]} ${label}`, now = p![rule]![name];
    if (spec.length === 3) {
      const k = PCT_PARAMS.includes(key) ? PCT : 1, show = (x: number) => +(x * k).toPrecision(PCT_PRECISION);
      return (
        <label key={key} className="hint bset">{label}<input type="number" step="any" aria-label={aria} min={show(spec[1])} max={show(spec[2])} style={{ width: 70 }} key={String(now)} defaultValue={show(now as number)}
          onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }}
          onBlur={e => { const t = e.target.value; const v = t === "" ? spec[0] : Math.min(spec[2], Math.max(spec[1], +t / k)); if (v !== now) setParam(rule, name, v); else e.target.value = String(show(v)); }} />{k === PCT ? "%" : ""}</label>
      );
    }
    if (Array.isArray(spec[0])) {
      const list = now as string[];
      return (
        <Fragment key={key}>
          {(spec[1] as string[]).map(c => (
            <label key={`${key}.${c}`} className="hint bset">{CHOICE_NAMES[c]}<input type="checkbox" aria-label={`${aria} ${CHOICE_NAMES[c]}`} checked={list.includes(c)}
              onChange={e => setParam(rule, name, (spec[1] as string[]).filter(x => (x === c ? e.target.checked : list.includes(x))))} /></label>
          ))}
        </Fragment>
      );
    }
    if (spec[1] === null) return s && s.datasets.length >= 2 ? (
      <label key={key} className="hint bset">{label}
        <select className="bunit" style={{ flexBasis: "auto" }} aria-label={aria} value={now as string} onChange={e => setParam(rule, name, e.target.value)}>
          <option value={spec[0] as string}>합계</option>
          {s.datasets.map(d => <option key={d.label} value={d.label}>{d.label}</option>)}
        </select>
      </label>
    ) : null;
    return (
      <label key={key} className="hint bset">{label}
        <select className="bunit" style={{ flexBasis: "auto" }} aria-label={aria} value={now as string} onChange={e => setParam(rule, name, e.target.value)}>
          {(spec[1] as string[]).map(c => <option key={c} value={c}>{CHOICE_NAMES[c]}</option>)}
        </select>
      </label>
    );
  };
  const rangeInput = (a: "y" | "y1", k: "min" | "max"): ReactNode => {
    const name = `${SIDES[a]} 축 ${k === "min" ? "최소" : "최대"}`;
    return (
      <label key={`${a}.${k}`} className="hint bset">{name}<input type="number" step="any" name={`${a}.${k}`} aria-label={name} style={{ width: 70 }} key={String(view.ranges?.[a]?.[k] ?? "")} defaultValue={view.ranges?.[a]?.[k] ?? ""}
        onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }} />{a === "y1" && pct ? "%" : ""}</label>
    );
  };
  // 줄의 네 칸을 한꺼번에 읽는다(쿼리 화면처럼 칸 하나만 보면 틀린 최소를 고칠 때 다른 칸 값이 사라진다)
  const saveRanges = (box: HTMLElement) => {
    const read = (a: "y" | "y1", k: "min" | "max") => { const i = box.querySelector<HTMLInputElement>(`input[name="${a}.${k}"]`); return i ? (i.value === "" ? undefined : +i.value) : view.ranges?.[a]?.[k]; };  // 숨은 칸(오른쪽 계열 없음)은 저장값 그대로
    const pick = (a: "y" | "y1") => Object.fromEntries((["min", "max"] as const).flatMap(k => { const v = read(a, k); return v == null ? [] : [[k, v]]; })) as { min?: number; max?: number };
    const next = { y: pick("y"), y1: pick("y1") };
    const err = (["y", "y1"] as const).filter(a => { const r = next[a]; return r.min != null && r.max != null && r.min >= r.max; }).map(a => `${SIDES[a]} 축의 최솟값을 최댓값보다 작게 넣어 주세요.`).join(" ");
    setRangeErr(err);
    if (err) return;  // 틀리면 저장하지 않는다(칸의 글자는 그대로)
    if (!Object.keys(next.y).length && !Object.keys(next.y1).length) { if (view.ranges) without("ranges"); return; }
    if (JSON.stringify(next) !== JSON.stringify(view.ranges)) setView({ ranges: next });
  };
  return (
    <div className="bextra">
      {card.kind === "chart" && (
        <div className="btools" role="group" aria-label="차트 종류">
          {Object.entries(consts.dash.types).map(typeBtn)}
          {(pointy || shown.data?.x_kind === "num") && (
            <label className="hint bset">버블 크기
              <select className="bunit" aria-label="버블 크기" title="버블 크기로 쓸 숫자 컬럼" value={view.points?.size ?? ""} onChange={e => tryPoints({ points: { size: e.target.value } })}>
                <option value="">크기 없음</option>
                {(shown.data?.num_cols ?? []).map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
          )}
          {pointy && ([["trend", "추세선"], ["show_r", "r"]] as const).map(([k, name]) => (
            <button key={k} type="button" className={`btype${view[k] ? " on" : ""}`} aria-pressed={!!view[k]} title={k === "show_r" ? "상관계수 r 보이기" : "추세선 보이기"} onClick={() => setView({ [k]: !view[k] })}>{name}</button>
          ))}
          {ROUND_TYPES.includes(view.type) && (
            <button type="button" className={`btype${view.rings ? " on" : ""}`} aria-pressed={!!view.rings} title="계열마다 고리 하나씩 겹쳐 그려요(안쪽이 첫 계열)" onClick={() => setView({ rings: !view.rings })}>겹친 고리</button>
          )}
          {!byUnit && dateSel}
          {byUnit && (
            <label className="hint bset">시간 단위
              <select className="bunit" aria-label="시간 단위" title="시간 단위. 일 값을 묶어서 다시 그려요" value={view.unit || "day"} onChange={e => setView({ unit: e.target.value as CardView["unit"] })}>
                {Object.entries(consts.dash.units).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
              </select>
            </label>
          )}
        </div>
      )}
      {card.kind !== "chart" && card.kind !== "analysis" && (
        <div className="btools">
          {withTarget && (target?.name ? <span className="hint">목표: {target.name}{targetErr ? ` (${targetErr})` : ""}</span> : (
            <label className="hint bset">목표
              <input type="number" aria-label="목표" style={{ width: 90 }} key={String(target?.value ?? "")} defaultValue={target?.value ?? ""}
                onBlur={e => setTarget(e.target.value)} onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }} />
            </label>
          ))}
          {dateSel}
          {(!PLAIN.includes(card.kind) || card.kind === "geomap" || card.kind === "calheat") && (
            <label className="hint bset">표시 형식<select className="bunit" aria-label="표시 형식" value={view.format ?? "number"} onChange={e => setView({ format: e.target.value as CardView["format"] })}>
                {FORMATS.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
              </select>
            </label>
          )}
          {kindFields}
          {unitSel}
          {unitText && <label className="hint bset">단위<input aria-label="단위" placeholder="단위" maxLength={10} style={{ width: 80, height: 24, fontSize: 12 }} value={view.unit_text ?? ""} onChange={e => setView({ unit_text: e.target.value })} /></label>}
        </div>
      )}
      <GroupPicker card={card} data={shown.data ?? {}} onChange={onChange} recompute={recompute} />
      {look && (<>
        <div className="btools">
          {rules.length > 0 && <button type="button" className={`btype${autoOpen ? " on" : ""}`} aria-expanded={autoOpen} title="그림을 그릴 때 알아서 하는 정렬·묶기·축 정리예요" onClick={() => setAutoOpen(!autoOpen)}>자동 규칙</button>}
          {look === "series" && (
            <label className="hint bset">값 축<select className="bunit" style={{ flexBasis: "auto" }} aria-label="값 축" value={view.scale ?? ""} onChange={e => (e.target.value ? setView({ scale: e.target.value as AxisScale }) : without("scale"))}>
                {SCALE_NAMES.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
              </select>
            </label>
          )}
          {ordered && (
            <label className="hint bset">정렬<select className="bunit" style={{ flexBasis: "auto" }} aria-label="정렬" value={card.source.body?.order ?? LEGACY_ORDER}
                onChange={e => { onChange({ source: { ...card.source, body: { ...card.source.body, order: e.target.value as Order } } }); setMsg(truncated ? ORDER_REFETCH : ""); }}>
                {consts.dash.orders.map(o => <option key={o} value={o}>{consts.dash.order_names[o]}</option>)}
              </select>
            </label>
          )}
          {look !== "ana" && (
            <label className="hint bset">모든 데이터
              <input type="checkbox" aria-label="모든 데이터" checked={view.all_data === true} onChange={e => { if (e.target.checked) setView({ all_data: true }); else without("all_data"); setMsg(look === "hist" || look === "points" ? RECOMPUTE : ""); }} />
            </label>
          )}
          {CHART_KINDS.includes(card.kind) && (
            <label className="hint bset">표로 보기
              <input type="checkbox" aria-label="표로 보기" checked={view.data_table === true} onChange={e => (e.target.checked ? setView({ data_table: true }) : without("data_table"))} />
            </label>
          )}
        </div>
        {autoOpen && rules.map(r => {
          const on = view.auto?.[r] !== false, name = consts.dash.auto_names[r];
          return (
            <div className="btools" key={r}>
              <label className="hint bset">{name}<input type="checkbox" aria-label={name} checked={on} onChange={e => putAuto(r, e.target.checked ? undefined : false)} /></label>
              {on && Object.entries(table![r]).map(([k, spec]) => ruleField(r, k, spec))}
            </div>
          );
        })}
        {(look === "series" || look === "points") && (
          <div className="btools" onBlur={e => saveRanges(e.currentTarget)}>
            {rangeInput("y", "min")}{rangeInput("y", "max")}
            {right.length > 0 && <>{rangeInput("y1", "min")}{rangeInput("y1", "max")}</>}
            <span className="err">{rangeErr}</span>
          </div>
        )}
      </>)}
      {timed && (
        <div className="btools">
          {byUnit.day && (
            <label className="hint bset">비교
              <select className="bunit" style={{ flexBasis: "auto" }} aria-label="비교 방식" value={view.compare?.mode ?? "span"} onChange={e => setView({ compare: { mode: e.target.value as "span" | "point" } })}>
                <option value="span">기간 비교</option><option value="point">시점 비교</option>
              </select>
            </label>
          )}
          <label className="hint bset">좋은 방향
            <select className="bunit" style={{ flexBasis: "auto" }} aria-label="좋은 방향" value={view.better ?? "up"} onChange={e => setView({ better: e.target.value as "up" | "down" })}>
              <option value="up">늘수록 좋음</option><option value="down">줄수록 좋음</option>
            </select>
          </label>
        </div>
      )}
      {hasRules && <RulesEditor view={view} columns={columns} onChange={p => setView(p)} />}
      <div className="btools">
        <button type="button" className={`btype${paint ? " on" : ""}`} aria-expanded={paint} title="계열·조각마다 색을 정해요" onClick={() => setPaint(!paint)}>색 바꾸기</button>
      </div>
      {paint && <ColorEditor card={card} shown={shown} theme={theme.series} palette={state.dash?.palette ?? []} kit={kit} onChange={onChange} />}
      <div className="hint btype-msg" role="status">{msg}</div>
    </div>
  );
}

// 이름마다 HEX 칸(옛 colorEditor). 틀린 값은 칸 옆에 이유를 보이고 이전 색을 그대로 둔다
function ColorEditor({ card, shown, theme, palette, kit, onChange }: { card: Card; shown: Card; theme: string[]; palette: string[]; kit: ReturnType<typeof seriesKit>; onChange: (patch: Partial<Card>) => void }) {
  const [errs, setErrs] = useState<Record<string, string>>({});
  const colors = { palette: palette.length ? palette : undefined, series: theme }, { group, names } = kit.colorNames(shown);
  return (
    <div className="bcolors">
      {names.map((name, i) => {
        // 지도 카드: 정하지 않은 영역은 모두 바탕 색(값에 따라 진하기만 다름)
        const now = card.kind === "geomap" && !card.colors?.[group]?.[name] ? (card.view.geo?.color as string | undefined) ?? colors.palette?.[0] ?? colors.series[0]
          : card.kind === "funnel" ? funnelColors(card, colors.palette)[name] : pickColor(shown, group, name, colors, i);  // 퍼널은 그림과 같은 고정 색
        return (
          <div className="brow" key={name + now}>
            <i className="swatch" style={{ background: now }} />
            <span className="bname">{name}</span>
            <input defaultValue={now} title="색 코드. 예: #11706f" aria-label={`${name} 색`} onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }} onBlur={e => {
              const v = e.target.value.trim();
              if (v === now) return;
              if (!HEX.test(v)) { e.target.value = now; return setErrs({ ...errs, [name]: `‘${v}’은 색 코드가 아니에요. #11706f처럼 적어 주세요.` }); }
              setErrs({ ...errs, [name]: "" });
              onChange({ colors: { ...card.colors, [group]: { ...card.colors?.[group], [name]: v } } });
            }} />
            <span className="err">{errs[name]}</span>
          </div>
        );
      })}
      <button type="button" className="ghost" title="이 카드에서 따로 정한 색을 지우고 대시보드 색으로 돌아가요" onClick={() => onChange({ colors: { ...card.colors, series: {}, parts: {}, order: [], parts_order: [] } })}>지정 색 지우기</button>
    </div>
  );
}

// 워드클라우드 방식 칸(카드 추가 메뉴와 카드 설정이 같이 쓴다): 방식·보일 태그·뽑을 행(기본값 없음)·맞춤(기본값 없음)·글 컬럼, full이면 품사·불용어·최소 글자 수.
// 태그 사전과 글 컬럼 기본값은 /api/labels. 빈 칸은 cloud에서 지운다(서버 check_cloud가 나머지를 검사)
export function CloudFields({ cloud, schema, table, texts, full, tagged, onChange }: {
  cloud: Cloud; schema: string; table: string; texts: string[]; full: boolean; tagged: boolean; onChange: (cloud: Cloud) => void;
}) {
  const [info, setInfo] = useState<{ vocab: string[]; text: string } | null>(null);
  const [err, setErr] = useState("");
  const [word, setWord] = useState("");
  useEffect(() => {
    if (!table) return;
    let live = true;
    fetchLabels(schema, table).then(r => { if (live) { setInfo({ vocab: r.vocab ?? [], text: r.text ?? "" }); setErr(""); } }).catch(e => live && setErr((e as Error).message));
    return () => { live = false; };
  }, [schema, table]);
  const put = (p: Partial<Cloud>) => {
    const next = { ...cloud, ...p } as Record<string, unknown>;
    for (const k of Object.keys(next)) if (next[k] === undefined || (Array.isArray(next[k]) && !(next[k] as unknown[]).length)) delete next[k];
    onChange(next as Cloud);
  };
  const setMode = (mode: Cloud["mode"]) => mode === "values" ? onChange({ mode })
    : mode === "labels" ? put({ mode, rows: undefined, match: undefined, text: undefined, pos: undefined, stop: undefined, min_len: undefined })
    : put({ mode, text: cloud.text || info?.text || undefined });  // 사전의 글 컬럼을 먼저 고른 상태로
  const tags = cloud.tags ?? [], stop = cloud.stop ?? [];
  const pos = cloud.pos?.length ? cloud.pos : [DEFAULT_POS];
  const words = cloud.mode === "words", pickTags = cloud.mode === "labels" || (words && cloud.rows === "tagged");
  const addStop = () => { const w = word.trim(); if (w && !stop.includes(w)) put({ stop: [...stop, w] }); setWord(""); };
  return (<>
    <select className="bunit" style={{ flexBasis: "auto" }} aria-label="워드클라우드 방식" value={cloud.mode} onChange={e => setMode(e.target.value as Cloud["mode"])}>
      {CLOUD_MODES.map(([v, n]) => <option key={v} value={v}>방식: {n}</option>)}
    </select>
    {words && (
      <select className="bunit" style={{ flexBasis: "auto" }} aria-label="단어를 뽑을 행" value={cloud.rows ?? ""}
        onChange={e => put(e.target.value === "all" ? { rows: "all", tags: undefined, match: undefined } : { rows: (e.target.value || undefined) as Cloud["rows"] })}>
        <option value="">단어를 뽑을 행: (고르세요)</option>
        {CLOUD_ROWS.map(([v, n]) => <option key={v} value={v}>단어를 뽑을 행: {n}</option>)}
      </select>
    )}
    {pickTags && (info?.vocab ?? []).map(t => (
      <Check key={t} label={t} checked={tags.includes(t)} onChange={on => put({ tags: (info?.vocab ?? []).filter(x => (x === t ? on : tags.includes(x))) })} />
    ))}
    {words && cloud.rows === "tagged" && (
      <select className="bunit" style={{ flexBasis: "auto" }} aria-label="태그 맞춤" value={cloud.match ?? ""} onChange={e => put({ match: (e.target.value || undefined) as Cloud["match"] })}>
        <option value="">태그: (고르세요)</option>
        {CLOUD_MATCH.map(([v, n]) => <option key={v} value={v}>태그: {n}</option>)}
      </select>
    )}
    {words && (
      <select className="bunit" style={{ flexBasis: "auto" }} aria-label="글 컬럼" value={cloud.text ?? ""} onChange={e => put({ text: e.target.value || undefined })}>
        <option value="">글 컬럼: (고르세요)</option>
        {texts.map(c => <option key={c} value={c}>글 컬럼: {c}</option>)}
      </select>
    )}
    {words && full && (<>
      {CLOUD_POS.map(([v, n]) => (
        <Check key={v} label={n} checked={pos.includes(v)} onChange={on => put({ pos: CLOUD_POS.map(([p]) => p).filter(p => (p === v ? on : pos.includes(p))) })} />
      ))}
      <span className="hint bset">불용어
        {stop.map(s => <span key={s} className="chip">{s}<button type="button" className="ghost" aria-label={`${s} 빼기`} onClick={() => put({ stop: stop.filter(x => x !== s) })}>×</button></span>)}
        <input aria-label="불용어" placeholder="낱말" style={{ width: 80 }} value={word} onChange={e => setWord(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.nativeEvent.isComposing) addStop(); }} />
        <button type="button" className="ghost" onClick={addStop}>더하기</button>
      </span>
      <label className="hint bset">최소 글자 수
        <input type="number" aria-label="최소 글자 수" min={1} step={1} placeholder="기본" style={{ width: 50 }} key={String(cloud.min_len ?? "")} defaultValue={cloud.min_len ?? ""}
          onBlur={e => { const v = e.target.value === "" ? undefined : Math.round(+e.target.value); if (v !== cloud.min_len) put({ min_len: v }); }}
          onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }} />
      </label>
    </>)}
    {pickTags && !tagged && <span className="hint">쿼리 화면에서 태그를 켠 검색으로 카드를 담으세요</span>}
    <span className="err">{err}</span>
  </>);
}
