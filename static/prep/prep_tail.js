// ---------- 컬럼 줄 우클릭 메뉴(P4·P5·P5-값·P11), 새 컬럼 상자(P9), 쿼리에서 넘어온 검색(P10) ----------
// 컬럼의 값 목록(많이 나온 순 앞 20개, 표본 기준). 값 골라서 제외·만 보기, 값 바꾸기 상자가 쓴다.
const valuesOf = name => api("/api/analyze", { recipe: { schema: S.schema, base: S.base }, how: { kind: "distribution", column: name }, sample, sample_how: sampleHow() }).then(d => ({ values: d.values?.labels ?? [], more: (d.values?.other ?? 0) > 0, partial: true }));  // 표본에서 많이 나온 앞 20개라, 상자가 "일부 행만 보고 모은 목록"이라고 알린다
const sampleOf = name => (S.handed?.shown?.rows ?? first?.rows ?? []).map(r => r[name]);  // JSON 글자인지 볼 값들
// 단계를 컬럼에 넣는다: 서버 꼴의 값을 입력 상태로 바꿔 edits에(원-핫처럼 원래 컬럼을 빼는 단계는 빼기까지)
function putOp(name, id, value) {
  const e = edits[name];
  e.ops[id] = stateOf(id, value);
  if (OPS[id].unkeep) { e.keepBefore ??= e.keep; e.keep = false; preview = null; }
}
// 우클릭 메뉴·값 바꾸기 상자에서 고른 단계: 넣고, 그 컬럼의 ③ 정리 칸을 열어 입력 칸에 초점을 둔다
function addOp(name, id, value) {
  if (!edits[name] || !OPS[id]) return fail(`'${name}' 컬럼에 이 정리 방법을 넣을 수 없어요.`);
  fail("");
  putOp(name, id, value);
  showAll();
  pickColumn(name);
  const line = lines.get(edits[name].ops[id]);
  line?.scrollIntoView({ block: "nearest" });
  if (OPS[id].fields) line?.querySelector(":scope > input, :scope > select, :scope > label input, :scope > div .btn")?.focus();
}
// 컬럼 줄 우클릭 메뉴 항목 [[라벨, 실행|null, 안내], …]: 컬럼 빼기, 값 골라서 제외·만 보기, 값 바꾸기, 전처리 단계 전부
function colItems(name) {
  const c = cols().find(x => x.name === name), d = deriv.find(x => x.name === name);
  if (!c && d) return [["이 컬럼 빼기", drops.includes(name) ? null : () => { drops = [...drops, name]; showAll(); }, "이미 뺐어요"]];
  if (!c) return null;
  const e = edits[name], src = c.from ?? c.name;  // 조건은 원래 컬럼 이름으로 쓴다
  const box = () => PrepMenu.valuesBox({ column: src, values: () => valuesOf(src), onApply: (keep, vals) => { RowPick.valueLines(src, keep, vals).forEach(l => filters.push(l)); syncFilters(); } });
  return [["이 컬럼 빼기", e.keep ? () => { e.keep = false; preview = null; showAll(); } : null, "이미 뺐어요"],
    ["값 골라서 제외·만 보기…", box],
    ...PrepMenu.menu({ column: name, type: c.type, sample: () => sampleOf(src), values: () => valuesOf(src), onPick: (id, v) => addOp(name, id, v), flat: connected === "flat", units: prof?.cast_units })];
}
// 컬럼 줄 우클릭(메뉴 상자·키보드·위치·초점은 common.js ctxMenu). 입력칸·표·선택한 글자 위에서는 브라우저 기본 메뉴를 둔다. 메뉴 키·Shift+F10은 그 컬럼 줄 아래에
document.oncontextmenu = e => { const row = e.target.closest?.("#pcols .pcol"); ctxMenu(e, row && !getSelection()?.toString() ? colItems(row.dataset.col) : null, row?.getBoundingClientRect()); };

