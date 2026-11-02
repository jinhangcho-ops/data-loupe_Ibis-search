// 순수 계산만. DOM·SERVER·Chart에 기대지 않는다(web/도 가져다 쓴다).
// 카드 설정 글 읽기(3-2b·3-4, dashboard/dash_rules.js에서 옮김): 조건 색 규칙(cond_rules)의 식을 읽어 칸 값에 맞는지 판정하고, 상태 규칙·연결선·문장 틀을 한 줄 글에서 읽는다. HEX는 lib/dash_calc.js.
// 조건 색 규칙 {column, match, color(HEX), arrow(none·up·down·auto), scope(cell·row)}: 맞는 칸만 색을 칠하고, 여러 규칙이 맞으면 위의 규칙 하나.
//   숫자 식: `= 0`, `> 100`, `>= 1,000`, `< -5%`, `a ~ b`(사이), 목록 `1, 3, 7`(이 숫자들만).  단어 식: 정확히 `취소`, 목록 `취소, 환불`, 포함 `*지연*`, 시작 `ERR*`, 끝 `*실패`(대소문자 무시).
// 틀린 규칙(숫자 칸에 단어 식, 읽을 수 없는 식, 틀린 HEX)은 이유 문장을 돌려주고 적용하지 않는다.

// "₩1,000"·"-5%"·"1,234.5" → 숫자(%는 ÷100). 숫자가 아니면 null. 안쪽에 공백이 있으면("100, 200") 숫자가 아니다(목록으로 읽힌다).
function ruleNumber(text) {
  const t = String(text).trim().replace(/₩\s*/g, "");
  if (!/^-?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?%?$/.test(t)) return null;
  const n = parseFloat(t.replace(/,/g, "").replace("%", ""));
  return t.endsWith("%") ? n / 100 : n;
}
const sameNum = (a, b) => Math.abs(a - b) <= Math.max(1e-9 * Math.abs(b), 1e-12);  // DB 합계의 꼬리(0.1 + 0.2)는 같은 값으로
const OPERATORS = { ">=": (a, b) => a >= b || sameNum(a, b), "<=": (a, b) => a <= b || sameNum(a, b), ">": (a, b) => a > b && !sameNum(a, b), "<": (a, b) => a < b && !sameNum(a, b), "=": sameNum, "==": sameNum, "!=": (a, b) => !sameNum(a, b) };
// 식 글자 → { kind: "num"·"word", test(값) } 또는 { error }
function parseMatch(text) {
  const t = String(text ?? "").trim();
  if (!t) return { error: "식을 적어 주세요. 예: > 100, 취소, *지연*" };
  const between = t.match(/^(.+?)\s*~\s*(.+)$/);
  if (between) {
    const [a, b] = [ruleNumber(between[1]), ruleNumber(between[2])];
    if (a == null || b == null) return { error: "~ 양쪽은 숫자여야 해요. 예: 10 ~ 20" };
    return { kind: "num", test: v => OPERATORS[">="](v, Math.min(a, b)) && OPERATORS["<="](v, Math.max(a, b)) };
  }
  const op = t.match(/^(>=|<=|!=|==|=|>|<)\s*(.*)$/);
  if (op) {
    const n = ruleNumber(op[2]);
    return n == null ? { error: `‘${op[2] || ""}’은 숫자가 아니에요. 예: ${op[1]} 100` } : { kind: "num", test: v => OPERATORS[op[1]](v, n) };
  }
  const single = ruleNumber(t);
  if (single != null) return { kind: "num", test: v => sameNum(v, single) };
  const parts = t.split(",").map(p => p.trim()).filter(Boolean);
  if (parts.length > 1 || t.includes(",")) {
    const nums = (/,\s/.test(t) ? t.split(/\s*,\s+/) : parts).map(ruleNumber);  // ", "로 나눈 목록이면 1,000 같은 천 단위 쉼표는 숫자 안에 둔다
    return nums.every(n => n != null) ? { kind: "num", test: v => nums.some(n => sameNum(v, n)) } : { kind: "word", test: s => parts.some(p => p.toLowerCase() === s) };
  }
  if (t.includes("*")) {
    const core = t.replace(/^\*|\*$/g, "").toLowerCase();
    if (!core || core.includes("*")) return { error: "* 는 앞뒤에만 쓸 수 있어요. 예: *지연*, ERR*, *실패" };
    const [head, tail] = [t.startsWith("*"), t.endsWith("*")];
    return { kind: "word", test: s => head && tail ? s.includes(core) : tail ? s.startsWith(core) : s.endsWith(core) };
  }
  return { kind: "word", test: s => s === t.toLowerCase() };
}
// 규칙 하나를 컴파일: { rule, parsed, error }. columnKinds는 {컬럼: "num"·"word"}(칸 값의 종류를 알면 숫자 칸에 단어 식 같은 어긋남을 알린다)
function compileRule(rule, columnKinds = {}) {
  const parsed = parseMatch(rule.match);
  let error = parsed.error ?? "";
  if (!error && !HEX.test(rule.color ?? "")) error = `색 ‘${rule.color ?? ""}’은 HEX가 아니에요(예: #e03131)`;
  const kind = columnKinds[rule.column];
  if (!error && kind && kind !== parsed.kind) error = kind === "num" ? `‘${rule.column}’은 숫자 칸이라 단어 식은 쓸 수 없어요` : `‘${rule.column}’은 글자 칸이라 숫자 식은 쓸 수 없어요`;
  return { rule, parsed, error };
}
const compileRules = (rules = [], columnKinds = {}) => rules.map(r => compileRule(r, columnKinds));
// 칸(컬럼 column, 값 value)에 맞는 첫 규칙(맞는 것이 없으면 null). 오류가 있는 규칙은 건너뛴다. 빈 값은 맞지 않는다.
function matchRule(compiled, column, value) {
  if (value == null || value === "") return null;
  for (const c of compiled) {
    if (c.error || c.rule.column !== column) continue;
    if (c.parsed.kind === "num" ? typeof value === "number" && c.parsed.test(value) : typeof value !== "number" && c.parsed.test(String(value).toLowerCase())) return c.rule;
  }
  return null;
}
// 화살표: up ▲ / down ▼ 고정, auto는 직전 값(prev)이 있을 때만 오르면 ▲ 내리면 ▼. 화면에 같은 값으로 보이면(shown으로 바꾼 글이 같으면) 강조 없이 =.
// 색은 규칙 색, 없으면 비교 tone(better가 down이면 반대).
function ruleArrow(rule, value, prev, better = "up", shown = String) {
  let text = "";
  if (rule.arrow === "up") text = "▲"; else if (rule.arrow === "down") text = "▼";
  else if (rule.arrow === "auto" && prev != null && typeof value === "number") text = value === prev || shown(value) === shown(prev) ? "=" : value > prev ? "▲" : "▼";
  if (!text) return null;
  if (text === "=") return { text, color: null, tone: "neutral" };
  const good = text === "▲" ? better === "up" : better === "down";
  return { text, color: rule.color || null, tone: good ? "good" : "bad" };
}
// 칸 값의 종류(숫자 칸·글자 칸): 비어 있지 않은 첫 값으로
function kindOfValues(values) {
  const first = values.find(v => v != null && v !== "");
  return first == null ? null : typeof first === "number" ? "num" : "word";
}

