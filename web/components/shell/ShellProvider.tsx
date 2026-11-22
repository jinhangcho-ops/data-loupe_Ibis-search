"use client";
// N4 — 화면 틀의 공용 상태(옛 dashboard.html 전역 + dash_ctl.js DashCtl + dash.js DashBoard.setRange·recompare + dash_ctl.js liveCards·recomputeRange).
// 지금 연결(live_min의 source), 대시보드 열기(저장 → 읽기), 기간·기준일·주기·실시간 칸, 기간으로 다시 구하기(연결됐을 때만), 비교 문장, 다시 구하기, 대시보드만 보기.
// 기간 적용은 "open" 액션을 보고 한다(누가 열어도 같은 규칙). 구한 값은 dispatch({type:"values"}), 다시 구하지 못한 이유는 결과의 error(이전 값은 그대로 둔다).
// 비교 문장은 store에 자리가 없어 여기 둔다: 카드는 useCompare(card.id)({text, tone, delta_text})를 읽는다.
// 열기는 이 open 한 곳(저장값을 먼저 보이고, 연결돼 있으면 기간 규칙대로 다시 구한다). 자동 저장(useAutosave)·되돌리기(useUndo)도 여기서 한 번만 부른다.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { CHART_KINDS } from "@/components/cards/ChartCard";
import { datedCard, isShell } from "@/components/edit/menu/kinds";
import { isFrame } from "@/components/frame/registry";
import { flush, useAutosave } from "@/components/hooks/useAutosave";
import { useUndo, type Undo } from "@/components/hooks/useUndo";
import { cardRequest, compare, computeCard, computeCards, getDashboard, getLiveMin, parseDay, parsePeriod, saveSettings, setLast } from "@/lib/api";
import { outsideWindow } from "@/lib/calc";
import { useConstants } from "@/lib/constants";
import { everySeconds, pullOf } from "@/lib/pull";
import { computedUpdate, sameContent, useActionLog, useDash } from "@/lib/store";
import { compareItem, newOrder, sameSource } from "@/lib/series";
import { fieldsFor, filtersFor, linkTargets, type ElemState, type Press, type SeriesView } from "@/lib/links";
import { seriesKit } from "@/components/cards/chart/series";
import type { Card, CompareMode, ComputeResult, Ctx, DashSettings, Dashboard, PickedFilter, Source, Unit } from "@/lib/types";

export type Note = { text: string; bad?: boolean };
export type Said = { text: string; tone: string; delta_text?: string; [more: string]: unknown };
type Fields = { as_of: string; period: string };
type NoteKey = "asof" | "per";
export type BellItem = { key: string; text: string };

export type Period = {
  inputs: Fields; setInput: (k: keyof Fields, v: string) => void;
  notes: Partial<Record<NoteKey, Note>>; today: string; past: boolean; settingsError: string;
  compareMode: CompareMode; setCompareMode: (m: CompareMode) => void;
  commitDay: (text?: string) => void; commitPeriod: (text?: string) => void;
};
export type Shell = {
  conn: Source; loadConn: () => Promise<void>; dbMin: number;  // dbMin: 이 연결에서 DB 계산 갱신 주기의 최소(초)
  open: (id: string) => Promise<void>; flush: () => Promise<void>;
  compares: Record<string, Said>; recompute: (fresh?: boolean) => Promise<void>; period: Period;  // fresh가 false면 갱신 표 없이 구한다
  settings: boolean; setSettings: (open: boolean) => void;  // 대시보드 설정 창 열림
  bell: BellItem[]; setBell: (key: string, text: string) => void;  // 종에 담을 화면 쪽 소식(text가 ""면 뺀다, 저장하지 않는다)
  addOpen: boolean; setAddOpen: (on: boolean) => void; gallery: boolean; setGallery: (on: boolean) => void; dashOnly: boolean; setDashOnly: (on: boolean) => void;
  saving: boolean; undo: Undo;
  build: boolean;  // 만드는 곳(/dashboard/build)이면 true, 보는 곳(/dashboard)이면 false
  page: string; setPage: (name: string) => void;  // 지금 보는 쪽(탭) 이름. 쪽 목록에 없으면 첫 쪽으로 본다(저장하지 않는다)
  picks: PickedFilter[]; setFilter: (column: string, values: string[]) => Promise<void>;  // 필터 칸에서 고른 값, 고르기(빈 목록이면 풀기)
  report: boolean; setReport: (open: boolean) => void;  // 그림·보고서 창 열림(저장하지 않는다)
  zoom: string | null; setZoom: (id: string | null) => void;  // 크게 보는 카드 id
  presses: Press[]; press: (from: string, next: Omit<Press, "from"> | null) => Promise<void>;  // (B4) 보낸 쪽마다 누른 것 하나(저장하지 않는다)
  elems: Record<string, ElemState>; elemPeriod: (id: string, field: "period" | "as_of", text: string) => Promise<void>; setElemUnit: (id: string, unit: Unit | null) => void; setCell: (id: string, cell: number) => Promise<void>;  // (B4) 요소마다의 값
  ctxOf: (id: string) => Ctx | null; unitOf: (id: string) => Unit | null; viewOf: (id: string) => SeriesView | null;  // (B4) 카드마다
  busy: string[];  // (B2) 값을 구하는 요청이 나가 있는 카드 id
  clearFilters: () => Promise<void>;  // 걸린 필터를 모두 푼다
};

