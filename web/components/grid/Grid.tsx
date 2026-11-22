"use client";
// N3 — 카드 격자와 카드 상자(옛 dash_edit.js EditBoard·board.js cardBox). N7: 카드 모음 놓기(옛 dash_panel.js acceptGalleryDrops·dashboard.html addShell), 비교 문장·다시 구하지 못한 이유.
// 12칸 격자(dash_edit.css .board.grid)에 card.layout대로 놓는다. 편집 모드에서만 제목 줄 끌기·⚙·×·크기 손잡이가 있다(대시보드만 보기에서는 고르기·편집 없음).
// 보는 곳: 몸통을 누르면 큰 창(KPI는 KpiZoom, 나머지는 같은 그림을 Pop .zoom-box에), 고르기는 머리 줄 체크 칸(.bcheck 단추)으로만.
// 만드는 곳: 카드를 누르면 고르기(state.picked)·설정 대상(selected). ⤢는 두 곳 모두.
// 끌기·크기는 useDrag가 미리보기를 주고, 놓으면 dispatch({type:"cards"})로 layout을 바꾼다(세로 압축은 calc가 한다). 카드가 없어도 격자를 그려 카드 모음을 놓을 수 있다.
import { useCallback, useRef, useState, type CSSProperties, type DragEvent, type Dispatch, type KeyboardEvent, type SetStateAction } from "react";
import { CardBody } from "@/components/cards/CardBody";
import { CHART_KINDS } from "@/components/cards/ChartCard";
import { Pop } from "@/components/cards/Pop";
import { KpiZoom } from "@/components/cards/svg/KpiZoom";
import { newCardId } from "@/components/edit/Templates";
import { FrameCard } from "@/components/frame/FrameCard";
import { isFrame } from "@/components/frame/registry";
import { CARD_MIME, isShell, shellOf, type Spec } from "@/components/edit/menu/kinds";
import { useShell } from "@/components/shell/ShellProvider";
import { cardValue } from "@/components/cards/svg/__pure__";
import { GRID_COLS, cellAt, clampLayout, compactLayout, isPoints, moveCard, onPage, pageOf } from "@/lib/calc";
import { useConstants } from "@/lib/constants";
import { pullOf } from "@/lib/pull";
import { useDash } from "@/lib/store";
import { sameSource } from "@/lib/series";
import { freshLinks, isSender, onlySeries } from "@/lib/links";
import type { Card, Ctx, Dashboard, Layout, Section, Source, Unit } from "@/lib/types";
import { AlignBar } from "./AlignBar";
import { MISSING_SECTION, NEW_PIECE, SUB, applySections, dropPiece, movePiece, pieceItems, pieceMin, resizePiece, roomOf, sectionMin, sectionsOf, wrapCard } from "./sections";
import { metrics, useDrag, type Drag, type DragPlan } from "./useDrag";

// 빈 틀 카드(템플릿·카드 모음에서 막 놓은 카드)의 안내
const SHELL_NOTE = "아직 데이터가 없어요. 제목 옆 ⚙를 눌러 테이블과 컬럼을 고르세요.";
const NO_SPOT = "그 자리에는 조각을 놓을 수 없어요. 다른 조각과 1눈금 이상 떨어진 빈 곳에 놓아 주세요.";
const LOOK_TIP = "눌러서 크게 봐요. 그림으로 내보낼 카드는 제목 줄의 체크 칸으로 골라요";  // 보는 곳 카드의 풍선
const PRESSABLE = "button, a, input, select, textarea, label, summary";  // 몸통 안에서 눌러도 큰 창을 열지 않는 것
const ACTIVE = PRESSABLE + ", .bcard:not(.frame) > .bbody";  // 보는 곳에서 누르면 일이 일어나는 곳(단추·입력·데이터 카드 몸통 = 크게 보기)

