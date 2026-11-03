"use client";
// N5 — 차트 선반(옛 static/core/dash_shelf.js shelfPanel)과 카드 모음(옛 dash_panel.js initGallery). 계산은 panel/shelf.ts(순수).
// Shelf: 컬럼 칩을 X축·값·색(나누기)·필터 칸에 끌어 놓거나(누르기 대체: 칩을 누르고 칸을 누른다) 놓으면 onChange(새 body). 부른 쪽이 source.body를 바꾸고 다시 구한다.
// Gallery: 빈 틀 카드 칸 모음. 두 번 누르거나 Enter로 onAdd(spec), 격자에 끌어 놓을 수 있게 CARD_MIME으로 spec을 싣는다(놓는 쪽은 Grid).
import { useState } from "react";
import { useConstants } from "@/lib/constants";
import type { Card, Columns } from "@/lib/types";
import { CARD_MIME, galleryItems, type Spec } from "./menu/kinds";
import { SHELF_ROWS, SHELVES, applyDrop, dropCheck, setAgg, shelfChips, shelfKind, stateOf, type ShelfKey, type ShelfState } from "./panel/shelf";

type Body = NonNullable<Card["source"]["body"]>;
export type ShelfProps = { card: Card; columns: Columns["columns"]; onChange: (body: Body) => void };
const CLASS = { num: "n", date: "d", bool: "t", text: "t" } as const;
const OPS = ["=", "!=", ">", ">=", "<", "<="];

export function Shelf({ card, columns, onChange }: ShelfProps) {
  const consts = useConstants();
  const [held, setHeld] = useState<{ name: string; type: string } | null>(null);  // 누르기 대체: 눌러서 고른 칩
  const [pending, setPending] = useState<{ name: string; type: string } | null>(null);  // 필터 칸에 놓았고 값을 기다리는 컬럼
  const [msg, setMsg] = useState("");
  const [op, setOp] = useState("="), [val, setVal] = useState("");
  const [over, setOver] = useState<string>("");
  const body = card.source.body;
  if (!columns.length) return <p className="empty">컬럼을 아직 못 받았어요.</p>;
  if (!body) return null;
  const state = stateOf(body);
  const change = (next: ShelfState, why: string) => {
    const out: Body = { ...body, x: next.x, series: next.series as Body["series"], split: next.split, conditions: next.conditions };
    if (!out.split) delete out.split;
    setMsg(why);
    onChange(out);
  };
  const drop = (shelf: ShelfKey, name: string, type: string) => {
    const check = dropCheck(shelf, name, type, state);
    if (!check.ok) return setMsg(check.reason);
    if (shelf === "filter") { setPending({ name, type }); setVal(""); return setMsg(`‘${name}’의 조건을 정하세요(예: = 서울).`); }
    change(applyDrop(shelf, name, type, state), `${SHELVES[shelf]}에 ${name === SHELF_ROWS ? "행 개수" : name}`);
  };
  const chips = shelfChips(state);
  const aggs = consts?.dash.aggs ?? [];
  const pick = (name: string, type: string) => { const now = held?.name === name ? null : { name, type }; setHeld(now); setMsg(now ? `‘${name}’ 컬럼을 놓을 칸을 누르세요.` : ""); };
  const fire = () => {
    if (!val.trim() || !pending) return setMsg("조건의 값을 적어 주세요.");
    const next = applyDrop("filter", pending.name, pending.type, state, val.trim(), op);
    const name = pending.name;
    setPending(null);
    change(next, `필터에 ${name}`);
  };
  const remove = (key: ShelfKey, i: number) => {
    const next = { ...state, series: [...state.series], conditions: [...state.conditions] };
    if (key === "x") next.x = ""; else if (key === "split") next.split = ""; else if (key === "y") next.series.splice(i, 1); else next.conditions.splice(i, 1);
    change(next, `${SHELVES[key]}에서 빼기`);
  };
  return (
    <div className="shelf">
      <div className="shelf-cols" role="group" aria-label="컬럼 칩">
        {[[SHELF_ROWS, ""] as [string, string], ...columns].map(([n, t]) => (
          <button key={n} type="button" draggable aria-pressed={held?.name === n} title={n === SHELF_ROWS ? "행 개수를 세요" : t}
            className={`shelf-chip ${n === SHELF_ROWS ? "n" : CLASS[shelfKind(t)]}${held?.name === n ? " held" : ""}`} style={{ minHeight: 24 }}
            onDragStart={e => { e.dataTransfer.setData("text/plain", JSON.stringify({ name: n, type: t })); e.dataTransfer.effectAllowed = "copy"; }} onClick={() => pick(n, t)}>{n}</button>
        ))}
      </div>
      <div className="shelf-slots">
        {(Object.keys(SHELVES) as ShelfKey[]).map(key => (
          <div key={key} className={`shelf-slot${over === key ? " over" : ""}`} role="group" aria-label={`${SHELVES[key]} 칸`}
            onDragOver={e => { e.preventDefault(); setOver(key); }} onDragLeave={() => setOver("")}
            onDrop={e => {
              e.preventDefault();
              setOver("");
              try { const item = JSON.parse(e.dataTransfer.getData("text/plain")); if (item?.name) drop(key, item.name, item.type); } catch { /* 칩이 아닌 것을 놓음 */ }
            }}
            onClick={() => { if (held) { const h = held; setHeld(null); drop(key, h.name, h.type); } }}>
            <b>{SHELVES[key]}</b>
            {chips[key].map((text, i) => {
              const row = key === "y" ? state.series[i] : null;
              return (
                <span key={i} className="shelf-wrap">
                  <span className="shelf-on">
                    {row?.y && (
                      <select title="집계 방법" aria-label={`${row.y} 집계 방법`} value={row.agg} onClick={e => e.stopPropagation()}
                        onChange={e => change({ ...state, series: setAgg(state.series, i, e.target.value) }, "값 집계 바꾸기")}>
                        {(aggs.includes(row.agg) ? aggs : [row.agg, ...aggs]).map(a => <option key={a} value={a}>{consts?.dash.agg_names?.[a] ?? a}</option>)}
                      </select>
                    )}
                    {row?.y ? ` (${row.y})` : text}
                  </span>
                  <button type="button" className="ghost" title="빼기" aria-label={`${SHELVES[key]}에서 ${text} 빼기`} style={{ minWidth: 24, minHeight: 24 }} onClick={e => { e.stopPropagation(); remove(key, i); }}>×</button>
                </span>
              );
            })}
            {key === "filter" && pending && (
              <span className="shelf-form" onClick={e => e.stopPropagation()}>
                <b>{pending.name}</b>
                <select aria-label="연산자" value={op} onChange={e => setOp(e.target.value)}>{OPS.map(o => <option key={o} value={o}>{o}</option>)}</select>
                <input aria-label="조건 값" placeholder="값" style={{ width: 90 }} value={val} onChange={e => setVal(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.ctrlKey && !e.metaKey) fire(); }} />
                <button type="button" className="btn" onClick={fire}>넣기</button>
                <button type="button" className="ghost" title="필터 넣기 취소" aria-label="필터 넣기 취소" style={{ minWidth: 24, minHeight: 24 }} onClick={() => { setPending(null); setMsg(""); }}>×</button>
              </span>
            )}
            {!chips[key].length && !(key === "filter" && pending) && <span className="hint">여기에 끌어 놓기{held ? " (또는 누르기)" : ""}</span>}
          </div>
        ))}
      </div>
      <div className="hint shelf-msg" role="status" aria-live="polite">{msg}</div>
    </div>
  );
}