/** 만드는 곳·보는 곳 주소(지금 연 대시보드를 그대로 열게 ?open=id) */
export const BUILD_PATH = "/dashboard/build", VIEW_PATH = "/dashboard";
const withOpen = (path: string, id?: string) => path + (id ? "?open=" + encodeURIComponent(id) : "");
export const buildHref = (id?: string) => withOpen(BUILD_PATH, id);
export const viewHref = (id?: string) => withOpen(VIEW_PATH, id);

const Ctx_ = createContext<Shell | null>(null);
/** 화면 틀 상태(ShellProvider 안에서만) */
export function useShell() {
  const found = useContext(Ctx_);
  if (!found) throw new Error("ShellProvider 안에서만 쓸 수 있어요.");
  return found;
}
/** 카드의 비교 문장(없으면 undefined). 화면 틀 밖(그림 복사 등)에서도 던지지 않는다 */
export const useCompare = (id: string) => useContext(Ctx_)?.compares[id];

// 카드의 입력 칸에 커서가 있는지(옛 typing): 실시간 갱신이 다시 그리면 쓰던 글이 지워지므로 그 회는 미룬다
const typing = () => { const a = document.activeElement as HTMLElement | null; return !!a && !!a.closest(".board") && (/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) || a.isContentEditable); };
const EMPTY: Fields = { as_of: "", period: "" };
const spanKey = (p: string, a: string, m: CompareMode): string => JSON.stringify([p.trim(), a, m]);
/** 대시보드를 열 때 처음 눌린 것: 값으로 거르기 세그먼트의 첫 칸(값이 있을 때) */
const seeds = (cards: Card[]): Press[] => cards.flatMap((seg): Press[] => {
  const f = seg.view.frame;
  if (seg.kind !== "segment" || f?.mode !== "filter") return [];
  const v = f.cells?.[0]?.value;
  return v ? [{ from: seg.id, value: { column: f.column ?? "", in: [v] } }] : [];
});
/** 저장된 기간·기준일·단위를 가진 기간 요소의 처음 값(카드 차례가 뒤일수록 나중에 바꾼 것) */
const ELEM_KINDS: readonly string[] = ["period_pick", "asof_field", "unit_seg", "top_strip"];
const seedElems = (cards: Card[]): Record<string, ElemState> => Object.fromEntries(cards.flatMap((c, i): [string, ElemState][] => {
  const f = c.view.frame;
  if (!f || !ELEM_KINDS.includes(c.kind) || !("period" in f || "as_of" in f || "unit" in f)) return [];
  return [[c.id, { at: i + 1, ...("period" in f ? { period: f.period } : {}), ...("as_of" in f ? { as_of: f.as_of } : {}), ...("unit" in f ? { unit: f.unit === "" ? null : f.unit } : {}) }]];
}));
const OFFLINE_NOTE = "연결이 없어 받아 둔 데이터로 보고 있어요.";

