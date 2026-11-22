"use client";
// B3 목록 카드(kind list, plans/dash-blocks.md "B3 세부 명세 (2차)"): 서버 dash_calc.list_data가 구한 값(data.values {columns, data(컬럼별 값), more})을
// 사용자가 만든 줄 부분들(view.list.parts)대로 줄마다 그린다. 줄 고르기는 몸통의 onPress(Grid → shell.press, 값 필터 {column, in: [...]}),
// 값 바꾸기 단추(view.list.swaps)는 카드 안에서만 바뀌고 저장하지 않는다. 그림·보고서가 "표로"면 같은 값을 표(.lprint)로도 그린다.
import { useState, type CSSProperties, type ReactNode } from "react";
import { csvText, type ChartTable } from "@/lib/autoviz";
import type { CardData, ListView } from "@/lib/types";
import { alpha, compileRules, kindOfValues, matchRule, showValue } from "@/lib/calc";
import { Box, Empty, type P } from "./common";

/** 꼬리표 바탕 색의 진하기(표 카드 Rows.tsx 칸 색 규칙과 같은 값) */
export const LIST_TAG_ALPHA = 0.28;
/** 값이 아직 없는 목록의 안내(표 카드 Rows.tsx와 같은 글) */
export const LIST_NO_VALUES = "아직 구한 값이 없어요";
/** 줄 부분을 아직 하나도 더하지 않은 목록의 안내 */
export const LIST_NO_PARTS = "카드 설정에서 줄에 넣을 부분을 더하세요.";
/** Excel이 한글을 바로 읽게 CSV 맨 앞에 붙이는 글자(Report.tsx CSV_BOM과 같다) */
export const LIST_CSV_BOM = "﻿";

/** 목록 값의 컬럼 하나(dash_calc.list_data의 columns): 묶음(group) 또는 값(value) */
export type ListCol = { name: string; kind?: string; agg?: string };
/** 줄 안 부분 하나를 그릴 것: 모양·자리, 글자, 색 규칙의 색(없으면 null), 막대 몫 0~1(막대가 아니면 null) */
export type ListCell = { kind: ListView["parts"][number]["kind"]; side: "start" | "end"; text: string; color: string | null; ratio: number | null };
/** 목록 줄 하나: 거를 값(고르기를 안 하거나 거를 컬럼 값이 비면 null)과 부분들(설정 차례) */
export type ListRow = { key: string | null; cells: ListCell[] };

/** [SPECIFICATION] plans/dash-blocks.md#listRows
 *  컬럼별 값과 목록 설정(지금 고른 값 바꾸기 단추 swap)으로 줄마다 그릴 부분들을 만든다. */
export function listRows(columns: ListCol[], data: unknown[][], list: ListView, swap: number): ListRow[] {
  const at = (name: string): number => {
    const i = columns.findIndex(c => c.name === name);
    if (i === -1) throw new Error(`목록 컬럼 '${name}'이 값에 없어요. 카드 설정에서 다시 고르세요.`);
    return i;
  };
  const n = data[0]?.length ?? 0;
  const ki = list.pick !== "none" ? at(list.key) : -1;
  const parts = list.parts.map(p => {
    const name = p.swap ? list.swaps[swap]?.value ?? "" : p.column;
    const i = at(name);
    const texts = data[i].map(v => (typeof v === "number" ? showValue(v, p.format) : v == null ? "" : String(v)));
    const compiled = p.rules ? compileRules(p.rules.map(r => ({ column: name, match: r.match, color: r.color, arrow: "none", scope: "cell" })), { [name]: kindOfValues(data[i]) ?? "" }) : null;
    const colors = data[i].map(v => (compiled ? matchRule(compiled, name, v)?.color ?? null : null));
    const pos = data[i].map(v => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : 0));
    const base = p.basis === "sum" ? pos.reduce((a, b) => a + b, 0) : Math.max(0, ...pos);
    const ratios = pos.map(v => (p.kind === "bar" ? (base > 0 ? v / base : 0) : null));
    return { p, texts, colors, ratios };
  });
  return Array.from({ length: n }, (_, r) => ({
    key: ki !== -1 && list.key ? (data[ki][r] == null ? null : String(data[ki][r])) : null,
    cells: parts.map(q => ({ kind: q.p.kind, side: q.p.side, text: q.texts[r], color: q.colors[r], ratio: q.ratios[r] })),
  }));
}

/** [SPECIFICATION] plans/dash-blocks.md#nextPick
 *  줄 하나를 눌렀을 때 새로 고른 값들(하나만: 바꾸거나 풀기, 여러 줄: 넣거나 빼기). */