export type GalleryProps = { onAdd: (spec: Spec) => void };

/** 카드 모음: 칸을 두 번 누르거나 Enter를 누르면 빈 틀 카드가 생긴다 */
export function Gallery({ onAdd }: GalleryProps) {
  const consts = useConstants();
  if (!consts) return null;
  const tiles = galleryItems(consts.dash.types), groups = [...new Set(tiles.map(t => t.group))];
  return (
    <div className="gallery">
      <div className="hint">끌어서 놓거나 두 번 누르면 빈 카드가 생겨요. 데이터는 카드 설정에서 정해요.</div>
      {groups.map(g => (
        <div key={g}>
          <h5>{g}</h5>
          <div className="gal">
            {tiles.filter(t => t.group === g).map(t => (
              <button key={t.kind + (t.type ?? "")} type="button" className="gal-tile" draggable title={`${t.help} (두 번 누르거나 Enter)`} style={{ minHeight: 24 }}
                onDragStart={e => { e.dataTransfer.setData(CARD_MIME, JSON.stringify(t)); e.dataTransfer.setData("text/plain", t.name); e.dataTransfer.effectAllowed = "copy"; }}
                onDoubleClick={() => onAdd(t)} onKeyDown={e => { if (e.key === "Enter") onAdd(t); }}>
                <b>{t.name}</b><span className="hint">{t.help}</span>
              </button>
            ))}
          </div>
        </div>
      ))}
      <div className="hint">분석 결과나 쿼리 화면의 차트는 그 화면에서 [대시보드에 추가]를 눌러 넣어요.</div>
    </div>
  );
}