// [+] 새 컬럼 상자: 계산 줄들을 전처리(컬럼의 정리 방법)와 계산 컬럼(formulas)으로 나눈다.
// `전처리 <단계> <컬럼> <JSON>` → 그 컬럼의 정리 방법, `식 <식> 이름 <새 이름>` → {name, expr}, 그 밖(그룹집계·창 함수·펼치기, 계산 컬럼에 거는 전처리)은 {line}
function planLines(lines) {
  const ops = [], made = [], names = Object.keys(edits).sort((a, b) => b.length - a.length);
  for (const line of lines) {
    const m = /^전처리 (\S+) (.+)$/s.exec(line), col = m && OPS[m[1]] && names.find(n => m[2].startsWith(n + " "));
    if (col) { try { ops.push([col, m[1], JSON.parse(m[2].slice(col.length + 1))]); continue; } catch { /* JSON이 아니면 줄 그대로 서버가 읽는다 */ } }
    const f = /^식 (.+) 이름 (.+)$/s.exec(line);
    made.push(f ? { name: f[2].trim(), expr: f[1].trim() } : { line });
  }
  return { ops, made };
}
// 상자에서 고를 수 있는 컬럼 [이름, 타입]: 남긴 컬럼(식에는 이름을 바꾸기 전 이름), 미리보기에서 알게 된 새 컬럼, 아직 미리보지 않은 계산 컬럼
const newColCols = () => !prof ? [] : [...kept().map(c => [c.name, c.type]), ...deriv.map(d => [d.name, d.type]), ...formulas.filter(f => f.name && !deriv.some(d => d.name === f.name)).map(f => [f.name, "?"])];
// 미리보기(5행): 지금 전처리에 이 줄들을 더한 recipe로 서버에 묻고, 새로 생긴·바뀐 컬럼만 보인다
async function previewLines(lines) {
  const r = recipe(), { ops, made } = planLines(lines);
  for (const [col, id, v] of ops) r.columns[col] = { ...r.columns[col], ops: { ...r.columns[col]?.ops, [id]: v } };
  r.formulas = [...(r.formulas ?? []), ...made];
  const res = await api("/api/preview", r), got = res.rows, touched = new Set(ops.map(o => o[0]));
  const fresh = got.columns.filter((n, k) => touched.has(n) || (res.derived[k] && !deriv.some(d => d.name === n)));
  return { columns: fresh.length ? fresh : got.columns, rows: got.rows.slice(0, 5) };
}
function addLines({ lines }) {
  const { ops, made } = planLines(lines), dup = made.find(f => f.name && formulas.some(x => x.name === f.name));
  if (dup) throw new Error(`'${dup.name}' 계산 컬럼이 이미 있어요. 다른 이름을 적어 주세요.`);  // 새 컬럼 상자(C1)가 닫히지 않고 입력을 둔 채 이유를 상자 안에 보인다
  ops.forEach(([col, id, v]) => putOp(col, id, v));
  formulas.push(...made);
  fail("");
  preview = null;
  showAll();
  document.querySelectorAll("#prep-view .step").forEach(s => s.classList.toggle("open", s.dataset.step === (made.length ? "derive" : "clean")));
  showPrepSummaries();
}
function openNewCol() {
  if (!prof) return fail("먼저 테이블을 골라 주세요.");
  NewCol.open({ columns: newColCols, screen: "prep", funcs: Funcs, groupable: true, values: valuesOf, onPreview: previewLines, onAdd: addLines });
}
$("nc-add").onclick = openNewCol;

// 쿼리에서 넘어온 검색(@이름)이면 넘어온 쪽을 받아 두고 ①을 한 줄로 바꾼다. 받지 못하면(프로그램을 다시 켬 …) 알리고 평소 화면 그대로 둔다.
async function loadHanded(base) {
  try {
    const d = await api("/api/prep/handed?base=" + encodeURIComponent(base)), s = d.shown;
    (S.tabBases ??= new Set()).add(d.body?.base);
    S.handed = { base, name: d.name, shown: s && { ...s, rows: s.rows.map(r => Array.isArray(r) ? Object.fromEntries(s.columns.map((c, i) => [c, r[i]])) : r) } };
    renderGrid();  // 프로필·통계를 읽기 전에 넘어온 표를 먼저 보인다
  } catch (e) { fail(e.message); }
}
$("handed-back").onclick = () => document.referrer ? history.back() : (location.href = "/search");
PrepMenu.load().catch(e => console.warn("전처리 단계 목록을 못 받았어요:", e.message));  // 우클릭 메뉴가 쓸 전처리 단계 목록

showAll();
showTarget();
// 연결을 이어받은 뒤, 주소에 ?base=이름이 있고 그 테이블이 목록에 있으면 고른 것과 같게 한다(전처리로 보내기).
showView(location.hash === "#analysis" || location.hash === "#graphs" ? "ana" : "prep");  // 예전 주소 #graphs도 분석으로
showPrepSummaries();
// 마지막으로 고른 테이블(common.js의 lastTable)이 이 연결에 있으면 고른 것과 같게 한다. 스키마가 다르면 스키마부터 바꾼다.
async function restoreLastTable() {
  const last = lastTable();
  if (!last || last.conn !== connName || S.base) return;
  if (last.schema !== S.schema && [...$("schema").options].some(o => o.value === last.schema)) { $("schema").value = last.schema; await loadTables(); }
  if ([...$("base").options].some(o => o.value === last.base)) { $("base").value = last.base; return $("base").dispatchEvent(new Event("change")); }
}
resume().then(async () => {
  showPrepSummaries();
  const base = new URLSearchParams(location.search).get("base");
  if (base && base.startsWith("@") && ![...$("base").options].some(o => o.value === base))  // 쿼리에서 파일 없이 보낸 검색(F08): 서버가 "@이름"으로 기억한다. 목록에 항목을 더해 고른다
    $("base").append(el("option", { value: base, textContent: `${base.slice(1)} (검색 결과)` }));
  if ([...$("base").options].some(o => o.value === base)) {
    $("base").value = base;
    if (base.startsWith("@")) await loadHanded(base);  // 넘어온 표를 먼저 그린 뒤 프로필·통계를 구한다
    return $("base").onchange();
  }
  return restoreLastTable();  // 주소에 테이블이 없으면 마지막에 쓴 테이블(쿼리에서 고른 것 포함)로
});