export function nextPick(pick: "one" | "many", now: string[], key: string): string[] {
  return pick === "one" ? (now.length === 1 && now[0] === key ? [] : [key]) : now.includes(key) ? now.filter(x => x !== key) : [...now, key];
}

/** [SPECIFICATION] plans/dash-blocks.md#tableChart
 *  컬럼별 값을 CSV·표로 쓸 표(ChartTable, autoviz.csvText가 받는 꼴)로 바꾼다. */
export function tableChart(columns: ListCol[], data: unknown[][]): ChartTable {
  const names = columns.map(c => c.name);
  const n = data[0]?.length ?? 0;
  return { columns: names, rows: Array.from({ length: n }, (_, r) => Object.fromEntries(names.map((nm, i) => {
    const v = data[i][r];
    return [nm, typeof v === "number" ? v : v == null ? null : String(v)];
  }))) };
}

/** [SPECIFICATION] plans/dash-blocks.md#listCsv
 *  목록 카드의 구한 값을 CSV 글(맨 앞 LIST_CSV_BOM)로 만든다. 값이 없으면 던진다. */
export function listCsv(data: CardData): string {
  const v = data.values as { columns?: ListCol[]; data?: unknown[][] } | undefined;
  if (!v?.columns || !v.data) throw new Error("이 목록에는 아직 구한 값이 없어요.");
  return LIST_CSV_BOM + csvText(tableChart(v.columns, v.data));
}

/** [SPECIFICATION] plans/dash-blocks.md#List
 *  목록 카드를 사용자가 만든 줄 부분대로 그린다(줄 고르기·값 바꾸기 단추·보고서용 표). */
export function List(p: P): ReactNode {
  const { card, onPress, pressed } = p;
  const [swap, setSwap] = useState(0);
  const v = card.data?.values as { columns?: ListCol[]; data?: unknown[][] } | undefined;
  const list = card.view.list as ListView | undefined;
  if (!v?.columns || !v.data) return <Box cls="list"><div className="hint">{LIST_NO_VALUES}</div></Box>;
  if (!list?.parts.length) return <Box cls="list"><div className="hint">{LIST_NO_PARTS}</div></Box>;
  const at = swap < list.swaps.length ? swap : 0;
  const rows = listRows(v.columns, v.data, list, at);
  if (!rows.length) return <Box cls="list"><Empty /></Box>;
  const cell = (c: ListCell, i: number): ReactNode => {
    const color = c.color ? { color: c.color } : undefined;
    if (c.kind === "text") return <span key={i} className="ltext" style={color}>{c.text}</span>;
    if (c.kind === "chip") return <span key={i} className="chip lchip" style={c.color ? { color: c.color, borderColor: c.color } : undefined}>{c.text}</span>;
    if (c.kind === "tag") return <span key={i} className="ltag" style={c.color ? { background: alpha(c.color, LIST_TAG_ALPHA), borderColor: c.color } : undefined}>{c.text}</span>;
    if (c.kind === "number") return <b key={i} className="lnum" style={color}>{c.text}</b>;
    return <span key={i} className="lbar" title={c.text}><i style={{ "--r": c.ratio, ...(c.color ? { "--c": c.color } : {}) } as CSSProperties} /><small>{c.text}</small></span>;
  };
  return (
    <Box cls="list">
      {list.swaps.length > 0 && <div className="fr-seg lswaps" role="group" aria-label={card.title}>{list.swaps.map((s, i) => <button key={i} type="button" className="fr-seg-b" aria-pressed={i === at} onClick={() => setSwap(i)}>{s.label}</button>)}</div>}
      <ul className="clist lscreen" aria-label={card.title}>{rows.map((r, i) => {
        const inner = <><span className="lstart">{r.cells.filter(c => c.side === "start").map(cell)}</span><span className="lend">{r.cells.filter(c => c.side === "end").map(cell)}</span></>;
        const mine = pressed?.column === list.key ? pressed.in : [];
        const on = r.key !== null && mine.includes(r.key);
        return <li key={i}>{!!onPress && list.pick !== "none" && r.key !== null
          ? <button type="button" className="lrow" aria-pressed={on} onClick={() => onPress(list.key, nextPick(list.pick as "one" | "many", mine, r.key as string))}>{inner}</button>
          : <div className="lrow">{inner}</div>}</li>;
      })}</ul>
      {list.report === "table" && <table className="lprint"><thead><tr>{v.columns.map(c => <th key={c.name}>{c.name}</th>)}</tr></thead><tbody>{rows.map((_, r) => <tr key={r}>{v.columns!.map((c, i) => {
        const x = v.data![i][r];
        return <td key={c.name}>{typeof x === "number" ? showValue(x, "number") : x == null ? "" : String(x)}</td>;
      })}</tr>)}</tbody></table>}
    </Box>
  );
}
