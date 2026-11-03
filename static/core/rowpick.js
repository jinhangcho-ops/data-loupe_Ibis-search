// 고른 행 빼기·남기기(P12)와 고른 값의 조건 줄(P5-값). 쿼리·전처리 화면이 함께 쓴다. 표는 common.js mountTable의 selectable 옵션으로 고른다(g.selected).
// - RowPick.condition(g, keyCol, keep): 고른 행의 조건 줄. 키 컬럼이 있으면 "키 in 값…"(남기기)·"키 except 값…"(빼기),
//   없으면 보이는 컬럼(중첩 값 제외, 앞 8개)의 튜플 조건 "(c1, c2) in (v1, v2), (v3, v4)"(남기기)·"(c1, c2) except …"(빼기).
//   빈 값이 든 행은 튜플에 넣지 않는다(서버 except는 빈 값 행을 남긴다): RowPick.tuples(g)의 skipped로 막대가 안내한다.
// - RowPick.valueLines(column, keep, values): 값 목록에서 고른 값의 조건 줄들(빈 값 null 포함). 모두 and로 더한다.
// - RowPick.quote(값): 조건 줄의 값 인용(쉼표·or·앞뒤 공백·가운데 따옴표면 반대 따옴표로 감싼다).
// - RowPick.bar(host, g, { keyColumns, columns, onApply(line, keep) }): 고른 행이 있을 때만 보이는 [선택한 행 빼기] [선택한 행만 남기기] [선택 해제].
//   columns(컬럼 이름) → 참/거짓: 조건에 쓸 컬럼만 거른다(쿼리는 원래 컬럼, 전처리는 정리 방법을 걸지 않은 컬럼). 안내·단추 켜기·조건 줄이 모두 이 컬럼으로. 없으면 표의 컬럼 전부.
// 값 인용은 늘 따옴표 해석을 켠 규칙이다(프로그램이 만든 조건이라 화면의 따옴표 해석 칸과 상관없이, 서버가 쉼표·공백을 그대로 읽게).
const RowPick = (() => {
  const MAX = 200, COLS = 8;  // 키 값·튜플 조합이 이보다 많으면 조건 줄이 너무 길어진다(안내만 하고 막는다), 튜플에 넣는 컬럼 수
  const wrapped = v => /^(['"]).*\1$/s.test(v);
  const plain = v => v !== "" && !/(^|\s)or(\s|$)|,|^['"]/i.test(v) && v === v.trim() ? v : v.includes("'") ? `"${v}"` : `'${v}'`;
  // 가운데 따옴표(O'Brien)는 서버가 닫히지 않은 따옴표로 읽으므로 반대 따옴표로 감싼다. 튜플 안에서는 괄호도 나누는 글자라 같이 감싼다
  const wrapIf = (re, v) => { const s = plain(v); return !wrapped(s) && re.test(s) ? (s.includes("'") ? `"${s}"` : `'${s}'`) : s; };
  const both = v => typeof v === "string" && v.includes("'") && v.includes('"');  // 두 따옴표가 다 든 값은 감쌀 수 없다
  const quote = v => wrapIf(/['"]/, v);
  const part = v => wrapIf(/['"()]/, v);
  const text = v => typeof v === "string" ? v : String(v);

  function valueLines(column, keep, values) {
    const some = values.filter(v => v != null).map(v => quote(text(v))), blank = values.some(v => v == null);
    if (keep) return [some.length ? `${column} in ${some.join(", ")}${blank ? ` or ${column} is null` : ""}` : `${column} is null`];
    return [...(some.length ? [`${column} except ${some.join(", ")}`] : []), ...(blank ? [`${column} is not null`] : [])];  // except는 빈 값 행을 남기므로 빈 값은 따로 뺀다
  }
  const picked = g => [...g.selected].sort((a, b) => a - b).map(i => g.rows[i]);
  // 키가 없을 때의 튜플: { cols, combos(서로 다른 "(v1, v2)"들), skipped(빈 값이 있어 뺀 행 수), mixed(두 따옴표가 다 든 값이 있어 뺀 행 수), cut(비교할 컬럼이 COLS개보다 많아 뒤를 뺐는지) }
  function tuples(g) {
    const rows = picked(g), all = g.columns.filter(c => rows.every(r => r[c] == null || typeof r[c] !== "object")), cols = all.slice(0, COLS);  // 중첩 값은 비교할 수 없다
    const full = rows.filter(r => cols.every(c => r[c] != null)), fit = full.filter(r => !cols.some(c => both(r[c])));
    return { cols, combos: [...new Set(fit.map(r => `(${cols.map(c => part(text(r[c]))).join(", ")})`))], skipped: rows.length - full.length, mixed: full.length - fit.length, cut: all.length > COLS };
  }
  function condition(g, keyCol, keep) {
    const rows = picked(g);
    if (!rows.length) return "";
    if (keyCol) { const vs = rows.map(r => r[keyCol]).filter(v => v != null); return vs.length ? valueLines(keyCol, keep, vs)[0] : ""; }  // 키가 빈 값인 행은 뺀다: 서버는 한 줄의 ' and '를 나누지 않는다(막대가 안내)
    const { cols, combos } = tuples(g);
    return cols.length && combos.length ? `(${cols.join(", ")}) ${keep ? "in" : "except"} ${combos.join(", ")}` : "";
  }
  function bar(host, g, { keyColumns = [], columns, onApply }) {
    const grid = () => { const gr = typeof g === "function" ? g() : g; return gr && columns ? { ...gr, columns: gr.columns.filter(columns) } : gr; };  // 쓸 컬럼만 남긴 표(안내·단추·조건이 같은 컬럼을 본다)
    const info = el("span", { className: "hint" });
    const keyOf = gr => [].concat(typeof keyColumns === "function" ? keyColumns() : keyColumns).find(c => gr.columns.includes(c));
    const go = keep => { const gr = grid(), line = condition(gr, keyOf(gr), keep); if (line) onApply(line, keep); };
    const drop = el("button", { type: "button", className: "btn", textContent: "선택한 행 빼기", onclick: () => go(false) });
    const only = el("button", { type: "button", className: "btn", textContent: "선택한 행만 남기기", onclick: () => go(true) });
    const clear = el("button", { type: "button", className: "ghost", textContent: "선택 해제", onclick: () => { const gr = typeof g === "function" ? g() : g; gr.selected.clear(); gridDraw(gr, true); show(); gr.host.dispatchEvent(new CustomEvent("rowselect", { bubbles: true, detail: gr })); } });  // 다시 그리기는 원래 표로
    const box = el("div", { className: "rowpick hidden" }, info, drop, only, clear);
    box.setAttribute("role", "toolbar"); box.setAttribute("aria-label", "고른 행");
    function show() {
      const gr = grid(), n = gr?.selected?.size ?? 0;
      box.classList.toggle("hidden", !n);
      if (!n) return;
      const key = keyOf(gr), t = key ? null : tuples(gr), blank = key ? picked(gr).filter(r => r[key] == null).length : 0;
      const many = t ? t.combos.length > MAX : n > MAX, none = t ? !t.cols.length || !t.combos.length : blank === n;
      info.textContent = `${n}행을 골랐어요.` + (t && !t.cols.length ? " 조건으로 쓸 수 있는 컬럼이 없어요."
        : many ? t ? ` 서로 다른 조합이 ${MAX}개를 넘어요. 고르는 행을 줄이거나 조건을 직접 써 보세요.` : ` ${MAX}행까지만 조건으로 만들 수 있어요. 조건을 직접 써 보세요.`
        : (t?.skipped || blank ? ` 빈 값이 든 ${t?.skipped || blank}행은 조건에 넣지 않았어요.` : "") + (t?.mixed ? ` 작은따옴표와 큰따옴표가 둘 다 든 값의 ${t.mixed}행은 조건에 넣지 않았어요.` : "")
          + (t?.cut ? ` 앞 ${COLS}개 컬럼 값으로만 견줘서, 그 값이 같은 다른 행도 함께 걸려요.` : ""));
      info.classList.toggle("warn", many || none || !!blank || !!t?.skipped || !!t?.mixed || !!t?.cut);
      drop.disabled = only.disabled = many || none;
    }
    document.addEventListener("rowselect", e => { if (e.detail === (typeof g === "function" ? g() : g)) show(); });
    host.append(box);
    show();
    return { el: box, show };
  }
  return { condition, valueLines, tuples, bar, quote, MAX };  // quote: 쿼리 화면 셀 메뉴(cellValue)도 같은 인용 규칙
})();