// ---------- 한 줄 글 읽기 ----------
const ARROW_WORDS = { "": "none", 없음: "none", none: "none", 위: "up", up: "up", 아래: "down", down: "down", 자동: "auto", auto: "auto" };
const SCOPE_WORDS = { "": "cell", 칸: "cell", cell: "cell", 줄: "row", row: "row" };
// `컬럼 | 식 | #색 | 화살표(없음·위·아래·자동) | 칸·줄` → { rule } 또는 { error }
function parseRuleLine(line) {
  const p = line.split("|").map(x => x.trim());
  if (p.length < 3) return { error: "‘컬럼 | 식 | #색 | 화살표 | 칸·줄’ 모양으로 적어 주세요. 예: 상태 | *지연* | #e03131 | 없음 | 줄" };
  const [column, match, color, arrow = "", scope = ""] = p;
  if (!column) return { error: "컬럼 이름을 적어 주세요" };
  if (!(arrow in ARROW_WORDS)) return { error: `화살표는 없음·위·아래·자동 중 하나예요(‘${arrow}’)` };
  if (!(scope in SCOPE_WORDS)) return { error: `적용 범위는 칸·줄 중 하나예요(‘${scope}’)` };
  const rule = { column, match, color, arrow: ARROW_WORDS[arrow], scope: SCOPE_WORDS[scope] }, found = compileRule(rule);
  return found.error ? { error: found.error } : { rule };
}
const LEVEL_WORDS = { 주의: "warn", warn: "warn", 이상: "bad", bad: "bad" };
// `컬럼 연산자 값 단계(주의·이상)` → { rule: {column, op, value, level} } 또는 { error }. 예: cpu >= 90 이상. 첫 연산자 왼쪽 전체가 컬럼(공백 있는 이름도)
function parseStateLine(line) {
  const m = line.trim().match(/^(.+?)\s*(>=|<=|!=|>|<|=)\s*(.+?)\s+(\S+)$/);
  if (!m) return { error: "‘컬럼 연산자 값 단계’ 모양으로 적어 주세요. 예: cpu >= 90 이상" };
  if (!(m[4] in LEVEL_WORDS)) return { error: `단계는 주의나 이상이에요(‘${m[4]}’)` };
  const n = ruleNumber(m[3]);
  return { rule: { column: m[1], op: m[2], value: n ?? m[3], level: LEVEL_WORDS[m[4]] } };
}
// `출발 -> 도착` → { link: [출발, 도착] } 또는 { error }
function parseLinkLine(line) {
  const m = line.trim().match(/^(.+?)\s*(?:->|→)\s*(.+)$/);
  return m ? { link: [m[1].trim(), m[2].trim()] } : { error: "‘출발 -> 도착’ 모양으로 적어 주세요" };
}
// 여러 줄 글을 줄마다 읽는다: [{ line, ...결과 }](빈 줄은 건너뜀). 화면은 오류가 있는 줄 옆에 이유를 보인다.
const readLines = (text, parse) => text.split("\n").map(l => l.trim()).filter(Boolean).map(line => ({ line, ...parse(line) }));
// 문장 틀의 {이름}을 값 글자로 바꾼 조각들 [{ text } | { blank: 이름 }](화면이 빈칸에 색을 입힌다)
function templateParts(template) {
  return String(template ?? "").split(/(\{[^{}]+\})/).filter(Boolean).map(p => /^\{[^{}]+\}$/.test(p) ? { blank: p.slice(1, -1) } : { text: p });
}
// 규칙 하나 → 편집 칸의 한 줄 글(parseRuleLine의 반대)
const ARROW_NAMES = { none: "없음", up: "위", down: "아래", auto: "자동" };
const ruleLine = r => [r.column, r.match, r.color, ARROW_NAMES[r.arrow] ?? "없음", r.scope === "row" ? "줄" : "칸"].join(" | ");