export function ShellProvider({ children, build }: { children: React.ReactNode; build: boolean }) {
  const { state, dispatch } = useDash();
  const consts = useConstants();
  const now = useRef(state);
  useEffect(() => { now.current = state; });
  const [conn, setConn] = useState<Source>(null);
  const connRef = useRef<Source>(null);
  const [compares, setCompares] = useState<Record<string, Said>>({});
  const [addOpen, setAddOpen] = useState(false);
  const [gallery, setGallery] = useState(false);
  const [dashOnly, setDashOnly] = useState(false);
  const [page, setPage] = useState("");
  const [picks, setPicks] = useState<PickedFilter[]>([]);  // 보는 사람이 필터 칸에서 고른 값(저장하지 않는다)
  const [report, setReport] = useState(false);
  const [zoom, setZoom] = useState<string | null>(null);
  const [settings, setSettings] = useState(false);
  const [dbMin, setDbMin] = useState(0);
  const [bell, setBells] = useState<BellItem[]>([]);
  const setBell = useCallback((key: string, text: string) => setBells(b => [...b.filter(x => x.key !== key), ...(text ? [{ key, text }] : [])]), []);
  const [busy, setBusy] = useState<string[]>([]);
  const hold = useCallback((ids: string[], on: boolean) => setBusy(b => (on ? [...new Set([...b, ...ids])] : b.filter(id => !ids.includes(id)))), []);
  const picked = useRef<PickedFilter[]>([]);
  const [presses, setPresses] = useState<Press[]>([]);
  const pressed = useRef<Press[]>([]);
  const [elems, setElems] = useState<Record<string, ElemState>>({});
  const elemRef = useRef<Record<string, ElemState>>({});
  const base = useRef<Ctx | null>(null);  // 저장된 기간·기준일의 ctx(필터 없이)
  const spans = useRef(new Map<string, Ctx | null>());  // (기간 글, 기준일, 비교 기준) → ctx
  const atSeq = useRef(0);
  // ---------- 기간 칸(DashCtl) ----------
  const [inputs, setInputs] = useState<Fields>(EMPTY);
  const saved = useRef<Fields>({ ...EMPTY });  // 확정된 값(틀리면 이 값으로 되돌린다)
  const [notes, setNotes] = useState<Partial<Record<NoteKey, Note>>>({});
  const [today, setToday] = useState("");
  const todayRef = useRef<Promise<string> | null>(null);
  const [past, setPast] = useState(false);
  const [settingsError, setSettingsError] = useState("");
  const [compareMode, setMode] = useState<CompareMode>("prev");
  const mode = useRef<CompareMode>("prev");  // 확정된 비교 기준(대시보드 설정 compare_mode)
  const seqs = useRef({ apply: 0, range: 0, cmp: 0, open: 0, hadRange: false, cmpKey: "", asOf: "" });
  const rangeCall = useRef(new Map<string, number>());

  const note = useCallback((k: NoteKey, text: string, bad = false) => setNotes(n => ({ ...n, [k]: { text, bad } })), []);
  const setInput = useCallback((k: keyof Fields, v: string) => setInputs(i => ({ ...i, [k]: v })), []);
  const ready = useCallback(() => todayRef.current ??= parseDay("").then(d => { setToday(d.date ?? ""); return d.date ?? ""; }), []);

  // 지금 연결 열쇠와 DB 계산 갱신 주기의 최소(옛 loadMin·showConn)
  const loadConn = useCallback(async () => {
    const d = await getLiveMin();
    connRef.current = d.source;
    setConn(d.source);
    setDbMin(d.seconds);
  }, []);

  const merge = useMemo(() => consts?.merge ?? [], [consts]);
  // 지금 보이는 값을 단 카드(구한 값이 있으면 그것)
  const shown = useCallback((c: Card): Card => ({ ...c, data: now.current.values[c.id]?.data ?? c.data }), []);
  // 구한 결과를 넣는다: 실패한 카드는 이전 값을 두고 이유만(옛 rangeNote)
  const putResults = useCallback((results: ComputeResult[]) => {
    const vals = now.current.values;
    dispatch({ type: "values", results: results.map(r => r.data ? r : { id: r.id, data: vals[r.id]?.data, error: r.error }) });
  }, [dispatch]);

  // 카드마다의 ctx: 저장된 기간(또는 링크된 요소의 기간·기준일) + 고른 필터 + 누른 것
  const ctxOf = useCallback((id: string): Ctx | null => {
    const cards = now.current.dash?.cards ?? [], f = fieldsFor(id, cards, elemRef.current);
    const b = f.period === undefined && f.as_of === undefined ? base.current : spans.current.get(spanKey(f.period ?? saved.current.period, f.as_of ?? saved.current.as_of, mode.current)) ?? base.current;
    const filters = [...picked.current, ...filtersFor(id, pressed.current, cards)];
    return !b && !filters.length ? null : { ...(b ?? {}), ...(filters.length ? { filters } : {}) };
  }, []);
  const unitOf = useCallback((id: string): Unit | null => fieldsFor(id, now.current.dash?.cards ?? [], elemRef.current).unit ?? null, []);
  // 이 카드를 상대로 하는 보기 바꾸기 세그먼트 가운데 가장 나중에 바꾼 것의 칸
  const viewOf = useCallback((id: string): SeriesView | null => {
    const cards = now.current.dash?.cards ?? [];
    const segs = cards.filter(c => c.kind === "segment" && (c.view.frame?.mode ?? "view") === "view" && linkTargets(c, cards)[0] === id);
    const seg = segs.reduce<Card | null>((best, c) => (best && (elemRef.current[best.id]?.at ?? 0) >= (elemRef.current[c.id]?.at ?? 0) ? best : c), null);
    if (!seg) return null;
    const cell = seg.view.frame?.cells?.[elemRef.current[seg.id]?.cell ?? 0];
    return cell ? { series: cell.series, share: cell.share } : null;
  }, []);

  // 기간 기준 날짜 컬럼이 있는 카드 등을 보는 기간으로 다시 구한다(옛 recomputeRange). 연결이 없거나 다른 연결의 카드는 이유만 적는다.
  const recomputeRange = useCallback(async (cards: Card[]) => {
    const call = ++seqs.current.range, key = connRef.current?.key ?? "", dash = now.current.dash;
    const pc = !!consts && !!dash && pullOf(dash, consts.dash.pull).calc === "pc";  // PC 방식이면 연결이 없어도 보낸다(서버가 받아 둔 파일로 구하거나 카드마다 이유를 준다)
    const notes: ComputeResult[] = [], todo: Card[] = [];
    cards.forEach(c => {
      rangeCall.current.set(c.id, call);
      const why = !connRef.current && !pc ? "연결하면 이 기간으로 다시 구해요" : c.source?.key && key && !sameSource(c.source.key, key) ? "다른 연결의 카드라 이 기간으로 구하지 못했어요" : "";
      if (why) notes.push({ id: c.id, error: why }); else if (c.id) todo.push(c);
    });
    if (notes.length) putResults(notes);
    if (!todo.length) return;
    const mine = (r: ComputeResult) => rangeCall.current.get(r.id) === call;  // 기간을 연달아 바꾸면 카드마다 가장 최근 요청의 것만
    const groups = new Map<string, Card[]>();  // 같은 ctx끼리 한 번에(카드 차례 유지)
    todo.forEach(c => { const k = JSON.stringify(ctxOf(c.id)); groups.set(k, [...(groups.get(k) ?? []), c]); });
    for (const group of groups.values()) {  // 연결은 하나라 묶음끼리는 차례로
      hold(group.map(c => c.id), true);
      try { putResults((await computeCards(group.map(cardRequest), ctxOf(group[0].id), dash?.id ?? "")).results.filter(mine)); }
      catch (e) { putResults(group.filter(c => rangeCall.current.get(c.id) === call).map(c => ({ id: c.id, error: (e as Error).message }))); }
      finally { hold(group.filter(c => rangeCall.current.get(c.id) === call).map(c => c.id), false); }  // 더 새 요청이 나간 카드는 그 요청이 끝날 때 푼다
    }
  }, [putResults, consts, hold, ctxOf]);

  // (B9) 대시보드를 연 뒤(열린 대시보드 id가 바뀔 때마다 한 번) 표시 칸이 있는 목록을 다시 구한다
  const openedId = state.dash?.id ?? "";
  useEffect(() => {
    const d = now.current.dash;
    if (!d || d.id !== openedId) return;
    const pc = !!consts && pullOf(d, consts.dash.pull).calc === "pc";
    if (!connRef.current && !pc) return;  // 구할 수 없으면 저장본 그대로(안내 줄을 만들지 않는다)
    const marked = d.cards.filter(c => c.kind === "list" && (c.view.row_marks?.fields.length ?? 0) > 0);
    if (marked.length) recomputeRange(marked).catch(e => dispatch({ type: "error", message: (e as Error).message }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openedId]);

  // 보는 기간을 바꾼다(옛 setRange): ctx를 두고, 날짜 X축이 아닌데 기간 기준 날짜 컬럼이 있는 카드·고유 개수 카드·읽은 범위 밖 카드만 다시 구한다
  const setRange = useCallback(async (c: Ctx | null, asOf: string) => {
    const range = c?.range ?? null;
    const cm = mode.current === "prev" ? undefined : mode.current;  // 기간이 비어도 카드 자신의 기간 줄(서버 own_range)을 같은 비교 기준으로
    const span: Ctx | null = range || asOf || cm ? { range: range ?? undefined, prev: c?.prev ?? null, months: c?.months, as_of: asOf || undefined, compare_mode: cm } : null;  // 필터 없이(지역 이름이 ref base와 겹쳐 span)
    const ctx = picked.current.length ? { ...span, filters: picked.current } : span;  // 고른 필터는 기간을 바꿔도 남는다
    base.current = span;
    dispatch({ type: "ctx", ctx });
    const was = seqs.current.hadRange;
    seqs.current.hadRange = !!range;
    const unique = (x: Card) => !CHART_KINDS.includes(x.kind) && !!x.data?.by_unit?.day?.series.some(s => !merge.includes(s.agg));
    const cards = now.current.dash?.cards ?? [];
    const stale = cards.map(shown).filter(x => (datedCard(x, range, was) || unique(x) || outsideWindow(x, range)) && fieldsFor(x.id, cards, elemRef.current).period === undefined && fieldsFor(x.id, cards, elemRef.current).as_of === undefined);
    if (stale.length) await recomputeRange(stale);
  }, [dispatch, merge, shown, recomputeRange]);

  // 누른 것(press)·요소 기간을 받는 카드들을 다시 구한다: 걸린 것이 없는 카드는 저장된 값을 보이고, 나머지만 구한다
  const refetch = useCallback(async (hit: Card[]) => {
    const none = hit.filter(c => !ctxOf(c.id)), rest = hit.filter(c => ctxOf(c.id));
    if (none.length) dispatch({ type: "values", results: none.map(c => ({ id: c.id })) });
    if (rest.length) await recomputeRange(rest);
  }, [dispatch, ctxOf, recomputeRange]);

  // 보낸 쪽(from)이 누른 것 하나를 바꾸고 그 쪽의 상대 카드를 다시 구한다
  const press = useCallback(async (from: string, next: Omit<Press, "from"> | null) => {
    const cards = now.current.dash?.cards ?? [], sender = cards.find(c => c.id === from);
    if (!sender) throw new Error("누른 카드가 대시보드에 없어요.");
    pressed.current = [...pressed.current.filter(p => p.from !== from), ...(next ? [{ from, ...next }] : [])];
    setPresses(pressed.current);
    const targets = linkTargets(sender, cards);
    await refetch(cards.filter(c => targets.includes(c.id) && !isFrame(c.kind) && !isShell(c)));
  }, [refetch]);

  const setElem = useCallback((id: string, next: ElemState) => { elemRef.current = { ...elemRef.current, [id]: next }; setElems(elemRef.current); }, []);

  // 기준일·기간 글을 읽어 그 읽기의 ctx를 낸다(setRange의 span 식과 같은 꼴)
  const readSpan = useCallback(async (asofText: string, perText: string) => {
    const today = await ready();
    const day = await parseDay(asofText);
    const parsed = !day.error && perText.trim() ? await parsePeriod(perText, day.date ?? undefined, true, mode.current) : null;
    const asOf = day.date !== today ? day.date ?? "" : "";
    const c = parsed?.context ?? null, cm = mode.current === "prev" ? undefined : mode.current;
    const ctx: Ctx | null = c?.range || asOf || cm ? { range: c?.range ?? undefined, prev: c?.prev ?? null, months: c?.months, as_of: asOf || undefined, compare_mode: cm } : null;
    return { day, parsed, asOf, ctx };
  }, [ready]);

  // 요소의 기간·기준일을 받는 카드마다 (기간, 기준일)의 ctx가 spans에 없으면 읽어 채운 뒤 그 카드들을 다시 구한다
  const spanFill = useCallback(async (cards: Card[]) => {
    const all = now.current.dash?.cards ?? [];
    for (const c of cards) {
      const f = fieldsFor(c.id, all, elemRef.current);
      if (f.period === undefined && f.as_of === undefined) continue;
      const p = f.period ?? saved.current.period, a = f.as_of ?? saved.current.as_of, key = spanKey(p, a, mode.current);
      if (spans.current.has(key)) continue;
      const { day, parsed, ctx } = await readSpan(a, p);
      if (!day.error && !parsed?.span.error) spans.current.set(key, ctx);
    }
    await recomputeRange(cards);
  }, [readSpan, recomputeRange]);

  // 기간·기준일 요소(id)가 고른 글: 읽어 요소 값으로 두고, 만드는 곳이면 요소의 저장 값도 고친다
  const elemPeriod = useCallback(async (id: string, field: "period" | "as_of", text: string) => {
    const cards = now.current.dash?.cards ?? [], el = cards.find(c => c.id === id);
    if (!el) throw new Error("요소가 대시보드에 없어요.");
    const e = elemRef.current[id] ?? { at: 0 };
    const asofText = field === "as_of" ? text : e.as_of ?? saved.current.as_of;
    const perText = field === "period" ? text : e.period ?? saved.current.period;
    const { day, parsed, asOf, ctx } = await readSpan(asofText, perText);
    if (day.error) return setElem(id, { ...e, notes: { ...e.notes, asof: { text: day.error, bad: true } } });
    if (parsed?.span.error) return setElem(id, { ...e, notes: { ...e.notes, per: { text: parsed.span.error, bad: true } } });
    spans.current.set(spanKey(perText, asOf, mode.current), ctx);
    const kept = field === "as_of" ? { as_of: asOf } : { period: perText.trim() };
    setElem(id, { ...e, ...kept, at: ++atSeq.current, notes: { asof: { text: day.label }, per: { text: parsed ? parsed.span.label : "기간을 걸지 않아요" } } });
    if (build) dispatch({ type: "card", id, patch: { view: { ...el.view, frame: { ...el.view.frame, ...kept } } } });  // 보는 곳에서는 저장하지 않는다
    const targets = linkTargets(el, cards);
    await spanFill(cards.filter(c => targets.includes(c.id) && !isFrame(c.kind) && !isShell(c)));
  }, [readSpan, setElem, spanFill, dispatch, build]);

  // 기간 단위 요소가 고른 단위(그리기만, 다시 구하지 않는다). 만드는 곳이면 요소의 저장 값도 고친다
  const setElemUnit = useCallback((id: string, unit: Unit | null) => {
    setElem(id, { ...(elemRef.current[id] ?? { at: 0 }), unit, at: ++atSeq.current });
    const el = (now.current.dash?.cards ?? []).find(c => c.id === id);
    if (build && el) dispatch({ type: "card", id, patch: { view: { ...el.view, frame: { ...el.view.frame, unit: unit ?? "" } } } });
  }, [setElem, dispatch, build]);

  // 세그먼트(id)의 칸 고르기: 값으로 거르기면 그 칸의 값을 누른 것으로 보낸다(빈 값이면 푼다)
  const setCell = useCallback(async (id: string, cell: number) => {
    const seg = (now.current.dash?.cards ?? []).find(c => c.id === id);
    if (!seg) throw new Error("세그먼트가 대시보드에 없어요.");
    setElem(id, { ...(elemRef.current[id] ?? { at: 0 }), cell, at: ++atSeq.current });
    const f = seg.view.frame;
    if (f?.mode !== "filter") return;
    const v = f.cells?.[cell]?.value ?? "";
    await press(id, v ? { value: { column: f.column ?? "", in: [v] } } : null);
  }, [setElem, press]);

  // 필터 칸에서 값을 고른다(values가 비면 그 필터를 푼다): ctx.filters를 바꾸고 모든 카드를 그 조건으로 다시 구해 화면에만 보인다(저장 값은 거르지 않은 값 그대로).
  // 필터가 다 풀리고 보는 기간도 없으면 다시 구하지 않고 화면 값을 비워 저장된 값을 보인다.
  const applyPicks = useCallback(async (next: PickedFilter[]) => {
    picked.current = next;
    setPicks(picked.current);
    const rest: Ctx = { ...now.current.ctx, filters: undefined };
    const ctx: Ctx | null = picked.current.length ? { ...rest, filters: picked.current } : Object.values(rest).some(v => v != null) ? rest : null;
    dispatch({ type: "ctx", ctx });
    const cards = (now.current.dash?.cards ?? []).filter(c => c.id && !isShell(c));
    if (cards.every(c => !ctxOf(c.id))) return dispatch({ type: "values", results: cards.map(c => ({ id: c.id })) });
    await recomputeRange(cards);
  }, [dispatch, recomputeRange, ctxOf]);
  const setFilter = useCallback((column: string, values: string[]) => applyPicks([...picked.current.filter(f => f.column !== column), ...(values.length ? [{ column, in: values }] : [])]), [applyPicks]);
  // 걸린 필터를 모두 푼다(걸린 것이 없으면 다시 구하지 않는다)
  // 값으로 거르기 세그먼트는 빈 값 칸이 있으면 그 칸으로 돌리고 누른 것을 풀며, 없으면 칸과 누른 것을 그대로 둔다. 그 밖의 누른 것은 모두 푼다
  const clearFilters = useCallback(async () => {
    const cards = now.current.dash?.cards ?? [], had = picked.current.length, before = pressed.current;
    const segs = cards.filter(c => c.kind === "segment" && c.view.frame?.mode === "filter");
    const blanks = new Map(segs.map(s => [s.id, (s.view.frame?.cells ?? []).findIndex(c => c.value === "")] as const));
    const kept = segs.filter(s => blanks.get(s.id)! < 0).map(s => s.id);
    picked.current = []; setPicks([]);
    pressed.current = before.filter(p => kept.includes(p.from));
    setPresses(pressed.current);
    elemRef.current = { ...elemRef.current, ...Object.fromEntries(segs.filter(s => blanks.get(s.id)! >= 0).map(s => [s.id, { ...(elemRef.current[s.id] ?? { at: 0 }), cell: blanks.get(s.id)! }])) };
    setElems(elemRef.current);
    if (!had && pressed.current.length === before.length) return;
    await refetch(cards.filter(c => c.id && !isFrame(c.kind) && !isShell(c)));
  }, [refetch]);

  // 설정 칸을 확정하면 그 항목만 저장한다. 거부되면 이유를 보이고 저장된 대시보드를 다시 연다
  const openRef = useRef<(id: string) => Promise<void>>(async () => {});
  const changed = useCallback(async () => {
    const id = now.current.dash?.id, f = saved.current;
    if (!id || !build) return;  // 보는 곳에서는 기간·기준일을 바꿔도 화면에만 적용한다(저장은 만드는 곳에서만)
    const fields: DashSettings = { as_of: f.as_of, period: f.period, compare_mode: mode.current };
    try { await saveSettings(id, fields); dispatch({ type: "meta", patch: fields }); setSettingsError(""); }
    catch (e) { setSettingsError((e as Error).message); await openRef.current(id); }
  }, [dispatch, build]);

  // 기준일과 기간 칸을 서버로 읽어 보는 기간·직전 기간을 정한다(옛 apply). 틀리면 이유를 보이고 이전 값으로 되돌린다
  const apply = useCallback(async (commit: boolean, asofText: string, perText: string) => {
    const today = await ready();
    const seq = ++seqs.current.apply;
    const day = await parseDay(asofText);
    if (seq !== seqs.current.apply) return;
    if (day.error) { setInput("as_of", saved.current.as_of); return note("asof", day.error, true); }
    const isPast = day.date !== today, blank = !perText.trim();
    const parsed = blank ? null : await parsePeriod(perText, day.date ?? undefined, true, mode.current);
    if (seq !== seqs.current.apply) return;
    if (parsed?.span.error) {
      if (!commit) {  // 저장본을 여는 중: 앞 대시보드의 기간·과거 표시가 남지 않게 기간 없이 본다
        note("asof", day.label);
        setPast(isPast);
        await setRange(null, isPast ? day.date ?? "" : "");
      }
      setInput("period", saved.current.period);
      return note("per", parsed.span.error, true);
    }
    const asOf = isPast ? day.date ?? "" : "";
    saved.current = { ...saved.current, as_of: asOf, period: perText.trim() };
    setInputs(i => ({ ...i, as_of: asOf, period: perText.trim() }));
    note("asof", day.label);
    note("per", parsed ? parsed.span.label + (parsed.span.days ? ` (${parsed.span.days}일)` : "") : "기간을 걸지 않아요. 카드마다 원래 조건대로 보여요");
    setPast(isPast);
    await setRange(parsed?.context ?? null, asOf);
    if (commit) await changed();
  }, [ready, note, setInput, setRange, changed]);

  // 대시보드가 열리면(누가 열었든) 칸을 채우고 기간을 적용한다(옛 ctl.set)
  useActionLog(a => {
    if (a.type !== "open") return;
    const d: Partial<Dashboard> = a.dash ?? {};
    saved.current = { as_of: d.as_of || "", period: d.period || "" };
    setInputs(saved.current);
    setNotes({});
    setSettingsError("");
    mode.current = d.compare_mode || "prev";
    setMode(mode.current);
    const cards = a.dash?.cards ?? [];
    pressed.current = seeds(cards);
    setPresses(pressed.current);
    spans.current.clear();
    base.current = null;
    elemRef.current = seedElems(cards);
    setElems(elemRef.current);
    atSeq.current = cards.length;
    if (!a.dash) { seqs.current.apply++; dispatch({ type: "ctx", ctx: null }); setPast(false); return; }
    apply(false, saved.current.as_of, saved.current.period)
      .then(() => spanFill(cards.filter(c => { const f = fieldsFor(c.id, cards, elemRef.current); return !isFrame(c.kind) && !isShell(c) && (f.period !== undefined || f.as_of !== undefined); })))  // 저장된 요소 기간을 받는 카드
      .catch(e => note("per", (e as Error).message, true));
    loadConn();
  });
  // 비교 기준을 바꾸면 대시보드 설정(compare_mode)에 저장하고 기간을 다시 읽어 견줄 기간을 바꾼다
  const setCompareMode = useCallback((m: CompareMode) => {
    setMode(m);
    mode.current = m;
    apply(true, saved.current.as_of, saved.current.period).catch(e => note("per", (e as Error).message, true));
  }, [apply, note]);

  // 대시보드 열기(옛 openDash): 지금 편집을 먼저 저장하고(못 하면 옮기지 않는다) 읽어 연다. 값은 저장된 값 그대로(다시 구하기는 연결된 뒤 기간 규칙대로)
  const open = useCallback(async (id: string) => {
    const mine = ++seqs.current.open;
    try { await flush(); }
    catch (e) { dispatch({ type: "error", message: "저장하지 못해서 다른 대시보드로 옮기지 않았어요. " + (e as Error).message }); return; }
    if (!id) return dispatch({ type: "open", dash: null });
    try {
      const { dashboard } = await getDashboard(id);
      if (mine !== seqs.current.open) return;
      picked.current = []; setPicks([]);  // 다른 대시보드를 열면 고른 필터를 푼다
      dispatch({ type: "open", dash: dashboard });
      setLast(id).catch(() => {});
    } catch (e) { if (mine === seqs.current.open) dispatch({ type: "error", message: (e as Error).message }); }
  }, [dispatch]);
  useEffect(() => { openRef.current = open; }, [open]);
  const { saving } = useAutosave(open);
  const undo = useUndo();

  // 모든 카드를 지금 연결에서 다시 구한다(옛 refreshDash). 기간을 건 채 구한 값은 화면에만, 아니면 카드 값으로(자동 저장이 쓴다)
  // fresh가 false면 갱신 표 없이(받아 둔 것으로) 구한다([전체 갱신] 뒤)
  const recompute = useCallback(async (fresh = true) => {
    if (!connRef.current) return dispatch({ type: "error", message: "먼저 데이터에 연결하세요." });
    const stamp = fresh ? crypto.randomUUID() : "", dash = now.current.dash?.id ?? "";
    const failed: string[] = [];
    for (const id of (now.current.dash?.cards ?? []).map(c => c.id)) {
      const card = now.current.dash?.cards.find(c => c.id === id);  // 도는 동안 고친 카드는 지금 정의로 구한다
      if (!card || isShell(card)) continue;  // 지운 카드, 데이터를 아직 정하지 않은 빈 카드
      const ctx = ctxOf(id);
      hold([id], true);
      try {
        const { data } = await computeCard(cardRequest(card), ctx, dash, stamp);
        if (!sameContent(now.current.dash?.cards.find(c => c.id === id)?.source, card.source)) continue;  // 구하는 사이 정의가 바뀌었다: 옛 값을 넣지 않는다
        const u = computedUpdate(card.id, data, ctx);  // 기간이 걸려 있으면 화면 값에만, 아니면 저장하고 화면 값은 비운다
        if (Object.keys(u.patch).length) dispatch({ type: "card", id: card.id, patch: u.patch, auto: true });  // 되돌리기에 쌓지 않는다(사용자 편집이 아니다)
        dispatch({ type: "values", results: [u.result] });
      } catch (e) { failed.push(`${card.title}: ${(e as Error).message}`); }
      finally { hold([id], false); }
    }
    let why = "";
    try { await flush(); } catch (e) { why = "저장하지 못했어요. " + (e as Error).message; }
    const message = [failed.length ? `카드 ${failed.length}개는 다시 구하지 못해서 이전 값을 그대로 뒀어요. ` + failed.join(" / ") : "", why].filter(Boolean).join(" ");
    dispatch({ type: "error", message });
    setBell("pull", message);
  }, [dispatch, setBell, ctxOf, hold]);

  // 갱신 주기 사슬(열어 둔 동안만): 앞 갱신이 끝난 뒤 max(주기, 걸린 시간) 뒤에 다음. 연결이 없으면 멈추고 종에 알린다
  const pullEvery = consts && state.dash ? everySeconds(pullOf(state.dash, consts.dash.pull).every, consts.dash.pull) : 0;
  const recomputeRef = useRef(recompute);
  useEffect(() => { recomputeRef.current = recompute; });
  useEffect(() => {
    if (!pullEvery) return;
    let on = true, timer: ReturnType<typeof setTimeout>;
    const step = async () => {
      if (!on) return;
      if (!connRef.current) { on = false; setBell("offline", OFFLINE_NOTE); return; }
      const start = performance.now();
      if (!document.hidden && !typing() && !saved.current.as_of) await recomputeRef.current();  // 지난 날짜 기준이면 돌지 않는다
      if (on) timer = setTimeout(step, Math.max(pullEvery * 1000, performance.now() - start));
    };
    timer = setTimeout(step, pullEvery * 1000);
    return () => { on = false; clearTimeout(timer); setBell("offline", ""); };  // 사슬이 다시 돌면 옛 "연결 없음" 소식은 뺀다
  }, [state.dash?.id, pullEvery, setBell]);

  // 종에 담을 빠진 구간 소식: 보이는 값의 note에 서버 gap_note가 든 카드 이름들
  useEffect(() => {
    if (!consts) return;
    const gap = consts.dash.pull.gap_note;
    const names = (state.dash?.cards ?? []).filter(c => (state.values[c.id]?.data ?? c.data)?.note?.includes(gap)).map(c => c.title);
    setBell("gap", names.length ? `${gap} ${names.join(", ")}` : "");
  }, [consts, state.dash, state.values, setBell]);

  // 비교 문장(옛 recompare): 보는 값·기간이 바뀌면 다시 받는다. 보낼 것이 같으면 묻지 않는다
  useEffect(() => {
    if (!consts) return;
    const items: ReturnType<typeof compareItem>[] = [], who: string[] = [];
    (state.dash?.cards ?? []).forEach(c => { const cx = ctxOf(c.id), item = compareItem({ ...c, data: state.values[c.id]?.data ?? c.data }, cx?.range ?? null, cx?.prev ?? null, merge, cx?.compare_mode); if (item) { items.push(item); who.push(c.id); } });
    const key = JSON.stringify([who, items]);
    if (key === seqs.current.cmpKey) return;
    seqs.current.cmpKey = key;
    const seq = ++seqs.current.cmp;
    (items.length ? compare(items as NonNullable<(typeof items)[number]>[]) : Promise.resolve({ results: [] as unknown[] }))
      .then(d => { if (seq === seqs.current.cmp) setCompares(Object.fromEntries(d.results.map((r, i) => [who[i], r as Said]))); })
      .catch(() => { if (seq === seqs.current.cmp) setCompares({}); });  // 문장을 못 받으면 문장 없이 그린다
  }, [consts, merge, state.dash, state.values, state.ctx, presses, elems, ctxOf]);

  // 새 라벨의 색 순서(옛 pickColor가 그리며 card.colors.order에 붙여 저장하던 것): 값이 바뀐 뒤 새 라벨이 생긴 카드만 dispatch card(→ 자동 저장)
  useEffect(() => {
    if (!consts || state.locked) return;
    const kit = seriesKit(consts);
    (state.dash?.cards ?? []).forEach(c => {
      if (isShell(c) || c.kind === "geomap") return;  // 지도 영역은 순서로 색을 고르지 않는다(정하지 않은 영역은 바탕 색)
      const shownCard = { ...c, data: state.values[c.id]?.data ?? c.data }, { group, names } = kit.colorNames(shownCard);
      const colors = newOrder(c, group, names);
      if (colors) dispatch({ type: "card", id: c.id, patch: { colors }, auto: true });  // 되돌리기에 쌓으면 되돌린 뒤 다시 붙으며 다시 하기를 지운다
    });
  }, [consts, state.dash, state.values, state.locked, dispatch]);

  // 첫 화면: 오늘·연결
  useEffect(() => { ready(); loadConn(); }, [ready, loadConn]);

  const period: Period = {
    inputs, setInput, notes, today, past, settingsError, compareMode, setCompareMode,
    commitDay: (text?: string) => { const t = text ?? inputs.as_of; setInput("as_of", t); if (t !== saved.current.as_of) apply(true, t, inputs.period); },  // 옛 change 이벤트처럼 바뀌었을 때만
    commitPeriod: (text?: string) => { const t = text ?? inputs.period; setInput("period", t); if (t.trim() !== saved.current.period) apply(true, inputs.as_of, t); },  // text: 달력이 고른 기간(칸 값이 아직 안 바뀐 때)
  };
  const value: Shell = { conn, loadConn, dbMin, settings, setSettings, bell, setBell, open, flush, compares, recompute, period, addOpen, setAddOpen, gallery, setGallery, dashOnly, setDashOnly, saving, undo, build, page, setPage, picks, setFilter, report, setReport, zoom, setZoom, clearFilters, presses, press, elems, elemPeriod, setElemUnit, setCell, ctxOf, unitOf, viewOf, busy };
  return <Ctx_.Provider value={value}>{children}</Ctx_.Provider>;
}
