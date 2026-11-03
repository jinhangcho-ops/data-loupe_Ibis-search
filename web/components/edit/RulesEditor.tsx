"use client";
// N6 — 규칙 편집(옛 dashboard/dash_rules.js lineEditor·ruleEditor, dash_menu.js의 상태 규칙·연결선 칸).
// 여러 줄 글 칸을 줄마다 읽어(calc.readLines) 맞는 줄만 onChange로 넘기고, 틀린 줄은 그 줄 글과 이유를 칸 아래에 보인다.
// 틀린 줄은 글 칸에 남는다(옛 card.ruleText): 밖에서 규칙이 바뀌었을 때(되돌리기 등)만 글을 다시 만든다. 칸을 떠날 때 읽는다(옛 onchange).
// 조건 색 규칙(view.cond_rules)은 늘, 상태 규칙(view.rules)·연결선(view.links)은 view에 그 칸이 있을 때(감시 카드) 보인다.
import { useState } from "react";
import { linkLine, parseLinkLine, parseRuleLine, parseStateLine, readLines, ruleLine, stateLine } from "@/lib/calc";
import type { CardView, CondRule, StateRule } from "@/lib/types";

export type RulesEditorProps = { view: CardView; columns: string[]; onChange: (patch: Partial<CardView>) => void };


type LineBoxProps<T> = { items: T[]; line: (item: T) => string; parse: (line: string) => { error?: string }; pick: (found: Record<string, unknown>) => T | undefined;
  placeholder: string; title?: string; onRead: (items: T[]) => void };

// 글 칸 하나(옛 lineEditor)
function LineBox<T>({ items, line, parse, pick, placeholder, title, onRead }: LineBoxProps<T>) {
  const shown = JSON.stringify(items);
  const [box, setBox] = useState({ shown, text: items.map(line).join("\n"), errors: [] as string[] });
  if (box.shown !== shown) setBox({ shown, text: items.map(line).join("\n"), errors: [] });  // 밖에서 바뀜(되돌리기·다른 카드): 글을 다시 만든다
  const read = () => {
    const found = readLines(box.text, parse);
    const good = found.map(f => pick(f as Record<string, unknown>)).filter((x): x is T => x !== undefined);
    setBox({ shown: JSON.stringify(good), text: box.text, errors: found.filter(f => f.error).map(f => `${f.line} → ${f.error}`) });
    if (JSON.stringify(good) !== shown) onRead(good);
  };
  return (
    <div>
      <textarea rows={3} value={box.text} placeholder={placeholder} title={title} style={{ width: "100%", fontSize: 12 }}
        onChange={e => setBox({ ...box, text: e.target.value })} onBlur={read} />
      {box.errors.map((e, i) => <div key={i} className="err">{e}</div>)}
    </div>
  );
}

export function RulesEditor({ view, columns, onChange }: RulesEditorProps) {
  const cols = columns.length ? `\n컬럼: ${columns.join(", ")}` : "";
  return (
    <div className="bextra">
      <div className="hint" title={"숫자 식 예: = 0, > 100, < -5%, 10 ~ 20, 1, 3, 7\n글자 식 예: 취소, 취소, 환불, *지연*, ERR*, *실패"}>조건에 맞는 칸에 색을 칠해요. 여러 규칙이 맞으면 위에 있는 규칙을 써요.</div>
      <LineBox<CondRule> items={view.cond_rules ?? []} line={ruleLine} parse={parseRuleLine} pick={f => f.rule as CondRule | undefined} title={cols.trim() || undefined}
        placeholder={"컬럼 | 식 | #색 | 화살표(없음·위·아래·자동) | 칸·줄\n예: 상태 | *지연* | #e03131 | 없음 | 줄\n예: 금액 | > 1,000 | #2b8a3e | 자동 | 칸"}
        onRead={cond_rules => onChange({ cond_rules })} />
      {view.rules !== undefined && <LineBox<StateRule> items={view.rules} line={stateLine} parse={parseStateLine} pick={f => f.rule as StateRule | undefined} title={cols.trim() || undefined}
        placeholder={"상태 규칙(한 줄에 하나): 컬럼 연산자 값 단계\n예: cpu >= 90 이상 / 상태 = DOWN 주의"} onRead={rules => onChange({ rules })} />}
      {view.links !== undefined && <LineBox<[string, string]> items={view.links} line={linkLine} parse={parseLinkLine} pick={f => f.link as [string, string] | undefined}
        placeholder={"연결선(한 줄에 하나): 출발 -> 도착\n예: 라우터1 -> 서버1"} onRead={links => onChange({ links })} />}
    </div>
  );
}
