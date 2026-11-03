"use client";
// sentence: 문장 틀의 {빈칸}에 값·직전·비교·기준일을 넣는다. 계산 못 한 빈칸은 —(이유는 마우스를 올리면). 빈칸에도 조건 색·화살표 규칙(규칙의 컬럼 = 빈칸 이름)(옛 paintSentence)
import { alpha, compileRules, matchRule, ruleArrow, showValue, templateParts } from "@/lib/calc";
import { Box, type P } from "./common";

type Blank = { kind: string; cur?: unknown; prev?: unknown; text?: string; format?: string; error?: string };

export function Sentence({ card }: P) {
  const blanks = (card.data?.values?.blanks ?? {}) as Record<string, Blank>, view = card.view;
  const compiled = compileRules(view.cond_rules, Object.fromEntries(Object.entries(blanks).map(([n, b]) => [n, b.kind === "value" || b.kind === "prev" ? "num" : "word"])));
  const parts = templateParts(view.template);
  return (
    <Box cls="summary">
      <div className="sum-text">{parts.map((p, i) => {
        if (p.text != null) return p.text;
        const b = blanks[p.blank];
        if (!b) return <span key={i} className="blank err" title={`‘${p.blank}’ 빈칸을 아직 정하지 않았어요`}>—</span>;
        if (b.error) return <span key={i} className="blank err" title={b.error}>—</span>;
        const said = b.kind === "delta" && b.text && /보다\s*$/.test(parts[i - 1]?.text ?? "") ? b.text.replace(/^[^()]*?보다 /, "") : b.text;
        const value = b.kind === "value" ? b.cur : b.kind === "prev" ? b.prev : said, rule = matchRule(compiled, p.blank, value);
        const arrow = rule && ruleArrow(rule, b.cur, b.prev, view.better, x => showValue(x, b.format));
        return (
          <span key={i} className="blank" style={rule ? { background: alpha(rule.color, 0.28), borderRadius: 4, padding: "0 4px" } : undefined}>
            {typeof value === "number" || value == null ? showValue(value, b.format) : String(value)}
            {arrow && <span className={"carrow " + arrow.tone} style={arrow.color ? { color: arrow.color } : undefined}>{" " + arrow.text}</span>}
          </span>
        );
      })}</div>
    </Box>
  );
}
