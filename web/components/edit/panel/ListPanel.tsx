"use client";
// B3 목록 카드의 설정 칸(plans/dash-blocks.md "B3 세부 명세 (2차)"): 정렬·줄 수(view.list_sort·list_rows, 바꾸면 다시 구함)와
// 줄 부분 더하기·빼기·차례·자리·컬럼·값 모양·색 규칙·막대 기준, 줄 고르기·거를 컬럼, 값 바꾸기 단추, 그림·보고서 모양(view.list). Tools의 kindFields가 부른다.
import { useState, type ReactNode } from "react";
import type { ListCol } from "@/components/cards/svg/List";
import { useShell } from "@/components/shell/ShellProvider";
import { cardRequest, computeCard } from "@/lib/api";
import { HEX, readLines } from "@/lib/calc";
import { useConstants } from "@/lib/constants";
import { freshLinks } from "@/lib/links";
import { computedUpdate, useDash } from "@/lib/store";
import type { Card, CardData, CardView, ListPart, ListView } from "@/lib/types";
import { Check, LineBox, Sel, Txt } from "../menu/Fields";
import { MarkPanel } from "./MarkPanel";

const MAX_LABEL = 30;  // 단추 이름 글자 상한(서버 dash_store.MAX_FRAME_LABEL)
const PART_NAMES: Record<ListPart["kind"], string> = { text: "글", chip: "칩", tag: "꼬리표", number: "숫자", bar: "작은 막대" };  // 모양의 상수 이름
const FORMATS: [string, string][] = [["number", "숫자"], ["percent", "%"], ["currency", "금액"]];
const BASES: [string, string][] = [["max", "최댓값 대비"], ["sum", "합계 몫"]];
const SIDES: [string, string][] = [["start", "앞쪽"], ["end", "뒤쪽"]];
const PICKS: [string, string][] = [["none", "안 함"], ["one", "하나만"], ["many", "여러 줄"]];
const REPORTS: [string, string][] = [["screen", "화면 그대로"], ["table", "표로"]];
const RULE_SEP = "|";
const RULE_ERROR = "식 | #색 꼴로 적어 주세요.";
const COLOR_ERROR = "색은 #rgb 또는 #rrggbb로 적어 주세요.";
const NEED_LABEL = "단추 이름을 적어 주세요.";

type Rule = ListPart["rules"][number];
const ruleLine = (r: Rule): string => `${r.match} ${RULE_SEP} ${r.color}`;
const parseRule = (line: string): { error?: string; rule?: Rule } => {
  const at = line.lastIndexOf(RULE_SEP);
  const match = line.slice(0, at).trim();
  if (at < 0 || !match) return { error: RULE_ERROR };
  const color = line.slice(at + 1).trim();
  return HEX.test(color) ? { rule: { match, color } } : { error: COLOR_ERROR };
};

// 색 규칙 글 칸: 틀린 줄도 글에 남기고(맞는 줄만 저장), 밖에서 규칙이 바뀌었을 때만 글을 다시 만든다(RulesEditor와 같은 꼴)
function RuleBox({ rules, onRead }: { rules: Rule[]; onRead: (rules: Rule[]) => void }): ReactNode {
  const shown = JSON.stringify(rules);
  const [box, setBox] = useState({ shown, text: rules.map(ruleLine).join("\n") });
  if (box.shown !== shown) setBox({ shown, text: rules.map(ruleLine).join("\n") });
  const change = (text: string): void => {
    const good = readLines(text, parseRule).flatMap(f => ((f as Record<string, unknown>).rule as Rule | undefined) ?? []);
    setBox({ shown: JSON.stringify(good), text });
    if (JSON.stringify(good) !== shown) onRead(good);
  };
  return <LineBox label="색 규칙" value={box.text} onChange={change} parse={parseRule} placeholder="한 줄에 하나: 식 | #색" />;
}

/** card: 고른 목록 카드. data: 보이는 값(구한 값이 있으면 그것). onChange: 고친 카드 조각(view 통째)을 받는다 */
export type ListPanelProps = { card: Card; data: CardData; onChange: (patch: Partial<Card>) => void };

/** [SPECIFICATION] plans/dash-blocks.md#ListPanel
 *  목록 카드의 줄 부분과 고르기·값 바꾸기·정렬·줄 수·보고서 모양 칸을 그린다. */
