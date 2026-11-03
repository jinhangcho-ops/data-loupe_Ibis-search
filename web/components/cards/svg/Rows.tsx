"use client";
// recent_rows(앞 N행)와 table(집계 값 또는 원래 행 표: 머리글 정렬, 합계 줄, 직전 기간 대비 ▲▼, 조건 색 규칙)(옛 paintRecent·paintTable·renderTable·styledCell).
// 행 값은 연결돼 있을 때만 읽고 저장하지 않는다(useRows). 정렬은 화면에만(옛 card.sortBy).
import { useState } from "react";
import { alpha, compileRules, kindOfValues, matchRule, ruleArrow, showValue } from "@/lib/calc";
import { Box, useRows, type P } from "./common";

type Col = { name: string; kind?: string; agg?: string };
type Arrow = { text: string; color: string | null; tone: string } | null;

export function RecentRows({ card }: P) {
  const got = useRows(card, card.view?.rows || 10);
  return (
    <Box cls="recent">
      {got.rows ? (
        <table>
          <thead><tr>{got.columns!.map(c => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>{got.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j}>{v == null ? "" : String(v)}</td>)}</tr>)}</tbody>
        </table>
      ) : got.error ? <div className="err">{got.error}</div> : <div className="hint">{got.hint}</div>}
    </Box>
  );
}

// 칸 글자와 조건 색·화살표를 입힌 td. rule이 맞으면 배경색, arrow는 규칙 화살표 또는 직전 값 비교 ▲▼
function Cell({ text, rule, arrow, num }: { text: string; rule: { color: string } | null; arrow: Arrow; num: boolean }) {
  return (
    <td className={num ? "num" : undefined} style={rule ? { background: alpha(rule.color, 0.28) } : undefined}>
      {text}{arrow && <span className={"carrow " + arrow.tone} style={arrow.color ? { color: arrow.color } : undefined}>{" " + arrow.text}</span>}
    </td>
  );
}

function TableView({ card, columns, rows, prev }: Pick<P, "card"> & { columns: Col[]; rows: unknown[][]; prev: Record<string, unknown[]> | null | undefined }) {
  const [sort, setSort] = useState<{ i: number; dir: number } | null>(null);
  const view = card.view, names = columns.map(c => c.name), formats = (view.formats ?? {}) as Record<string, string>;
  const kinds = Object.fromEntries(names.map((n, i) => [n, kindOfValues(rows.map(r => r[i])) ?? ""]));
  const compiled = compileRules(view.cond_rules, kinds), groups = columns.filter(c => c.kind === "group").length;
  const order = rows.map((_, i) => i);
  if (sort) order.sort((a, b) => { const [x, y] = [rows[a][sort.i], rows[b][sort.i]] as [never, never]; return Number(x == null) - Number(y == null) || (x > y ? 1 : x < y ? -1 : 0) * sort.dir; });
  const sum = (i: number) => showValue(rows.reduce<number>((a, r) => a + ((r[i] as number) ?? 0), 0), formats[names[i]] ?? "number");
  return (
    <table>
      <thead><tr>{names.map((n, i) => (
        <th key={i} style={{ cursor: "pointer" }} title="눌러서 정렬해요" onClick={() => setSort({ i, dir: sort?.i === i ? -sort.dir : 1 })}>{n + (sort?.i === i ? (sort.dir > 0 ? " ▲" : " ▼") : "")}</th>
      ))}</tr></thead>
      <tbody>
        {order.map(r => {
          const row = rows[r], hits = names.map((n, i) => matchRule(compiled, n, row[i])), whole = hits.find(h => h?.scope === "row");
          const old = prev?.[row.slice(0, groups).map(String).join("\x01")];
          return (
            <tr key={r} style={whole ? { background: alpha(whole.color, 0.22) } : undefined}>
              {row.map((v, i) => {
                const fmt = formats[names[i]] ?? "number", before = columns[i].kind === "value" ? old?.[i - groups] : null, num = typeof v === "number";
                const shown = (x: unknown) => showValue(x, fmt);  // 화면 글이 같으면 =
                const arrow = (hits[i] && ruleArrow(hits[i]!, v, before, view.better, shown)) || (num && before != null && !hits[i]?.arrow ?ruleArrow({ arrow: "auto", color: "" }, v, before, view.better, shown) : null);
                return <Cell key={i} text={num ? showValue(v, fmt) : v == null ? "" : String(v)} rule={hits[i]?.scope === "cell" ? hits[i] : null} arrow={arrow} num={num} />;
              })}
            </tr>
          );
        })}
        {view.total ? (
          <tr className="ctotal">{columns.map((c, i) => (
            <td key={i} className="num">{i < groups ? (i === 0 ? "합계" : "") : ["sum", "count"].includes(c.agg ?? "") && !card.data?.values?.more && !rows.some(r => typeof r[i] === "string") ? sum(i) : "—"}</td>
          ))}</tr>
        ) : null}
      </tbody>
    </table>
  );
}

export function Table({ card }: P) {
  const v = card.data?.values as { columns: Col[]; rows: unknown[][]; prev?: Record<string, unknown[]> | null } | undefined;
  const got = useRows(card, card.view?.rows || 20, !v);
  return (
    <Box cls="table">
      {v ? <TableView card={card} columns={v.columns} rows={v.rows} prev={v.prev} />
        : got.rows ? <TableView card={card} columns={got.columns!.map(n => ({ name: n, kind: "value" }))} rows={got.rows} prev={null} />
        : got.error ? <div className="err">{got.error}</div> : <div className="hint">{got.hint}</div>}
    </Box>
  );
}
