// 행 태그(Phase 4): 태그 켜기·사전 창, 태그로 거르기, 결과 표 아래 태그 막대, 숫자 키.
// 켜면(labelsOn) index.html의 state()가 검색 요청마다 labels를 실어 결과에 `태그` 컬럼이 붙는다. 인라인 핸들러 없음(CSP).
// 쓰는 것: common.js의 S·SERVER·$·el·fill·api·send·busy·say, index.html의 query·runSearch·last·skip·confirmBox.
let labelsOn = false, labelUse = {};  // labelUse: {}(붙이기만) · {any: [태그…]} · {all: [태그…]} · {none: true}
const LABEL_SEP = ", ";  // 서버 labels.TAG_SEP: 태그 컬럼 글을 나눌 때
const labelButton = (text, title, run, disabled = false) => {  // run은 오류를 스스로 보인다
  const b = el("button", { className: "btn", textContent: text, title, disabled, onclick: () => busy(b, run) });
  return b;
};

const Labels = (() => {
  let info = null, key = "", why = "", mode = "";  // info: GET /api/labels 답, key: 마지막으로 읽은 schema·base, why: 켤 수 없는 이유(@탭 기준 등), mode: 거르기 select 값
  const openTitle = $("label-open").title;
  const body = () => ({ schema: S.schema, base: S.base });
  const on = () => (info?.on ? info : null);
  const prune = () => { if ((labelUse.any ?? labelUse.all ?? []).some(t => !info.vocab.includes(t))) labelUse = {}; };  // 이름을 바꾸거나 지운 태그로 거르던 중이면 거르기를 푼다

  function paintTags() {
    const used = new Set(labelUse.any ?? labelUse.all ?? []), box = $("label-tags");
    box.classList.toggle("hidden", mode !== "any" && mode !== "all");
    box.replaceChildren(...(info?.vocab ?? []).map(t => el("label", { className: "check" }, el("input", { type: "checkbox", value: t, checked: used.has(t), onchange: filter }), t)));
  }
  function paint() {
    mode = labelUse.none ? "none" : labelUse.any ? "any" : labelUse.all ? "all" : mode === "any" || mode === "all" ? mode : "";  // 태그를 아직 안 고른 any·all은 그대로 둔다
    const open = $("label-open"), lit = labelsOn && !!on();
    open.disabled = !S.base || !!why;
    open.title = why || openTitle;
    open.className = lit ? "btn p" : "btn";
    open.setAttribute("aria-pressed", String(lit));
    $("label-edit").classList.toggle("hidden", !on());
    $("label-one-open").classList.toggle("hidden", !lit);
    $("label-filter").classList.toggle("hidden", !lit);
    $("label-mode").value = mode;
    paintTags();
  }
  // 거르기 select·태그 체크가 바뀌면 labelUse를 바꾸고 처음 쪽부터 다시 검색한다.
  function filter() {
    mode = $("label-mode").value;
    const tags = [...$("label-tags").querySelectorAll("input:checked")].map(i => i.value);
    const next = mode === "none" ? { none: true } : mode && tags.length ? { [mode]: tags } : {};
    const changed = JSON.stringify(next) !== JSON.stringify(labelUse);
    labelUse = next;
    paint();
    if (changed) runSearch(0);
  }
  // 지금 기준 테이블의 태그 정보를 읽는다(같은 schema·base면 다시 읽지 않는다). 못 켜는 기준(@탭·이어붙이기 등)이면 서버 문구를 단추 title에.
  async function load() {
    const next = `${S.schema}\u0000${S.base}`;
    if (next !== key) {
      key = next; info = null; why = "";
      if (S.base) {
        try { const d = await api("/api/labels?" + new URLSearchParams(body())); if (next === key) info = d; }
        catch (e) { if (next === key) why = e.message; }
      }
    }
    paint();
  }
  function reset() { labelsOn = false; labelUse = {}; mode = ""; info = null; key = ""; why = ""; paint(); }
  function merge(d) { info = { ...info, ...d }; prune(); paint(); }  // apply·vocab·rename·drop의 답을 정보에 합친다

  // 켜기 창(처음): 고유 키 컬럼·글 컬럼·처음 태그
  function setup(box, err) {
    const cols = ($("rows")._grid?.columns ?? S.cols.map(c => c[0])).filter(c => c !== SERVER.labels.column);
    const keyBox = el("select", { title: "행마다 값이 다른 컬럼이에요(정수나 글자). 이 값으로 행을 기억해요" }), textBox = el("select", { title: "[하나씩 태그]에서 크게 보여 줄 글 컬럼이에요(선택)" });
    const tags = el("input", { placeholder: "처음 태그 (쉼표로 여러 개)" });
    fill(keyBox, cols);
    fill(textBox, S.cols.filter(c => c[1] === "문자").map(c => c[0]), "글 컬럼 없음");
    const go = el("button", { className: "btn p", textContent: "켜기", onclick: () => busy(go, async () => {
      try {
        info = await (await send("/api/labels/setup", { ...body(), key: keyBox.value, text: textBox.value, vocab: tags.value.split(",").map(t => t.trim()).filter(Boolean) })).json();
        labelsOn = true; labelUse = {}; mode = "";
        box.remove();
        paint();
        runSearch(0);
      } catch (e) { err.textContent = e.message; }
    }) });
    const caption = text => el("div", { className: "hint", textContent: text });
    return [el("h3", { textContent: "태그 켜기" }), el("p", { className: "hint", textContent: "이 테이블의 행에 태그를 달 수 있게 해요. 태그는 이 PC에만 암호화해 저장해요." }),
      caption("고유 키 컬럼"), keyBox, caption("글 컬럼 (선택)"), textBox, caption("처음 태그 (선택)"), tags, go];
  }
  // 태그 사전: 줄마다 이름 바꾸기·차례·지우기, 아래에서 새 태그 더하기. 바꿀 때마다 정보를 합치고 결과 표를 다시 그린다.
  function editor(render, err) {
    const v = info.vocab;
    const edit = async (url, extra) => {
      try {
        merge(await api(url, { ...body(), ...extra }));
        err.textContent = "";
        if (labelsOn && last?.rows) runSearch(skip);
      } catch (e) { err.textContent = e.message; }
      render();
    };
    const move = (i, d) => { const next = [...v]; [next[i], next[i + d]] = [next[i + d], next[i]]; return edit("/api/labels/vocab", { vocab: next }); };
    const line = (t, i) => {
      const name = el("input", { value: t, title: "이름을 바꾸고 칸을 떠나면 달린 행에서도 바뀌어요", onchange: () => edit("/api/labels/rename", { old: t, new: name.value }) });
      return el("div", { className: "row" }, name,
        labelButton("위", "한 칸 위로", () => move(i, -1), i === 0), labelButton("아래", "한 칸 아래로", () => move(i, 1), i === v.length - 1),
        labelButton("지우기", "사전에서 지워요", async () => { if (await confirmBox("태그를 지울까요?", `'${t}' 태그를 지워요. 이 태그가 달린 행에서도 지워요.`, "지우기")) await edit("/api/labels/drop", { name: t }); }));
    };
    const fresh = el("input", { placeholder: "새 태그" });
    return [el("h3", { textContent: "태그 사전" }),
      el("p", { className: "hint", textContent: `키 컬럼: ${info.key}${info.text ? `, 글 컬럼: ${info.text}` : ""}. 태그가 달린 행 ${info.labeled.toLocaleString()}개.` }),
      ...v.map(line), el("div", { className: "row" }, fresh, labelButton("더하기", "사전 끝에 태그를 더해요", () => edit("/api/labels/vocab", { vocab: [...v, fresh.value] })))];
  }
  function open() {
    const err = el("div", { className: "err" }), inner = el("div", { className: "modal-box" });
    const box = el("div", { className: "modal", onclick: e => { if (e.target === box) box.remove(); } }, inner);
    const render = () => inner.replaceChildren(...(on() ? editor(render, err) : setup(box, err)), err,
      el("div", { className: "row", style: "justify-content:flex-end" }, el("button", { className: "ghost", textContent: "닫기", onclick: () => box.remove() })));
    render();
    document.body.append(box);
  }

  $("label-open").onclick = () => {
    if (!on()) return open();
    labelsOn = !labelsOn; labelUse = {}; mode = "";  // 켜면 다시 검색해 `태그` 컬럼을 붙인다
    paint();
    if (labelsOn) runSearch(0); else LabelBar.show($("label-bar"), $("rows")._grid, true);
  };
  $("label-edit").onclick = open;
  $("label-mode").onchange = filter;
  return { on, load, reset, paint, merge };
})();

