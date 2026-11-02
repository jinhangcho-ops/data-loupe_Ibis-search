// 카드 설정 글 읽기(3-2b·3-4)의 화면 쪽: 문장 빈칸 줄 읽기(서버 집계 목록 SERVER.dash.aggs를 본다)와 규칙 편집 칸.
// 조건 색 규칙·상태 규칙·연결선 읽기와 판정(ruleNumber·parseMatch·compileRule·matchRule·ruleArrow·parseRuleLine·readLines·ruleLine …)은 lib/dash_rules.js(먼저 불러 온다).

const BLANK_WORDS = { 값: "value", 직전: "prev", 비교: "delta", 기준일: "asof" };
const FORMAT_WORDS = { 숫자: "number", "%": "percent", 금액: "currency", "₩": "currency", number: "number", percent: "percent", currency: "currency" };
// `이름 = 종류(값·직전·비교·기준일) [컬럼 [집계 [모양]]]` → { blank } 또는 { error }. 예: 매출 = 값 amt sum 금액
function parseBlankLine(line) {
  const m = line.trim().match(/^(\S+)\s*=\s*(\S+)(?:\s+(\S+))?(?:\s+(\S+))?(?:\s+(\S+))?$/);
  if (!m) return { error: "‘이름 = 종류 [컬럼 [집계 [모양]]]’ 모양으로 적어 주세요. 예: 매출 = 값 amt sum 금액" };
  if (!(m[2] in BLANK_WORDS)) return { error: `종류는 값·직전·비교·기준일 중 하나예요(‘${m[2]}’)` };
  const agg = m[4] ?? (m[3] ? "sum" : "count");
  if (!SERVER.dash.aggs.includes(agg)) return { error: `집계는 ${SERVER.dash.aggs.slice(0, 6).join("·")} … 중 하나예요(‘${agg}’). 전체 목록은 카드 추가 메뉴의 집계 칸에서 볼 수 있어요` };
  if (m[5] && !(m[5] in FORMAT_WORDS)) return { error: `모양은 숫자·%·금액 중 하나예요(‘${m[5]}’)` };
  return { blank: { name: m[1], kind: BLANK_WORDS[m[2]], y: m[3] ?? "", agg, format: FORMAT_WORDS[m[5]] ?? "number" } };
}

// ---------- 규칙 편집 칸(카드 설정과 [카드 추가] 메뉴가 함께 쓴다) ----------
// 여러 줄 글 칸 + 줄마다 읽어 본 결과(오류가 있는 줄은 그 줄 글과 이유를 칸 아래에 보인다). parse는 줄 읽기 함수(parseRuleLine 같은), onRead(읽은 줄들)는 바뀔 때마다 부른다.
function lineEditor(initial, parse, placeholder, onRead) {
  const area = el("textarea", { rows: 3, value: initial, placeholder, style: "width:100%;font-size:12px" }), errs = el("div", {});
  const read = () => {
    const found = readLines(area.value, parse);
    errs.replaceChildren(...found.filter(f => f.error).map(f => el("div", { className: "err", textContent: `${f.line} → ${f.error}` })));
    onRead?.(found);
    return found;
  };
  area.onchange = read;
  return { root: el("div", {}, area, errs), area, read };
}
// 카드의 조건 색 규칙 편집: 맞는 규칙만 view.cond_rules에 넣고, 틀린 줄은 이유를 줄 옆(아래)에 보인다. 글은 카드에 runtime(ruleText)으로 남아 틀린 줄이 사라지지 않는다.
function ruleEditor(card, onChange) {
  const editor = lineEditor(card.ruleText ?? (card.view.cond_rules ?? []).map(ruleLine).join("\n"), parseRuleLine, "컬럼 | 식 | #색 | 화살표(없음·위·아래·자동) | 칸·줄\n예: 상태 | *지연* | #e03131 | 없음 | 줄\n예: 금액 | > 1,000 | #2b8a3e | 자동 | 칸", found => {
    card.view.cond_rules = found.filter(f => f.rule).map(f => f.rule);
  });
  editor.area.onchange = () => { editor.read(); card.ruleText = editor.area.value; onChange(); };
  if (card.ruleText) editor.read();
  return el("div", { className: "bextra" }, el("div", { className: "hint", textContent: "조건에 맞는 칸에 색을 칠해요. 여러 규칙이 맞으면 위에 있는 규칙을 써요.",
    title: "숫자 식 예: = 0, > 100, < -5%, 10 ~ 20, 1, 3, 7\n글자 식 예: 취소, 취소, 환불, *지연*, ERR*, *실패" }), editor.root);
}