/** 카드 아래 안내(옛 dash.js .bwhen): 다른 연결의 이전 값, 서버 안내(값을 구한 시각은 카드 머리 .bhead의 title), 저장된 일 값의 시작일, 기간 기준 날짜, 연결해서 다시 구해야 하는 집계. 한 줄에 하나 */
export function whenLines(card: Card, d: Card["data"], range: Ctx["range"], conn: Source, merge: string[]) {
  const first = d.by_unit?.day?.labels[0], paint = !CHART_KINDS.includes(card.kind);
  const noDate = range && !d.by_unit && !card.source?.date && !["status", "alerts", "map"].includes(card.kind) && !isPoints(card);
  return [
    conn && card.source?.key && !sameSource(card.source.key, conn.key) ? "다른 연결에서 구한 이전 값이에요" : "",
    d.note,
    range?.start && first && range.start < first ? `저장된 일 값은 ${first}부터예요` : "",
    card.source?.date && !d.by_unit ? `기간 기준 날짜: ${card.source.date}` : noDate ? "기간·기준일은 날짜 X축 카드나 기간 기준 날짜 컬럼을 고른 카드에만 적용돼요" : "",
    range && paint && d.by_unit?.day?.series.some((s, i) => !merge.includes(s.agg) && cardValue(card, i, range, merge) == null) ? "이 집계(고유 개수·중앙값 …)는 기간을 바꾸면 연결해서 다시 구해야 해요" : "",
  ].filter(Boolean).join("\n");
}

/** [SPECIFICATION] plans/phase-15.md#scrollToCard
 *  격자에서 id 카드(section[data-id])를 다음 그리기 뒤에 화면 안으로 스크롤한다. */