// 결과 표 아래 태그 막대: 고른 행(g.selected)이 있을 때만. 칩마다 "고른 행 중 몇 개에 달렸는지", 누르면 모두에 달기·떼기.
const LabelBar = (() => {
  let now = null, seenGrid = null, seenSig = "";  // now: 지금 칩들(숫자 키가 누른다), seen*: 마지막으로 그린 표·고른 행·사전(같으면 다시 그리지 않는다)
  const report = fn => async () => { try { await fn(); } catch (e) { say("err-search", e.message); } };
  const typing = t => t.matches?.("textarea, select, [contenteditable], input:not([type=checkbox], [type=radio], [type=button])");

  function show(host, g, force = false) {
    const lab = labelsOn ? Labels.on() : null, idx = lab ? [...(g?.selected ?? [])] : [], sig = JSON.stringify([idx, lab?.vocab]);
    if (!force && g === seenGrid && sig === seenSig) return;
    seenGrid = g; seenSig = sig; now = null;
    host.classList.toggle("hidden", !idx.length);
    if (!idx.length) return host.replaceChildren();
    const picked = idx.map(i => g.rows[i]), tagsOf = r => String(r[SERVER.labels.column] ?? "").split(LABEL_SEP);
    const lock = !g.columns.includes(lab.key) ? `키 컬럼(${lab.key})을 표시 컬럼에 넣어야 태그를 달 수 있어요.`
      : picked.length > SERVER.labels.max_apply ? `고른 행이 ${SERVER.labels.max_apply.toLocaleString()}개를 넘어요. [검색 결과 전체에]를 쓰세요.` : "";
    const finish = async d => { Labels.merge(d); if (last?.rows) await runSearch(skip); };
    const mark = (add, remove) => report(async () => finish(await api("/api/labels/apply", { schema: S.schema, base: S.base, keys: picked.map(r => r[lab.key]), add, remove })));
    const chips = lab.vocab.map((t, i) => {
      const n = picked.filter(r => tagsOf(r).includes(t)).length, all = n === picked.length;
      const b = el("button", { className: all ? "btn p" : "btn", disabled: !!lock, title: all ? "고른 행 모두에서 떼요" : "고른 행 모두에 달아요",
        textContent: `${i < SERVER.labels.quick ? (i + 1) % 10 + " " : ""}${t} ${n}/${picked.length}`, onclick: () => busy(b, mark(all ? [] : [t], all ? [t] : [])) });
      b.setAttribute("aria-pressed", String(all));
      return b;
    });
    now = { chips };
    const name = el("input", { placeholder: "새 태그", title: "달 태그의 이름이에요. [검색 결과 전체에]는 비워 두면 오른쪽 목록의 태그를 써요" }), pick = el("select", { title: "사전의 태그" });
    fill(pick, lab.vocab, "사전에서 고르기");
    const whole = add => report(async () => {  // 지금 검색 조건의 모든 행(서버가 개수를 알리고 확인을 받는다)
      const t = name.value || pick.value;
      await finish(await (await send("/api/labels/apply_where", { ...query(0), add: add && t ? [t] : [], remove: !add && t ? [t] : [] })).json());
    });
    host.replaceChildren(...chips, ...(lock ? [el("span", { className: "hint", textContent: lock })] : []), name,
      labelButton("달기", "고른 행에 이 이름의 태그를 달아요", () => mark(name.value ? [name.value] : [], [])(), !!lock), pick,
      labelButton("검색 결과 전체에 달기", "지금 검색 조건에 맞는 모든 행에 달아요", whole(true)), labelButton("검색 결과 전체에서 떼기", "지금 검색 조건에 맞는 모든 행에서 떼요", whole(false)));
  }
  // 숫자 키(결과 영역 안, 입력 칸 밖): 1~9·0 → 그 번호 칩
  document.addEventListener("keydown", e => {
    const i = (+e.key + 9) % 10;
    if (!now || !/^\d$/.test(e.key) || e.isComposing || e.ctrlKey || e.metaKey || e.altKey || i >= SERVER.labels.quick || !e.target.closest?.(".results") || typing(e.target)) return;
    if (now.chips[i]) { e.preventDefault(); now.chips[i].click(); }
  });
  // 고르기가 바뀌면 막대를 다시 그린다(고르기를 처리한 뒤에)
  ["change", "click"].forEach(type => $("rows").addEventListener(type, () => setTimeout(() => show($("label-bar"), $("rows")._grid), 0)));
  return { show, typing };
})();

