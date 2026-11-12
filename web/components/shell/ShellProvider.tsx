"use client";
// N4 — 화면 틀의 공용 상태(옛 dashboard.html 전역 + dash_ctl.js DashCtl + dash.js DashBoard.setRange·recompare + dash_ctl.js liveCards·recomputeRange).
// 지금 연결(live_min의 source), 대시보드 열기(저장 → 읽기), 기간·기준일·주기·실시간 칸, 기간으로 다시 구하기(연결됐을 때만), 비교 문장, 다시 구하기, 대시보드만 보기.
// 기간 적용은 "open" 액션을 보고 한다(누가 열어도 같은 규칙). 구한 값은 dispatch({type:"values"}), 다시 구하지 못한 이유는 결과의 error(이전 값은 그대로 둔다).
// 비교 문장은 store에 자리가 없어 여기 둔다: 카드는 useCompare(card.id)({text, tone, delta_text})를 읽는다.
// 열기는 이 open 한 곳(저장값을 먼저 보이고, 연결돼 있으면 기간 규칙대로 다시 구한다). 자동 저장(useAutosave)·되돌리기(useUndo)도 여기서 한 번만 부른다.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { CHART_KINDS } from "@/components/cards/ChartCard";
import { datedCard, isShell } from "@/components/edit/menu/kinds";
import { flush, useAutosave } from "@/components/hooks/useAutosave";
import { useUndo, type Undo } from "@/components/hooks/useUndo";
import { cardRequest, compare, computeCard, computeCards, getDashboard, getLiveMin, parseDay, parseEvery, parsePeriod, saveSettings, setLast, setLiveMin } from "@/lib/api";
import { outsideWindow } from "@/lib/calc";
import { useConstants } from "@/lib/constants";
import { computedUpdate, useActionLog, useDash } from "@/lib/store";
import { compareItem, newOrder, sameSource } from "@/lib/series";
import { seriesKit } from "@/components/cards/chart/series";
import type { Card, CompareMode, ComputeResult, Ctx, DashSettings, Dashboard, PrevSpan, Source } from "@/lib/types";

export type Note = { text: string; bad?: boolean };
export type Said = { text: string; tone: string; delta_text?: string; [more: string]: unknown };
type Fields = { as_of: string; period: string; refresh: string; live: string };
type NoteKey = "asof" | "per" | "refresh" | "live" | "min";

export type Period = {
  inputs: Fields; setInput: (k: keyof Fields, v: string) => void;
  notes: Partial<Record<NoteKey, Note>>; today: string; past: boolean; liveOn: boolean;
  min: string; setMin: (v: string) => void; minOn: boolean; settingsError: string;
  compareMode: CompareMode; setCompareMode: (m: CompareMode) => void;
  commitDay: (text?: string) => void; commitPeriod: (text?: string) => void; commitEvery: (kind: "refresh" | "live") => void;
  toggleLive: () => void; saveMin: () => void;
};
export type Shell = {
  conn: Source; loadConn: () => Promise<void>;
  open: (id: string) => Promise<void>; flush: () => Promise<void>;
  compares: Record<string, Said>; recompute: () => Promise<void>; period: Period;
  addOpen: boolean; setAddOpen: (on: boolean) => void; gallery: boolean; setGallery: (on: boolean) => void; dashOnly: boolean; setDashOnly: (on: boolean) => void;
  saving: boolean; undo: Undo;
};

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
const EMPTY: Fields = { as_of: "", period: "", refresh: "", live: "" };