export function scrollToCard(id: string): void {
  requestAnimationFrame(() => document.querySelector<HTMLElement>(`section[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
}

/** 빈 틀 카드 하나 넣기(옛 addShell): 다음 빈 자리(놓은 칸이 있으면 그 자리, 겹치면 다른 카드가 밀린다)에 놓고 편집 모드로 그 카드를 고른다 */
export function useAddShell() {
  const { state, dispatch } = useDash();
  const { page } = useShell();
  const max = useConstants()?.dash.max_cards ?? Infinity;
  return useCallback((spec: Spec, cell?: { x: number; y: number }, section?: string) => {
    const dash = state.dash;
    if (!dash) return dispatch({ type: "error", message: "대시보드를 먼저 고르거나 새로 만드세요." });
    if (dash.cards.length >= max) return dispatch({ type: "error", message: `대시보드에는 카드를 ${max}개까지 넣을 수 있어요.` });
    const { sections, cards } = sectionsOf(dash);
    const base = { ...shellOf(spec), id: newCardId(), layout: { x: 0, y: 0, w: 6, h: 8 } } as Card;
    const target = section ? sections.find(s => s.id === section) : undefined;
    if (isSender(base.kind)) base.view = { ...base.view, link_to: freshLinks(base, dash, target ? pageOf(target, dash.pages ?? []) : page) };
    let card: Card, placed: Section[];
    if (!section) {
      const wrapped = wrapCard(base, sections, dash.pages, page);
      card = wrapped.card;
      placed = [...sections, wrapped.section];
      if (cell) {
        const s = wrapped.section;
        const at = moveCard(onPage(placed, dash.pages, page).map(p => ({ id: p.id, layout: p.layout })), s.id, { x: Math.min(Math.max(0, cell.x), GRID_COLS - s.layout.w), y: Math.max(0, cell.y) });
        placed = placed.map(p => ({ ...p, layout: at[p.id] ?? p.layout }));
      }
    } else {
      const target = sections.find(s => s.id === section);
      if (!target) return dispatch({ type: "error", message: MISSING_SECTION });
      const room = roomOf(target), min = pieceMin(base);
      const spot = dropPiece(pieceItems(cards.filter(c => c.section === section)), { id: base.id, layout: { x: 0, y: 0, w: NEW_PIECE * SUB.x, h: NEW_PIECE * SUB.y }, minW: min.w, minH: min.h }, cell ?? { x: 0, y: 0 }, room);
      if (!spot) return dispatch({ type: "error", message: NO_SPOT });
      card = { ...base, section, layout: spot };
      placed = sections;
    }
    dispatch({ type: "cards", cards: [...cards, card], sections: placed });
    dispatch({ type: "editing", on: true });
    dispatch({ type: "select", id: card.id });
    scrollToCard(card.id);
    dispatch({ type: "notice", message: `빈 ${spec.name} 카드를 놓았어요. 오른쪽 카드 설정에서 데이터를 정하세요.` });
  }, [state.dash, dispatch, max, page]);
}

function WhenLine({ card, d, range }: { card: Card; d: Card["data"]; range: Ctx["range"] }) {
  const { conn } = useShell(), merge = useConstants()?.dash.merge ?? [];
  return <div className="hint bwhen [padding:0_.625rem]">{whenLines(card, d ?? {}, range, conn, merge)}</div>;
}

const withUnit = (card: Card, unit: Unit | null): Card => unit ? { ...card, view: { ...card.view, unit } } : card;  // 기간 단위 요소가 고른 단위로 그리기만(저장하지 않는다)

type Shared = {
  edit: boolean; look: boolean; star: Dashboard["north_star"]; palette: Dashboard["palette"]; shares: string[];
  setShares: Dispatch<SetStateAction<string[]>>; pick: (id: string) => void; cross: (from: string, column: string, value: string) => void; remove: (id: string) => void;
};
type PieceProps = Shared & { card: Card; l: Layout; drag: Pick<Drag, "startMove" | "startResize"> };

/** 조각 하나(옛 카드 상자): 고르기·크게 보기·%·⚙·×·이름 고치기·비교 문장·안내·틀 요소 */
function Piece({ card, l, drag, edit, look, star, palette, shares, setShares, pick, cross, remove }: PieceProps) {
  const { state, dispatch } = useDash();
  const { compares, dashOnly, build, conn, setZoom, ctxOf, unitOf, viewOf, presses, press, busy } = useShell();
  const consts = useConstants(), pc = !!consts && !!state.dash && pullOf(state.dash, consts.dash.pull).calc === "pc", pickable = !build && (!!conn || pc);  // 보는 곳에서 연결이 있거나 PC 계산 대시보드면 누르기를 받는다(ShellProvider recomputeRange와 같은 판정)
  const { startMove, startResize } = drag;
  const cx = ctxOf(card.id), v = viewOf(card.id), loading = busy.includes(card.id);
  const pressable = look && card.kind === "tile" && !!card.view.press, on = presses.some(p => p.from === card.id);
  const toggle = () => { press(card.id, on ? null : { tile: { body: card.source.body ?? {}, periods: card.view.press === "period" } }).catch(e => dispatch({ type: "error", message: (e as Error).message })); };
  const key = (e: KeyboardEvent) => {
    if (e.target !== e.currentTarget || (e.key !== "Enter" && e.key !== " ")) return;
    e.preventDefault();
    if (!look) pick(card.id); else if (pressable) toggle(); else if (!isShell(card)) setZoom(card.id);
  };
  const sel = state.picked.includes(card.id), open = state.selected === card.id;
  const tools = edit && (
    <>
      <button className="ib" title="카드 설정" aria-label={`${card.title} 카드 설정`} onClick={e => { e.stopPropagation(); dispatch({ type: "select", id: card.id }); }}>⚙</button>
      <button className="ib" title="이 카드 빼기" aria-label={`${card.title} 카드 빼기`} onClick={e => { e.stopPropagation(); remove(card.id); }}>×</button>
    </>
  );
  const resize = edit && <i className="bresize" title="끌어서 크기를 바꿔요" aria-hidden="true" onPointerDown={e => startResize(e, card.id)} />;
  // 요소: 머리 줄·안내 없이 몸통만, 고르기(pick) 없음(그림 저장 대상이 아니다). 편집 중에는 .fr-tools 줄과 몸통을 잡아 옮긴다
  if (isFrame(card.kind)) return (
    <section data-id={card.id} aria-label={card.title}
      className={"bcard frame" + (open && edit ? " editing" : "")}
      style={{ gridColumn: `${l.x + 1} / span ${l.w}`, gridRow: `${l.y + 1} / span ${l.h}` }}>
      {edit && (
        <div className="fr-tools" data-noprint onPointerDown={e => startMove(e, card.id)}>
          <i className="fr-grip" aria-hidden="true" title="끌어서 옮겨요" />
          {tools}
        </div>
      )}
      <div className="bbody" onPointerDown={edit ? e => startMove(e, card.id) : undefined}><FrameCard card={card} live={!edit} /></div>
      {resize}
    </section>
  );
  const paint = !CHART_KINDS.includes(card.kind);
  const result = state.values[card.id], said = card.kind === "kpi" || card.kind === "tile" ? undefined : compares[card.id];
  const drawn = withUnit(v?.series ? { ...card, data: onlySeries(card.data, v.series) } : card, unitOf(card.id));
  const res = v?.series && result?.data ? { ...result, data: onlySeries(result.data, v.series) } : result;
  const at = isShell(card) ? undefined : (result?.data ?? card.data)?.computed_at;
  return (
    <section data-id={card.id} tabIndex={0} aria-label={card.title + (sel ? " (선택됨)" : "")} title={dashOnly ? undefined : look ? LOOK_TIP : "눌러서 선택해요. 여러 개 고를 수 있어요"}
      aria-pressed={pressable ? on : undefined} aria-busy={loading || undefined}
      className={"bcard" + (sel ? " sel" : "") + (open && edit ? " editing" : "") + (look && !isShell(card) ? " zoomin" : "") + (loading ? " busy" : "")}
      style={{ gridColumn: `${l.x + 1} / span ${l.w}`, gridRow: `${l.y + 1} / span ${l.h}` }}
      onClick={look ? undefined : () => pick(card.id)} onKeyDown={key}>
      <div className={"bhead" + (edit ? " movable" : "")} title={at ? `값을 구한 시각 ${at.replace("T", " ")}` : undefined} onPointerDown={edit ? e => startMove(e, card.id) : undefined}>
        <button type="button" className="bcheck" aria-pressed={sel} aria-label={`${card.title} 카드 고르기`} title="그림·보고서로 내보낼 카드로 골라요" onClick={e => { e.stopPropagation(); pick(card.id); }} />
        {star.card === card.id && <span className="bbadge" title="북극성 지표">★</span>}
        {star.counters.includes(card.id) && <span className="bbadge" title="카운터 지표">⚑</span>}
        <input className="btitle" key={card.title} defaultValue={card.title} readOnly={!edit} aria-label="카드 이름" title={edit ? "카드 이름. 그림으로 저장할 때 파일 이름이 돼요" : undefined}
          onClick={e => e.stopPropagation()}
          onBlur={e => { const t = e.target.value.trim() || card.title; e.target.value = t; if (t !== card.title) dispatch({ type: "card", id: card.id, patch: { title: t } }); }} />
        {!isShell(card) && !paint && card.kind !== "analysis" && !isPoints(card) && (
          <button className="ib" aria-pressed={shares.includes(card.id)} title={shares.includes(card.id) ? "값으로 보기" : "비율(%)로 보기. 저장되지 않고 화면에서만 바뀌어요"} aria-label={`${card.title} 카드 값·비율 바꾸기`}
            onClick={e => { e.stopPropagation(); setShares(v => v.includes(card.id) ? v.filter(x => x !== card.id) : [...v, card.id]); }}>%</button>
        )}
        {!isShell(card) && <button className="ib" title="크게 보기" aria-label={`${card.title} 카드 크게 보기`} onClick={e => { e.stopPropagation(); setZoom(card.id); }}>⤢</button>}
        {tools}
      </div>
      <div className={"bbody" + (paint ? " paint" : "")} onClick={look && !isShell(card) ? e => { const hit = (e.target as Element).closest(PRESSABLE); if (!hit || !e.currentTarget.contains(hit)) { if (pressable) toggle(); else setZoom(card.id); } } : undefined}>
        {isShell(card) ? <div className="hint bnote">{SHELL_NOTE}</div> : <CardBody card={drawn} result={res} palette={palette} ctx={cx} onPick={pickable ? (column, value) => cross(card.id, column, value) : undefined} pressed={presses.find(p => p.from === card.id)?.value} onPress={pickable ? (column, values) => { press(card.id, values.length ? { value: { column, in: values } } : null).catch(e => dispatch({ type: "error", message: (e as Error).message })); } : undefined} share={shares.includes(card.id) || !!v?.share} />}
        {loading && <i className="bspin" aria-hidden="true" />}
      </div>
      {said && <div className={`bcmp ${said.tone} [padding:0_.625rem]`} title={said.delta_text}>{said.text}</div>}
      {!isShell(card) && <WhenLine card={card} d={result?.data ?? card.data} range={cx?.range} />}
      {result?.error && <div className="hint bnote [padding:0_.625rem] m-0" role="status">{result.error}</div>}
      {resize}
    </section>
  );
}

type SectionProps = { s: Section; l: Layout; pieces: Card[]; all: Card[]; allSecs: Section[]; drag: Pick<Drag, "startMove" | "startResize">; shared: Shared; removeAll: (ids: string[], sections: Section[]) => void };

/** 섹션 하나(쪽 격자의 카드 상자): 편집 도구 줄·이름·조각 격자·크기 손잡이. 조각 끌기는 여기서 useDrag를 부른다 */
function SectionBox({ s, l, pieces, all, allSecs, drag, shared, removeAll }: SectionProps) {
  const { state, dispatch } = useDash();
  const body = useRef<HTMLDivElement>(null);
  const [sure, setSure] = useState(false);  // 조각이 둘 이상인 섹션을 뺄 때 한 번 더 누르기
  const room = roomOf(s), { edit } = shared;
  const put = (sections: Section[], cards: Card[] = all) => dispatch({ type: "cards", cards, sections });
  const setPieces = (layouts: Record<string, Layout>) => put(allSecs, all.map(c => (layouts[c.id] ? { ...c, layout: layouts[c.id] } : c)));
  const away: DragPlan["away"] = (id, at) => {
    const target = document.elementFromPoint(at.x, at.y)?.closest<HTMLElement>(".bsection");
    if (!target || target.dataset.section === s.id) return false;
    const t = allSecs.find(x => x.id === target.dataset.section);
    if (!t) throw new Error(MISSING_SECTION);
    const tRoom = roomOf(t), tBody = target.querySelector<HTMLElement>(".bsec-body") as HTMLElement;
    const card = pieces.find(c => c.id === id) as Card;
    const cell = cellAt(at.x, at.y, metrics(tBody, tRoom.cols, tRoom.rows), at.grabX, at.grabY);
    const spot = dropPiece(pieceItems(all.filter(c => c.section === t.id)), pieceItems([card])[0], cell, tRoom);
    if (spot) put(allSecs, all.map(c => (c.id === id ? { ...c, section: t.id, layout: spot } : c)));
    return true;  // 자리가 없으면 옮기지 않고 제자리로 돌아간다(알림 없음)
  };
  const { preview, lines, startMove, startResize } = useDrag(body, pieceItems(pieces), setPieces, {
    cols: room.cols, rows: room.rows,
    move: (it, id, c) => movePiece(it, id, c, room),
    resize: (it, id, w, h) => resizePiece(it, id, w, h, room),
    away,
  });
  const n = pieces.length;
  return (
    <div className={"bsection" + (edit ? " editing-sec" : "")} data-section={s.id} aria-label={s.title || "섹션"}
      style={{ gridColumn: `${l.x + 1} / span ${l.w}`, gridRow: `${l.y + 1} / span ${l.h}` }}>
      {edit && (
        <div className="sec-tools" data-noprint>
          <i className="fr-grip" title="끌어서 섹션을 옮겨요" onPointerDown={e => drag.startMove(e, s.id)} />
          <input key={s.title} defaultValue={s.title} aria-label="섹션 이름" placeholder="섹션 이름(비우면 이름 줄 없음)"
            onBlur={e => { const t = e.target.value.trim(); e.target.value = t; if (t !== s.title) put(allSecs.map(x => (x.id === s.id ? { ...x, title: t } : x))); }} />
          <button type="button" className="ib" aria-pressed={s.follow} title="섹션 크기를 바꾸면 조각도 같이 바뀜" aria-label="섹션 크기를 바꾸면 조각도 같이 바뀜"
            onClick={() => put(allSecs.map(x => (x.id === s.id ? { ...x, follow: !x.follow } : x)))}>⤡</button>
          <AlignBar ids={state.picked.filter(id => pieces.some(c => c.id === id))} items={pieceItems(pieces)} room={room} onApply={setPieces} onError={message => dispatch({ type: "error", message })} />
          <button type="button" className={sure ? "btn" : "ib"} aria-label="섹션 빼기" onBlur={() => setSure(false)}
            onClick={() => { if (n > 1 && !sure) setSure(true); else removeAll(pieces.map(c => c.id), allSecs.filter(x => x.id !== s.id)); }}>
            {sure ? `조각 ${n}개도 함께 지워요. 한 번 더 누르면 지워요` : "×"}
          </button>
        </div>
      )}
      {s.title && <div className="bsec-title">{s.title}</div>}
      <div className="bsec-body" ref={body} style={{ "--cols": room.cols, "--rows": room.rows } as CSSProperties}>
        {pieces.map(card => <Piece key={card.id} card={card} l={preview?.[card.id] ?? card.layout} drag={{ startMove, startResize }} {...shared} />)}
        {lines.map(g => <i key={`${g.axis}${g.at}`} className={"guide " + g.axis} data-noprint style={g.axis === "x" ? { left: `${g.at / room.cols * 100}%` } : { top: `${g.at / room.rows * 100}%` }} />)}
      </div>
      {edit && <i className="sresize" title="끌어서 섹션 크기를 바꿔요" aria-hidden="true" onPointerDown={e => drag.startResize(e, s.id)} />}
    </div>
  );
}

export function Grid() {
  const { state, dispatch } = useDash();
  const { dashOnly, build, conn, presses, press, unitOf, ctxOf, clearFilters, page, zoom, setZoom } = useShell();
  const [shares, setShares] = useState<string[]>([]);  // 비율(%)로 보는 카드 id(저장하지 않는다)
  // 누르면 거르기(4단계): 같은 값을 다시 누르면 푼다. 보는 곳에서 연결돼 있거나 PC 계산 대시보드일 때(만드는 곳에서는 카드 누르기가 설정 열기다)
  const cross = (from: string, column: string, value: string) => {
    const on = presses.some(p => p.from === from && p.value?.column === column && p.value.in[0] === value);
    press(from, on ? null : { value: { column, in: [value] } }).catch(e => dispatch({ type: "error", message: (e as Error).message }));
  };
  const addShell = useAddShell();
  const ref = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  const dash = state.dash;
  const { sections: allSecs, cards: all } = dash ? sectionsOf(dash) : { sections: [], cards: [] }, paged = !!dash?.pages?.length;
  const secs = onPage(allSecs, dash?.pages, page);  // 지금 쪽의 섹션만 그린다(쪽을 나누지 않았으면 전부)
  const cards = all.filter(c => secs.some(s => s.id === c.section));  // 지금 쪽의 조각
  const raw = secs.map(s => { const m = sectionMin(s, all.filter(c => c.section === s.id)); return { id: s.id, layout: clampLayout(s.layout, m.h, m.w), minW: m.w, minH: m.h }; });
  const cut = raw.some((r, i) => JSON.stringify(r.layout) !== JSON.stringify(secs[i].layout)), up = paged || cut ? compactLayout(raw) : null;  // 다른 쪽 섹션이 빠진 빈 줄·낮게 저장된 자리는 위로 붙인다
  const items = up ? raw.map(i => ({ ...i, layout: up[i.id] })) : raw, placed = Object.fromEntries(items.map(i => [i.id, i.layout]));  // 낮게 저장된 둥근 카드는 여기서 올라간다(저장은 다음 편집 때)
  const { preview, startMove, startResize } = useDrag(
    ref,
    items,
    layouts => {
      const same = secs.every(s => !layouts[s.id] || JSON.stringify(layouts[s.id]) === JSON.stringify(clampLayout(s.layout)));
      if (!same) { const n = applySections(all, allSecs, layouts); dispatch({ type: "cards", cards: n.cards, sections: n.sections }); }
    },
  );
  if (!dash) return null;
  const star = dash.north_star, edit = state.editing && !dashOnly;
  const look = !build;  // 보는 곳: 몸통을 누르면 큰 창, 고르기는 체크 칸으로만

  function removePieces(ids: string[], sections: Section[]) {  // 조각을 뺀다(섹션은 sections대로 남는다)
    dispatch({ type: "cards", cards: all.filter(c => !ids.includes(c.id)), sections });
    if (state.selected && ids.includes(state.selected)) dispatch({ type: "select", id: null });
    if (ids.includes(star.card) || star.counters.some(c => ids.includes(c))) {  // 지운 카드의 id가 남으면 서버가 저장을 거부한다(liveStar)
      const counters = star.counters.filter(c => !ids.includes(c));
      dispatch({ type: "meta", patch: { north_star: { card: ids.includes(star.card) ? "" : star.card, counters, limits: Object.fromEntries(Object.entries(star.limits ?? {}).filter(([k]) => counters.includes(k))) } } });
    }
  }
  const pick = (id: string) => {
    if (dashOnly) return;
    const on = !state.picked.includes(id);
    dispatch({ type: "pick", ids: on ? [...state.picked, id] : state.picked.filter(p => p !== id) });
    if (on) dispatch({ type: "select", id }); else if (state.selected === id) dispatch({ type: "select", id: null });
  };
  // 카드 모음 칸을 끌어 놓기: 섹션 위면 그 섹션 눈금의 칸에 조각으로, 아니면 쪽 격자 칸에 새 섹션으로
  const gallery = (e: DragEvent) => !dashOnly && [...e.dataTransfer.types].includes(CARD_MIME);
  const drop = (e: DragEvent) => {
    setReady(false);
    const raw = e.dataTransfer.getData(CARD_MIME);
    if (!raw || !ref.current) return;
    e.preventDefault();
    const box = (e.target as Element).closest<HTMLElement>(".bsection");
    if (!box) return addShell(JSON.parse(raw) as Spec, cellAt(e.clientX, e.clientY, metrics(ref.current)));
    const room = roomOf(allSecs.find(s => s.id === box.dataset.section) as Section);
    addShell(JSON.parse(raw) as Spec, cellAt(e.clientX, e.clientY, metrics(box.querySelector<HTMLElement>(".bsec-body") as HTMLElement, room.cols, room.rows)), box.dataset.section);
  };

  const zoomed = cards.find(c => c.id === zoom), big = zoomed && withUnit(zoomed, unitOf(zoomed.id));
  const shared: Shared = { edit, look, star, palette: dash.palette, shares, setShares, pick, cross, remove: id => removePieces([id], allSecs) };
  return (
    <>
    {big && big.kind === "kpi" && <KpiZoom card={big} result={state.values[big.id]} palette={dash.palette} ctx={ctxOf(big.id)} unit={unitOf(big.id)} live={!!conn && (!big.source?.key || sameSource(big.source.key, conn.key))} onClose={() => setZoom(null)} />}
    {big && big.kind !== "kpi" && (
      <Pop title={big.title} cls="zoom-box" onClose={() => setZoom(null)}>
        <div className={"bbody" + (CHART_KINDS.includes(big.kind) ? "" : " paint")}><CardBody card={big} result={state.values[big.id]} palette={dash.palette} ctx={ctxOf(big.id)} share={shares.includes(big.id)} /></div>
      </Pop>
    )}
    <div className={"board grid" + (ready ? " drop-ready" : "")} ref={ref} style={secs.length ? undefined : { minHeight: 200 }}
      onClick={look ? e => { if (!(e.target as Element).closest(ACTIVE)) clearFilters().catch(err => dispatch({ type: "error", message: (err as Error).message })); } : undefined}
      onDragOver={e => { if (gallery(e)) { e.preventDefault(); setReady(true); } }} onDragLeave={e => { if (e.target === e.currentTarget) setReady(false); }} onDrop={drop}>
      {secs.map(s => <SectionBox key={s.id} s={s} l={preview?.[s.id] ?? placed[s.id]} pieces={all.filter(c => c.section === s.id)} all={all} allSecs={allSecs} drag={{ startMove, startResize }} shared={shared} removeAll={removePieces} />)}
    </div>
    </>
  );
}