export function ListPanel({ card, data, onChange }: ListPanelProps): ReactNode {
  const consts = useConstants();
  const { state, dispatch } = useDash();
  const { page } = useShell();
  const [msg, setMsg] = useState("");
  if (!consts) return null;
  const d = consts.dash.list;
  const list = card.view.list ?? d.default.list;
  const sort = card.view.list_sort ?? d.default.list_sort;
  const rows = card.view.list_rows ?? d.default.list_rows;
  const cols = (data.values?.columns as ListCol[] | undefined) ?? [];
  const groups = cols.filter(c => c.kind === "group").map(c => c.name);
  const values = cols.filter(c => c.kind === "value").map(c => c.name);
  const names = [...groups, ...values];
  const put = (next: ListView, extra: Partial<CardView> = {}): void => onChange({ view: { ...card.view, list: next, ...extra } });
  const putPart = (i: number, p: Partial<ListPart>): void => put({ ...list, parts: list.parts.map((x, j) => (j === i ? { ...x, ...p } : x)) });
  const move = (i: number, to: number): void => {
    const parts = [...list.parts];
    [parts[i], parts[to]] = [parts[to], parts[i]];
    put({ ...list, parts });
  };
  const putSwap = (i: number, s: Partial<ListView["swaps"][number]>): void => put({ ...list, swaps: list.swaps.map((x, j) => (j === i ? { ...x, ...s } : x)) });
  // 정렬·줄 수가 바뀌면 Tools의 recompute와 같은 길로 다시 구한다
  const resort = (patch: Partial<CardView>): void => {
    const next: Card = { ...card, view: { ...card.view, ...patch } };
    onChange({ view: next.view });
    computeCard(cardRequest({ ...next, data: {} }), state.ctx, state.dash?.id ?? "").then(r => {
      const u = computedUpdate(card.id, r.data, state.ctx, { view: next.view, source: { ...next.source, ...r.source } });
      onChange(u.patch);
      dispatch({ type: "values", results: [u.result] });
      setMsg("");
    }, e => setMsg((e as Error).message));
  };
  const pickKey = (pick: ListView["pick"], key: string): void => put({ ...list, pick, key }, key && !card.view.link_to && state.dash ? { link_to: freshLinks(card, state.dash, page) } : {});
  const newPart: ListPart = { kind: d.part_kinds[0], column: groups[0] ?? values[0] ?? "", swap: false, format: "number", rules: [], basis: d.bases[0], side: d.sides[0] };
  return (
    <div>
      <Sel label="정렬 컬럼" value={sort.column} blank="묶음 컬럼 순" options={names} onChange={column => resort({ list_sort: { ...sort, column } })} />
      <Check label="내림차순" checked={sort.desc} onChange={desc => resort({ list_sort: { ...sort, desc } })} />
      <Txt label="줄 수" type="number" min={1} max={d.max_rows} value={String(rows)} onChange={v => {
        const n = Math.round(Number(v));
        if (v !== "" && Number.isFinite(n)) resort({ list_rows: Math.min(d.max_rows, Math.max(1, n)) });
      }} />
      {msg && <div className="err" role="alert">{msg}</div>}
      {list.parts.map((p, i) => (
        <div key={i} className="cm-block">
          <Sel label="모양" value={p.kind} options={d.part_kinds.map((k): [string, string] => [k, PART_NAMES[k]])} onChange={kind => putPart(i, { kind: kind as ListPart["kind"] })} />
          {list.swaps.length > 0 && <Check label="값 바꾸기 단추를 따름" checked={p.swap} onChange={swap => putPart(i, { swap })} />}
          {!p.swap && <Sel label="컬럼" value={p.column} options={[...names, ...(card.view.row_marks?.fields ?? []).map(f => f.name)]} onChange={column => putPart(i, { column })} />}
          <Sel label="값 모양" value={p.format} options={FORMATS} onChange={format => putPart(i, { format: format as ListPart["format"] })} />
          {p.kind === "bar" && <Sel label="막대 기준" value={p.basis} options={BASES} onChange={basis => putPart(i, { basis: basis as ListPart["basis"] })} />}
          <Sel label="자리" value={p.side} options={SIDES} onChange={side => putPart(i, { side: side as ListPart["side"] })} />
          <RuleBox rules={p.rules} onRead={rules => putPart(i, { rules })} />
          <button type="button" className="ghost" disabled={i === 0} onClick={() => move(i, i - 1)}>위</button>
          <button type="button" className="ghost" disabled={i === list.parts.length - 1} onClick={() => move(i, i + 1)}>아래</button>
          <button type="button" className="ghost" onClick={() => put({ ...list, parts: list.parts.filter((_, j) => j !== i) })}>빼기</button>
        </div>
      ))}
      {list.parts.length < d.max_parts && <button type="button" className="ghost" onClick={() => put({ ...list, parts: [...list.parts, newPart] })}>부분 더하기</button>}
      {/* 묶음 컬럼이 없으면 거를 값이 없어 고르기를 켤 수 없다. 켤 때 거를 컬럼이 비었으면 첫 묶음 컬럼으로 시작한다(아래 칸에서 바꾼다) */}
      <Sel label="줄 고르기" value={list.pick} options={groups.length ? PICKS : PICKS.slice(0, 1)} onChange={pick => pickKey(pick as ListView["pick"], pick === "none" ? "" : list.key || groups[0])} />
      {list.pick !== "none" && <Sel label="거를 컬럼" value={list.key} blank="" options={groups}
        onChange={v => pickKey(list.pick, v)} />}
      {list.swaps.map((s, i) => (
        <div key={i} className="cm-block">
          {/* 글자 상한은 maxLength 대신 입력을 자른다(Txt에 maxLength 칸이 없다) */}
          <Txt label="단추 이름" value={s.label} onChange={label => putSwap(i, { label: label.slice(0, MAX_LABEL) })} />
          <Sel label="값 컬럼" value={s.value} options={values} onChange={value => putSwap(i, { value })} />
          {s.label === "" && <div className="err" role="alert">{NEED_LABEL}</div>}
          <button type="button" className="ghost" onClick={() => { const swaps = list.swaps.filter((_, j) => j !== i); put({ ...list, swaps, parts: swaps.length ? list.parts : list.parts.map(p => ({ ...p, swap: false })) }); }}>빼기</button>
        </div>
      ))}
      {list.swaps.length < d.max_swaps && <button type="button" className="ghost" onClick={() => put({ ...list, swaps: [...list.swaps, { label: "", value: values[0] ?? "" }] })}>단추 더하기</button>}
      <Sel label="그림·보고서 모양" value={list.report} options={REPORTS} onChange={report => put({ ...list, report: report as ListView["report"] })} />
      <MarkPanel card={card} groups={groups} onChange={onChange} onApply={resort} />
    </div>
  );
}
