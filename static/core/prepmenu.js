// 컬럼 우클릭의 전처리 메뉴(P4)와 값 고르기 상자들(P5-값·P11). 쿼리·전처리 화면이 함께 쓴다.
// - PrepMenu.load(): 단계 목록(GET /api/prep/ops)을 한 번 받아 둔다. items()는 받아 둔 목록으로 바로 항목을 돌려준다(못 받았으면 빈 목록을 주고 받기 시작).
// - PrepMenu.items({ column, type, screen, sample, onPick, values, flat }): 우클릭 메뉴 항목 [[라벨, 실행, 안내], …]. 라벨은 "그룹 › 이름", 컬럼 종류에 안 맞으면 실행이 null(흐리게)이고 안내가 이유.
//   flat: 테이블 파일 연결이면 true — 칸 상자에서 파일 연결이 못 하는 옵션(빈 값 처리의 앞·뒤 값 채우기)을 숨긴다(전처리 정리 칸 OPS.fill과 같은 규칙).
//   설정 칸이 있는 단계는 작은 상자에서 값을 받은 뒤 onPick(단계id, 값), 값 바꿔 쓰기(replace)는 replaceBox로 받는다.
// - PrepMenu.replaceBox({ column, type, values, onApply(spec) }): 치환 상자. spec은 서버 prep_ops replace의 새 꼴(how·pairs·ignore_case·trim·rest·to_new).
// - PrepMenu.valuesBox({ column, values, onApply(keep, values) }): 값 목록에 체크해 제외(keep=false)·만 보기(keep=true). "(빈 값)"은 null.
// - PrepMenu.value(id, st, kind): 단계 칸의 입력 상태 → 서버로 보낼 값(전처리 화면 OPS도 이것을 쓴다). 쓸 수 없으면 null.
// values()는 Promise<값[] | {values, more, partial}>(/api/values 그대로 줘도 된다). common.js의 el·api가 먼저 불려 있어야 한다.
const PrepMenu = (() => {
  let ops = null, loading = null;
  const load = () => loading ??= api("/api/prep/ops").then(d => ops = d.ops).catch(e => { loading = null; throw e; });  // 실패한 약속을 붙들어 두면 다시 받을 수 없다
  // 서버·화면의 타입 이름(정수·문자·날짜시간·목록<…>, int·string·timestamp …)을 단계의 need 종류로
  const KINDS = [[/^(구조|struct|map|json)/i, "struct"], [/^(목록|array|list)/i, "list"], [/^(문자|글자|string|str|text|varchar|char)/i, "str"],  // struct가 str보다 먼저(앞 글자가 같다)
    [/^(정수|실수|숫자|u?int|float|double|decimal|num|bigint|real)/i, "num"]  /* uint8~uint64도 */, [/^(날짜|date|timestamp|time)/i, "date"]];
  const kindOf = type => KINDS.find(([re]) => re.test(String(type ?? "")))?.[1] ?? "other";
  const NEED = { str: "글자", num: "숫자", date: "날짜", struct: "구조·JSON", list: "목록" };
  const parsed = v => { try { JSON.parse(v); return true; } catch { return false; } };
  // 글자 컬럼이어도 표본 5개 중 3개 이상이 JSON({…}·[…])이면 구조·목록 단계를 켠다
  const looksJson = sample => (sample?.() ?? []).slice(0, 5).filter(v => typeof v === "string" && /^\s*[[{]/.test(v) && parsed(v)).length >= 3;

  // 표 전체 통계·값 목록을 먼저 읽는 단계: 쿼리 계산 줄로는 서버가 거절한다(calc.prep_line). 구조 컬럼 펼치기는 읽지 않아 된다. fill·outlier는 통계 방식만 서버가 거절
  const STATS = ["scale", "label", "onehot", "rare", "expand"];
  const statsOnly = (op, screen, type) => screen === "query" && STATS.includes(op.id) && !(op.id === "expand" && /^(구조|struct)/i.test(String(type ?? "")));
  // 쿼리에서 되는 옵션만 적는다: 그 밖(평균·중앙값·최빈값 채우기, 사분위수·표준편차 범위)은 표 전체 통계를 읽어 서버 calc.reads_stats가 거절한다. 새 통계 옵션도 막힌 채로 시작
  const QUERY_OPTIONS = { fill: { how: ["drop", "value", "prev", "next"] }, outlier: { by: ["range"] } };
  const FLAT_HIDDEN = { fill: { how: ["prev", "next"] } };  // 테이블 파일 연결은 행 차례가 없어 앞·뒤 값으로 채울 수 없다
  function items({ column, type, screen, sample, onPick, values, flat }) {
    if (!ops) { load().catch(e => console.warn("전처리 단계 목록을 못 받았어요:", e.message)); return []; }
    const kind = kindOf(type), json = kind === "str" && looksJson(sample), groups = [...new Set(ops.map(o => o.group))];
    return [...ops].sort((a, b) => groups.indexOf(a.group) - groups.indexOf(b.group)).map(op => {
      const fits = op.need === "any" || op.need === kind || (json && (op.need === "struct" || op.need === "list"));
      const ok = fits && !statsOnly(op, screen, type);
      return [`${op.group} › ${op.label}`, ok ? () => pickOp(op, column, kind, onPick, values, screen, flat) : null,
        ok ? "" : fits ? "쿼리에서는 전처리 화면으로 넘겨서 하세요" : `${NEED[op.need] ?? op.need} 컬럼에만 쓸 수 있어요`];
    });
  }
  function pickOp(op, column, kind, onPick, values, screen, flat) {
    if (op.id === "replace") return replaceBox({ column, type: kind, values, onApply: spec => onPick(op.id, spec) });
    if (!(op.fields ?? []).some(f => f.name)) return onPick(op.id, true);  // 칸 없는 단계(trim·label·onehot·expand …)는 true
    fieldBox(op, column, kind, onPick, screen, flat);
  }

  // 화면 위 상자(.modal). 바깥을 누르거나 Esc면 닫힌다(Esc는 common.js가 바깥 누르기로 보낸다).
  function modal(title, cls, ...body) {
    const box = el("div", { className: `modal-box ${cls}` }), back = el("div", { className: "modal" }, box);
    box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true"); box.setAttribute("aria-label", title);
    const close = () => back.remove();
    back.onclick = e => { if (e.target === back) close(); };
    box.append(el("h3", { textContent: title }), ...body);
    document.body.append(back);
    box.querySelector("input, select, textarea, button")?.focus();
    return { el: box, close };
  }
  const named = (node, label) => { node.setAttribute("aria-label", label); return node; };
  const buttons = (...b) => el("div", { className: "row pm-actions" }, ...b);

  // 설정 칸의 입력 상태 → 서버로 보낼 값(prep.html OPS의 value와 같은 규칙). 비어 쓸 수 없으면 null.
  const list = t => String(t ?? "").split(",").map(v => v.trim()).filter(Boolean);
  const num = v => String(v ?? "").trim() === "" ? null : /^\s*[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?\s*$/i.test(v) && Number.isFinite(+v) ? +v : NaN;  // 1e999(무한)도 NaN: JSON에서 null이 되어 조용히 빈다
  const VALUE = {
    nulls: s => { const v = list(s.text); if (s.blank) v.push(""); return v.length ? v : null; },
    remove: s => s.text || null,
    regex: s => s.pattern ? [s.pattern, s.to ?? ""] : null,
    type: (s, kind) => ({ to: s.to, ...(s.clean && kind === "str" && ["int", "float"].includes(s.to) ? { clean: true } : {}), ...(s.to === "date" && s.format ? { format: s.format } : {}) }),  // 쉼표·단위 떼기는 글자 → 숫자일 때만
    fill: (s, kind) => {
      if (s.how === "value") { const v = kind === "num" ? num(s.value) : s.value ?? ""; return v === null || v === "" || Number.isNaN(v) ? null : { how: "value", value: v }; }
      if (s.how === "prev" || s.how === "next") return s.by ? { how: s.how, by: s.by } : null;
      return (s.how === "mean" || s.how === "median") && kind !== "num" ? null : { how: s.how };  // 평균·중앙값은 숫자 컬럼만
    },
    outlier: s => {
      if (s.by === "range") { const low = num(s.low), high = num(s.high); return (low === null && high === null) || Number.isNaN(low) || Number.isNaN(high) ? null : { how: s.how, by: "range", low, high }; }
      const k = num(s.k);
      return Number.isNaN(k) ? null : { how: s.how, by: s.by, ...(k === null ? {} : { k }) };
    },
    round: s => /^\d+$/.test(s.digits ?? "") ? +s.digits : null,
    bins: s => { const e = list(s.text).map(Number); return e.length >= 2 && e.every((v, i) => Number.isFinite(v) && (!i || v > e[i - 1])) ? e : null; },
    rare: s => /^\d+$/.test(s.min ?? "") && +s.min >= 2 ? { min: +s.min, label: s.label || "기타" } : null,
    parts: s => { const v = Object.keys(s).filter(k => s[k] === true); return v.length ? v : null; },
    extract: s => s.pattern && (!s.group || /^\d+$/.test(s.group)) ? { pattern: s.pattern, ...(s.group ? { group: +s.group } : {}) } : null,
    has: s => { const w = list(s.words); return w.length && w.length <= 50 && new Set(w).size === w.length ? { words: w, ...(s.regex ? { regex: true } : {}) } : null; },
    counts: s => s.sep ? { sep: s.sep } : {},
  };
  const valueOf = (id, s, kind) => { if (VALUE[id]) return VALUE[id](s, kind); const v = Object.values(s); return v.length === 1 ? (v[0] === "" ? null : v[0]) : s; };  // case·scale처럼 칸 하나면 그 값

  function fieldBox(op, column, kind, onPick, screen, flat) {
    const state = {}, msg = el("div", { className: "err" }), only = screen === "query" ? QUERY_OPTIONS[op.id] ?? {} : {}, hide = flat ? FLAT_HIDDEN[op.id] ?? {} : {};
    const rows = op.fields.map(f => {
      if (!f.name) return el("p", { className: "hint", textContent: f.hint ?? "" });
      if (f.check) { const c = el("input", { type: "checkbox", onchange: () => { state[f.name] = c.checked; } }); state[f.name] = false; return el("label", { className: "check" }, c, f.check); }
      if (f.options) {
        const shut = only[f.name] ? f.options.map(o => [].concat(o)[0]).filter(v => !only[f.name].includes(v)) : [];  // 쿼리에서 막힌 옵션은 흐리게(고를 수 없게)
        const s = named(el("select", { onchange: () => { state[f.name] = s.value; } }, ...f.options.filter(o => !(hide[f.name] ?? []).includes([].concat(o)[0])).map(o => { const [v, t] = [].concat(o); return el("option", { value: v, textContent: t ?? v, disabled: shut.includes(v), title: shut.includes(v) ? "쿼리에서는 전처리 화면으로 넘겨서 하세요" : "" }); })), f.placeholder ?? op.label);
        if (s.selectedOptions[0]?.disabled) s.value = [...s.options].find(o => !o.disabled)?.value ?? "";  // 첫 옵션이 막혔으면 쓸 수 있는 첫 옵션으로
        state[f.name] = s.value;
        return shut.length ? el("div", {}, s, el("p", { className: "hint", textContent: `${f.options.filter(o => shut.includes([].concat(o)[0])).map(o => [].concat(o)[1] ?? [].concat(o)[0]).join("·")}은 표 전체 통계를 읽어서, 쿼리에서는 전처리 화면으로 넘겨서 하세요.` })) : s;
      }
      const i = named(el("input", { placeholder: f.placeholder ?? "", oninput: () => { state[f.name] = i.value; } }), f.placeholder || f.name);
      state[f.name] = "";
      return i;
    });
    const apply = () => {
      const v = valueOf(op.id, state, kind);
      if (v === null || Number.isNaN(v)) { msg.textContent = "칸을 알맞게 채워 주세요."; return; }
      m.close();
      onPick(op.id, v);
    };
    const m = modal(`${column} · ${op.label}`, "pm-box", ...rows, msg, buttons(el("button", { type: "button", textContent: "적용", onclick: apply }), el("button", { type: "button", className: "btn", textContent: "닫기", onclick: () => m.close() })));
    m.el.onkeydown = e => { if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); apply(); } };
    return m;
  }

  // 값 목록 체크(받은 값 전부, 최대 LIST_MAX개 + 찾기 칸). values()는 값 배열이나 /api/values 꼴 {values, more, partial}. withNull이면 맨 앞에 "(빈 값)"(값 null). picked()는 체크한 값들.
  const LIST_MAX = 200;  // /api/values가 주는 최대 수(result.get_values)
  function valueList(values, withNull) {
    const find = named(el("input", { placeholder: "값 찾기" }), "값 찾기"), box = el("div", { className: "pm-values" }), note = el("p", { className: "hint", textContent: "값을 읽는 중…" });
    const boxes = [];
    const add = (v, text) => { const c = el("input", { type: "checkbox" }); c._v = v; boxes.push(c); box.append(el("label", { className: "check" }, c, text)); };
    const label = v => v === "" ? "(빈 글자)" : String(v);  // 빈 글자 값도 보이고 고를 수 있게
    if (withNull) add(null, "(빈 값)");
    Promise.resolve(values?.() ?? []).then(d => {
      const vs = (Array.isArray(d) ? d : d?.values ?? []).filter(v => v != null), shown = vs.slice(0, LIST_MAX), more = !Array.isArray(d) && d?.more || vs.length > LIST_MAX;
      shown.forEach(v => add(v, label(v)));
      note.textContent = [!vs.length && "값이 없어요.", more && `앞의 ${shown.length}개만 받았어요(값 차례). 찾는 값이 없으면 조건을 직접 쓰세요.`, !Array.isArray(d) && d?.partial && "일부 행만 보고 모은 목록이에요."].filter(Boolean).join(" ");
    }).catch(e => { note.textContent = e.message; });
    find.oninput = () => { const q = find.value.trim().toLowerCase(); boxes.forEach(c => c.parentNode.classList.toggle("hidden", !!q && !c.parentNode.textContent.toLowerCase().includes(q))); };  // 보이는 이름으로 찾는다("(빈 글자)"·"(빈 값)" 포함)
    return { el: el("div", { className: "pm-list" }, find, note, box), picked: () => boxes.filter(c => c.checked).map(c => c._v) };
  }

  function valuesBox({ column, values, onApply }) {
    const vl = valueList(values, true), msg = el("div", { className: "err" });
    const go = keep => { const v = vl.picked(); if (!v.length) { msg.textContent = "값을 하나 이상 고르세요."; return; } m.close(); onApply(keep, v); };
    const m = modal(`${column} 값 고르기`, "pm-box", vl.el, msg,
      buttons(el("button", { type: "button", textContent: "고른 값 제외", onclick: () => go(false) }), el("button", { type: "button", className: "btn", textContent: "고른 값만 보기", onclick: () => go(true) })));
    return m;
  }

  // 붙여넣은 여러 줄을 [찾을, 바꿀] 쌍으로: 줄마다 탭이 있으면 첫 탭에서, 없으면 첫 쉼표에서 나눈다.
  const parsePairs = text => String(text).split(/\r?\n/).filter(l => l.trim()).map(l => { const at = l.includes("\t") ? l.indexOf("\t") : l.indexOf(","); return at < 0 ? [l.trim(), ""] : [l.slice(0, at).trim(), l.slice(at + 1).trim()]; });
  // 상자의 입력 → 치환 spec. 쓸 것이 없으면 null.
  function replaceSpec(f) {
    const pairs = f.how === "list" ? (f.picked.length ? [[f.picked, f.to]] : []) : f.how === "null_to" ? (f.to === "" ? [] : [["", f.to]])
      : f.pairs.filter(([a]) => a !== "").map(([a, b]) => [a, f.how === "to_null" ? "" : b]);
    return pairs.length ? { how: f.how, pairs, ignore_case: !!f.ignore_case, trim: !!f.trim, rest: f.rest === "value" ? f.restValue : f.rest, to_new: f.toNew ?? "" } : null;
  }
  const HOWS = [["exact", "값이 같으면"], ["contains", "글자가 들어 있으면"], ["prefix", "이 글자로 시작하면"], ["suffix", "이 글자로 끝나면"], ["regex", "정규식이 맞으면"],
    ["list", "목록에서 고른 값이면"], ["cond", "조건이 맞으면"], ["null_to", "빈 값을 채우기"], ["to_null", "이 값을 빈 값으로"]];
  let uid = 0;
  function replaceBox({ column, values, onApply }) {
    const n = ++uid, radio = (name, v, text, on) => el("label", { className: "check" }, el("input", { type: "radio", name: `${name}${n}`, value: v, checked: !!on }), text);
    const checked = name => m.el.querySelector(`input[name=${name}${n}]:checked`).value;
    const hows = el("div", { className: "pm-hows" }, ...HOWS.map(([v, t], i) => radio("pm-how", v, t, !i)));
    const pairs = el("div", { className: "pm-pairs" }), to = named(el("input", { placeholder: "바꿀 값" }), "바꿀 값");
    function pairRow(a = "", b = "") {
      const find = named(el("input", { value: a }), "찾을 값"), put = named(el("input", { value: b }), "바꿀 값");
      find.onpaste = e => {  // 표에서 복사한 여러 줄(탭·쉼표)은 줄마다 한 쌍으로
        const t = e.clipboardData?.getData("text") ?? "";
        if (!/[\n\t]/.test(t)) return;
        e.preventDefault();
        parsePairs(t).forEach(([x, y], i) => { if (i === 0) { find.value = x; put.value = y; } else pairs.append(pairRow(x, y)); });
      };
      const row = el("div", { className: "row pm-pair" }, find, el("span", { className: "pm-arrow", textContent: "→" }), put, el("button", { type: "button", className: "ghost", textContent: "×", title: "이 줄 빼기", onclick: () => row.remove() }));
      return row;
    }
    pairs.append(pairRow());
    const more = el("button", { type: "button", className: "btn", textContent: "줄 더하기", onclick: () => pairs.append(pairRow()) });
    const vl = valueList(values, false);
    const ignore = el("input", { type: "checkbox" }), trim = el("input", { type: "checkbox" });
    const restValue = named(el("input", { placeholder: "나머지에 넣을 값" }), "나머지에 넣을 값"), newName = named(el("input", { placeholder: "새 컬럼 이름", value: `${column}_바꿈` }), "새 컬럼 이름");
    const rest = el("div", { className: "pm-line" }, el("span", { textContent: "나머지 값:" }), radio("pm-rest", "keep", "그대로", true), radio("pm-rest", "null", "빈 값", false), radio("pm-rest", "value", "정한 값", false), restValue);
    const out = el("div", { className: "pm-line" }, el("span", { textContent: "결과:" }), radio("pm-out", "over", "이 컬럼에 덮어쓰기", true), radio("pm-out", "new", "새 컬럼으로", false), newName);
    const msg = el("div", { className: "err" });
    function show() {  // 방식마다 필요한 칸만
      const how = checked("pm-how"), table = !["list", "null_to"].includes(how);
      pairs.classList.toggle("hidden", !table); more.classList.toggle("hidden", !table);
      pairs.classList.toggle("find-only", how === "to_null");
      pairs.querySelectorAll(".pm-pair input:first-child").forEach(i => { i.placeholder = how === "cond" ? "조건, 예: 금액 > 100" : how === "regex" ? "정규식" : "찾을 값"; });
      vl.el.classList.toggle("hidden", how !== "list");
      to.classList.toggle("hidden", !["list", "null_to"].includes(how));
      to.placeholder = how === "null_to" ? "채울 값" : "바꿀 값";
    }
    hows.onchange = show;
    const apply = () => {
      const spec = replaceSpec({ how: checked("pm-how"), pairs: [...pairs.querySelectorAll(".pm-pair")].map(r => [...r.querySelectorAll("input")].map(i => i.value)),
        picked: vl.picked(), to: to.value, ignore_case: ignore.checked, trim: trim.checked, rest: checked("pm-rest"), restValue: restValue.value,
        toNew: checked("pm-out") === "new" ? newName.value.trim() : "" });
      if (!spec) { msg.textContent = "바꿀 값을 하나 이상 넣어 주세요."; return; }
      if (checked("pm-out") === "new" && !spec.to_new) { msg.textContent = "새 컬럼 이름을 넣어 주세요."; return; }
      m.close();
      onApply(spec);
    };
    const m = modal(`${column} 값 바꿔 쓰기`, "pm-box pm-wide", hows, pairs, more, vl.el, to,
      el("div", { className: "pm-line" }, el("label", { className: "check" }, ignore, "대소문자 무시"), el("label", { className: "check" }, trim, "앞뒤 공백 무시")), rest, out, msg,
      buttons(el("button", { type: "button", textContent: "적용", onclick: apply }), el("button", { type: "button", className: "btn", textContent: "닫기", onclick: () => m.close() })));
    show();
    return m;
  }
  return { load, items, kindOf, value: valueOf, replaceBox, valuesBox, parsePairs, replaceSpec, get ops() { return ops; } };
})();