// 하나씩 태그 창: 결과 행을 하나씩 읽어(limit 1) 글 컬럼을 크게 보이고, 숫자 키로 태그를 켜고 끄며 Enter로 다음 행으로 간다.
const LabelOne = (() => {
  let box = null, inner = null, at = 0, total = null, only = true, working = false, row = null, cols = [], mine = [];  // at: 읽는 offset, total: 전체 행 수, only: 태그 없는 행만, mine: 지금 행에 달린 태그
  const tagsOf = r => String(r?.[SERVER.labels.column] ?? "").split(LABEL_SEP).filter(Boolean);

  function render(msg = "") {
    const lab = Labels.on(), textCol = cols.includes(lab.text) ? lab.text : S.cols.find(c => c[1] === "문자" && cols.includes(c[0]) && c[0] !== SERVER.labels.column)?.[0];
    const lock = row && !cols.includes(lab.key) ? `키 컬럼(${lab.key})을 표시 컬럼에 넣어야 태그를 달 수 있어요.` : "";
    const chips = lab.vocab.map((t, i) => {
      const on = mine.includes(t);
      const b = el("button", { className: on ? "btn p" : "btn", disabled: !row || !!lock, title: on ? "이 행에서 떼요" : "이 행에 달아요", textContent: `${i < SERVER.labels.quick ? (i + 1) % 10 + " " : ""}${t}`, onclick: () => busy(b, () => toggle(i)) });
      b.setAttribute("aria-pressed", String(on));
      return b;
    });
    const rest = row ? cols.filter(c => c !== textCol && c !== SERVER.labels.column).map(c => el("div", { className: "hint", style: "margin:0", textContent: `${c}: ${row[c] ?? ""}` })) : [];
    inner.replaceChildren(el("h3", { textContent: "하나씩 태그" }),
      el("div", { className: "row", style: "align-items:center; justify-content:space-between" }, el("span", { className: "hint", style: "margin:0", textContent: `${(at + 1).toLocaleString()} / ${total?.toLocaleString() ?? "?"}` }),
        el("label", { className: "check" }, el("input", { type: "checkbox", checked: only, onchange: e => { only = e.target.checked; total = null; load(0); } }), "태그 없는 행만")),
      el("div", { style: "font-size:18px; white-space:pre-wrap; max-height:40vh; overflow:auto; margin:10px 0", textContent: row ? (textCol ? String(row[textCol] ?? "") : "") : "더 볼 행이 없어요." }),
      ...rest, el("div", { className: "row", style: "margin-top:10px; flex-wrap:wrap" }, ...chips),
      el("div", { className: "hint", textContent: lock || "숫자: 태그 켜기·끄기, Enter: 다음 행, ←: 앞 행, Esc: 닫기" }), el("div", { className: "err", textContent: msg }),
      el("div", { className: "row", style: "justify-content:flex-end" }, el("button", { className: "ghost", textContent: "닫기", onclick: close })));
  }
  async function load(i) {
    if (working) return;
    working = true;
    try {
      const d = await (await send("/api/query", { ...query(i), labels: only ? { none: true } : labelUse, limit: 1, offset: i, count: i === 0, known_count: total ?? undefined })).json();
      at = i; row = d.rows.rows[0] ?? null; cols = d.rows.columns; mine = tagsOf(row);
      if (d.count != null) total = d.count;
      render();
    } catch (e) { render(e.message); }
    finally { working = false; }
  }
  async function toggle(i) {
    const lab = Labels.on(), t = lab.vocab[i];
    if (working || !row || t === undefined || !cols.includes(lab.key)) return;
    working = true;
    try {
      const has = mine.includes(t), d = await api("/api/labels/apply", { schema: S.schema, base: S.base, keys: [row[lab.key]], add: has ? [] : [t], remove: has ? [t] : [] });
      Labels.merge(d);
      mine = d.vocab.filter(v => (has ? v !== t && mine.includes(v) : v === t || mine.includes(v)));
      render();
    } catch (e) { render(e.message); }
    finally { working = false; }
  }
  // 태그 없는 행만 읽는 중 지금 행에 태그를 달았으면 그 행은 목록에서 빠졌으므로 같은 offset을 다시 읽는다.
  function next() {
    if (!row) return;
    const stays = only && mine.length > 0;
    if (stays && total !== null) total -= 1;
    return load(stays ? at : at + 1);
  }
  function close() { box.remove(); box = null; if (last?.rows) runSearch(skip); }
  function open() {
    if (!labelsOn || !Labels.on()) return;
    inner = el("div", { className: "modal-box" });
    box = el("div", { id: "label-one", className: "modal", onclick: e => { if (e.target === box) close(); } }, inner);
    document.body.append(box);
    only = true; total = null; row = null; mine = [];
    return load(0);
  }
  document.addEventListener("keydown", e => {
    if (!box?.isConnected || e.isComposing || e.ctrlKey || e.metaKey || e.altKey || LabelBar.typing(e.target)) return;
    const i = (+e.key + 9) % 10;
    if (/^\d$/.test(e.key)) { if (i < SERVER.labels.quick) { e.preventDefault(); toggle(i); } }
    else if (e.key === "Enter") { e.preventDefault(); next(); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); if (at > 0) load(at - 1); }
    else if (e.key === "Escape") close();
  });
  $("label-one-open").onclick = () => busy($("label-one-open"), open);
  return { open };
})();