export function ShellProvider({ children }: { children: React.ReactNode }) {
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
  // ---------- 기간 칸(DashCtl) ----------
  const [inputs, setInputs] = useState<Fields>(EMPTY);
  const saved = useRef<Fields>({ ...EMPTY });  // 확정된 값(틀리면 이 값으로 되돌린다)
  const [notes, setNotes] = useState<Partial<Record<NoteKey, Note>>>({});
  const [today, setToday] = useState("");
  const todayRef = useRef<Promise<string> | null>(null);
  const [past, setPast] = useState(false);
  const [liveOn, setLiveOn] = useState(false);
  const live = useRef({ on: false, gen: 0, timer: 0 as unknown as ReturnType<typeof setTimeout>, seconds: 0, times: [] as number[] });
  const [min, setMin] = useState("");
  const [settingsError, setSettingsError] = useState("");
  const [compareMode, setMode] = useState<CompareMode>("prev");
  const mode = useRef<CompareMode>("prev");  // 확정된 비교 기준(대시보드 설정 compare_mode)
  const seqs = useRef({ apply: 0, range: 0, cmp: 0, open: 0, hadRange: false, cmpKey: "", asOf: "" });
  const rangeCall = useRef(new Map<string, number>());

  const note = useCallback((k: NoteKey, text: string, bad = false) => setNotes(n => ({ ...n, [k]: { text, bad } })), []);
  const setInput = useCallback((k: keyof Fields, v: string) => setInputs(i => ({ ...i, [k]: v })), []);
  const ready = useCallback(() => todayRef.current ??= parseDay("").then(d => { setToday(d.date ?? ""); return d.date ?? ""; }), []);

  // 지금 연결 열쇠와 실시간 최소 간격(옛 loadMin·showConn)
  const loadConn = useCallback(async () => {
    try {
      const d = await getLiveMin();
      connRef.current = d.source;
      setConn(d.source);
      setMin(d.seconds ? String(d.seconds) : "");
      note("min", d.source ? d.source.label : "연결하면 정할 수 있어요");
    } catch (e) { note("min", (e as Error).message, true); }
  }, [note]);
  const saveMin = useCallback(async () => {
    try {
      const d = await setLiveMin(+min || 0);
      setMin(d.seconds ? String(d.seconds) : "");
      note("min", d.seconds ? `${d.source?.label}: ${d.seconds}초 이상` : "제한 없음");
    } catch (e) { note("min", (e as Error).message, true); }
  }, [min, note]);

  const merge = useMemo(() => consts?.merge ?? [], [consts]);
  // 지금 보이는 값을 단 카드(구한 값이 있으면 그것)
  const shown = useCallback((c: Card): Card => ({ ...c, data: now.current.values[c.id]?.data ?? c.data }), []);
  // 구한 결과를 넣는다: 실패한 카드는 이전 값을 두고 이유만(옛 rangeNote)
  const putResults = useCallback((results: ComputeResult[]) => {
    const vals = now.current.values;
    dispatch({ type: "values", results: results.map(r => r.data ? r : { id: r.id, data: vals[r.id]?.data, error: r.error }) });
  }, [dispatch]);

  // 기간 기준 날짜 컬럼이 있는 카드 등을 보는 기간으로 다시 구한다(옛 recomputeRange). 연결이 없거나 다른 연결의 카드는 이유만 적는다.
  const recomputeRange = useCallback(async (cards: Card[], ctx: Ctx | null) => {
    const call = ++seqs.current.range, key = connRef.current?.key ?? "";
    const notes: ComputeResult[] = [], todo: Card[] = [];
    cards.forEach(c => {
      rangeCall.current.set(c.id, call);
      const why = !connRef.current ? "연결하면 이 기간으로 다시 구해요" : c.source?.key && key && !sameSource(c.source.key, key) ? "다른 연결의 카드라 이 기간으로 구하지 못했어요" : "";
      if (why) notes.push({ id: c.id, error: why }); else if (c.id) todo.push(c);
    });
    if (notes.length) putResults(notes);
    if (!todo.length) return;
    const mine = (r: ComputeResult) => rangeCall.current.get(r.id) === call;  // 기간을 연달아 바꾸면 카드마다 가장 최근 요청의 것만
    try { putResults((await computeCards(todo.map(cardRequest), ctx)).results.filter(mine)); }
    catch (e) { putResults(todo.filter(c => rangeCall.current.get(c.id) === call).map(c => ({ id: c.id, error: (e as Error).message }))); }
  }, [putResults]);

  // 보는 기간을 바꾼다(옛 setRange): ctx를 두고, 날짜 X축이 아닌데 기간 기준 날짜 컬럼이 있는 카드·고유 개수 카드·읽은 범위 밖 카드만 다시 구한다
  const setRange = useCallback(async (c: Ctx | null, asOf: string) => {
    const range = c?.range ?? null;
    const cm = mode.current === "prev" ? undefined : mode.current;  // 기간이 비어도 카드 자신의 기간 줄(서버 own_range)을 같은 비교 기준으로
    const ctx: Ctx | null = range || asOf || cm ? { range: range ?? undefined, prev: c?.prev ?? null, months: c?.months, as_of: asOf || undefined, compare_mode: cm } : null;
    dispatch({ type: "ctx", ctx });
    const was = seqs.current.hadRange;
    seqs.current.hadRange = !!range;
    const unique = (x: Card) => !CHART_KINDS.includes(x.kind) && !!x.data?.by_unit?.day?.series.some(s => !merge.includes(s.agg));
    const stale = (now.current.dash?.cards ?? []).map(shown).filter(x => datedCard(x, range, was) || unique(x) || outsideWindow(x, range));
    if (stale.length) await recomputeRange(stale, ctx);
  }, [dispatch, merge, shown, recomputeRange]);

  const stopLive = useCallback((text: string) => {
    live.current.on = false;
    live.current.gen++;
    clearTimeout(live.current.timer);
    setLiveOn(false);
    if (text) note("live", text);
  }, [note]);

  // 설정 칸을 확정하면 그 항목만 저장한다. 거부되면 이유를 보이고 저장된 대시보드를 다시 연다
  const openRef = useRef<(id: string) => Promise<void>>(async () => {});
  const changed = useCallback(async () => {
    const id = now.current.dash?.id, f = saved.current;
    if (!id) return;
    const fields: DashSettings = { as_of: f.as_of, period: f.period, compare_mode: mode.current, refresh: { every: f.refresh }, live: { every: f.live } };
    try { await saveSettings(id, fields); dispatch({ type: "meta", patch: fields }); setSettingsError(""); }
    catch (e) { setSettingsError((e as Error).message); await openRef.current(id); }
  }, [dispatch]);

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
    if (isPast) stopLive("지난 날짜 기준으로 보는 중이라 실시간 갱신을 멈췄어요");
    await setRange(parsed?.context ?? null, asOf);
    if (commit) await changed();
  }, [ready, note, setInput, setRange, stopLive, changed]);

  // 최신화 주기·실시간 간격 칸을 서버로 검사한다(최신화는 1시간 이상, 실시간은 이 연결의 최소 이상)
  const commitEvery = useCallback(async (kind: "refresh" | "live") => {
    const text = (kind === "refresh" ? inputs.refresh : inputs.live).trim();
    if (text === saved.current[kind]) return;
    if (text) {
      const d = await parseEvery(text, kind).catch(e => ({ seconds: null, error: (e as Error).message }));
      if (d.error) { setInput(kind, saved.current[kind]); return note(kind, d.error, true); }
      if (kind === "live") live.current.seconds = d.seconds ?? 0;
    }
    saved.current = { ...saved.current, [kind]: text };
    note(kind, text ? "" : kind === "refresh" ? "안 함" : "");
    await changed();
  }, [inputs, setInput, note, changed]);

  // 실시간 한 번(옛 liveCards): 연결이 있는 카드의 값을 다시 구해 보인다(저장하지 않는다)
  const liveRun = useCallback(async () => {
    if (typing()) return false;
    if (!connRef.current) throw new Error("먼저 데이터에 연결하세요.");
    const cards = (now.current.dash?.cards ?? []).filter(c => c.source?.key && c.id).map(cardRequest);
    putResults((await computeCards(cards, now.current.ctx)).results);
    return true;
  }, [putResults]);
  // 실시간 사슬: 앞 조회가 끝난 뒤 max(간격, 걸린 시간) 뒤에 다음. gen이 바뀌면(멈춤·다시 시작) 앞 사슬은 끊긴다
  const tick = useCallback(async function step(gen: number) {
    const L = live.current;
    if (!L.on || gen !== L.gen) return;
    const interval = L.seconds * 1000;
    let took = 0;
    if (!document.hidden) {
      const start = performance.now();
      try { const ran = await liveRun(); note("live", ran === false ? "입력 중이라 이번 갱신은 건너뛰었어요" : `${new Date().toLocaleTimeString()}에 갱신했어요`); }
      catch (e) { note("live", (e as Error).message, true); }
      took = performance.now() - start;
      L.times.push(took);
      if (took > interval) note("live", `조회가 간격보다 오래 걸려요. 평균 ${(L.times.reduce((a, b) => a + b, 0) / L.times.length / 1000).toFixed(1)}초 걸렸어요`, true);
    }
    if (L.on && gen === L.gen) L.timer = setTimeout(() => step(gen), Math.max(interval, took));
  }, [liveRun, note]);
  const toggleLive = useCallback(async () => {
    if (live.current.on) return stopLive("멈췄어요");
    if (!saved.current.live) return note("live", "실시간 간격을 먼저 적어 주세요. 예: 30초", true);
    if (saved.current.as_of) return note("live", "지난 날짜 기준으로 보는 중이라 실시간 갱신을 시작할 수 없어요", true);
    const every = await parseEvery(saved.current.live, "live");
    if (every.error || !((every.seconds ?? 0) > 0)) return note("live", every.error || "실시간 간격을 읽지 못했어요", true);  // 간격 없이 시작하면 쉬지 않고 조회한다
    Object.assign(live.current, { seconds: every.seconds, on: true, times: [] });
    setLiveOn(true);
    tick(++live.current.gen);
  }, [stopLive, note, tick]);
  useEffect(() => () => { live.current.on = false; clearTimeout(live.current.timer); }, []);

  // 대시보드가 열리면(누가 열었든) 칸을 채우고 기간을 적용한다(옛 ctl.set)
  useActionLog(a => {
    if (a.type !== "open") return;
    stopLive("");
    const d: Partial<Dashboard> = a.dash ?? {};
    saved.current = { as_of: d.as_of || "", period: d.period || "", refresh: d.refresh?.every || "", live: d.live?.every || "" };
    setInputs(saved.current);
    setNotes({});
    setSettingsError("");
    mode.current = d.compare_mode || "prev";
    setMode(mode.current);
    if (!a.dash) { seqs.current.apply++; dispatch({ type: "ctx", ctx: null }); setPast(false); return; }
    apply(false, saved.current.as_of, saved.current.period).catch(e => note("per", (e as Error).message, true));
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
      dispatch({ type: "open", dash: dashboard });
      setLast(id).catch(() => {});
    } catch (e) { if (mine === seqs.current.open) dispatch({ type: "error", message: (e as Error).message }); }
  }, [dispatch]);
  useEffect(() => { openRef.current = open; }, [open]);
  const { saving } = useAutosave(open);
  const undo = useUndo();

  // 모든 카드를 지금 연결에서 다시 구한다(옛 refreshDash). 기간을 건 채 구한 값은 화면에만, 아니면 카드 값으로(자동 저장이 쓴다)
  const recompute = useCallback(async () => {
    if (!connRef.current) return dispatch({ type: "error", message: "먼저 데이터에 연결하세요." });
    const failed: string[] = [];
    for (const id of (now.current.dash?.cards ?? []).map(c => c.id)) {
      const card = now.current.dash?.cards.find(c => c.id === id);  // 도는 동안 고친 카드는 지금 정의로 구한다
      if (!card || isShell(card)) continue;  // 지운 카드, 데이터를 아직 정하지 않은 빈 카드
      const ctx = now.current.ctx;
      try {
        const { data } = await computeCard(cardRequest(card), ctx);
        if (now.current.dash?.cards.find(c => c.id === id)?.source !== card.source) continue;  // 구하는 사이 정의가 바뀌었다: 옛 값을 넣지 않는다
        const u = computedUpdate(card.id, data, ctx);  // 기간이 걸려 있으면 화면 값에만, 아니면 저장하고 화면 값은 비운다
        if (Object.keys(u.patch).length) dispatch({ type: "card", id: card.id, patch: u.patch, auto: true });  // 되돌리기에 쌓지 않는다(사용자 편집이 아니다)
        dispatch({ type: "values", results: [u.result] });
      } catch (e) { failed.push(`${card.title}: ${(e as Error).message}`); }
    }
    let why = "";
    try { await flush(); } catch (e) { why = "저장하지 못했어요. " + (e as Error).message; }
    dispatch({ type: "error", message: [failed.length ? `카드 ${failed.length}개는 다시 구하지 못해서 이전 값을 그대로 뒀어요. ` + failed.join(" / ") : "", why].filter(Boolean).join(" ") });
  }, [dispatch]);

  // 비교 문장(옛 recompare): 보는 값·기간이 바뀌면 다시 받는다. 보낼 것이 같으면 묻지 않는다
  useEffect(() => {
    if (!consts) return;
    const range = state.ctx?.range ?? null, prev: PrevSpan | null = state.ctx?.prev ?? null, items: ReturnType<typeof compareItem>[] = [], who: string[] = [];
    (state.dash?.cards ?? []).forEach(c => { const item = compareItem({ ...c, data: state.values[c.id]?.data ?? c.data }, range, prev, merge, state.ctx?.compare_mode); if (item) { items.push(item); who.push(c.id); } });
    const key = JSON.stringify([who, items]);
    if (key === seqs.current.cmpKey) return;
    seqs.current.cmpKey = key;
    const seq = ++seqs.current.cmp;
    (items.length ? compare(items as NonNullable<(typeof items)[number]>[]) : Promise.resolve({ results: [] as unknown[] }))
      .then(d => { if (seq === seqs.current.cmp) setCompares(Object.fromEntries(d.results.map((r, i) => [who[i], r as Said]))); })
      .catch(() => { if (seq === seqs.current.cmp) setCompares({}); });  // 문장을 못 받으면 문장 없이 그린다
  }, [consts, merge, state.dash, state.values, state.ctx]);

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
    inputs, setInput, notes, today, past, liveOn, min, setMin, minOn: !!conn, settingsError, compareMode, setCompareMode,
    commitDay: (text?: string) => { const t = text ?? inputs.as_of; setInput("as_of", t); if (t !== saved.current.as_of) apply(true, t, inputs.period); },  // 옛 change 이벤트처럼 바뀌었을 때만
    commitPeriod: (text?: string) => { const t = text ?? inputs.period; setInput("period", t); if (t.trim() !== saved.current.period) apply(true, inputs.as_of, t); },  // text: 달력이 고른 기간(칸 값이 아직 안 바뀐 때)
    commitEvery: kind => { commitEvery(kind); }, toggleLive: () => { toggleLive(); }, saveMin: () => { saveMin(); },
  };
  const value: Shell = { conn, loadConn, open, flush, compares, recompute, period, addOpen, setAddOpen, gallery, setGallery, dashOnly, setDashOnly, saving, undo };
  return <Ctx_.Provider value={value}>{children}</Ctx_.Provider>;
}
