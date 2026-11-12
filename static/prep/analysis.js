// ---------- 분석 ----------
// A.cols: 분석할 표의 [컬럼, 종류(num·date·text)]. A.result: 마지막 /api/analyze 결과(how·조건 글 포함). A.view: 보고 있는 그래프.
const A = { cols: [], hashed: null, result: null, view: null, lo: 0, hi: 0 };
const VIEW_NAMES = { hist: "히스토그램", box: "상자 그림", values: "값 빈도", heat: "히트맵", scatter: "산점도", groups: "그룹 비교", trend: "추이", funnel: "퍼널",
  cohort: "코호트 표", curve: "평균 유지율", rfm: "RFM 세그먼트", pareto: "파레토", chi: "관계 검정", ab: "A/B 검정", basket: "연관 쌍", waterfall: "변화 요인", season: "시계열 분해" };
const VIEW_RATIOS = { box: 2.4, heat: 1, scatter: 4 / 3, cohort: 1, chi: 1 };  // 나머지는 16:9
const ADD_TITLE = $("a-add").title;
const anaDraw = island($("a-chart"), m => say("err-ana", m));
$("a-max-corr").textContent = SERVER.analysis.max_corr;
$("a-max-points").textContent = SERVER.analysis.max_points.toLocaleString();
$("a-max-steps").textContent = SERVER.analysis.max_steps;
// 새 분석 입력 칸의 기본값·상한은 서버 값(SERVER.analysis, SERVER.defaults)에서 받는다. 비우면 서버 기본값이 쓰인다.
const PCT = 100;  // 비율 ↔ % 칸
{
  const lim = SERVER.analysis, set = (id, attrs) => Object.assign($(id), attrs);
  set("a-rspan", { placeholder: lim.cohort_span, min: 1, max: lim.max_cohort_span });
  set("a-pa", { placeholder: lim.pareto_a * PCT, min: 0, max: PCT });
  set("a-pb", { placeholder: lim.pareto_b * PCT, min: 0, max: PCT });
  set("a-bconf", { placeholder: SERVER.defaults.sample_conf });
  set("a-ksup", { placeholder: lim.min_support * PCT, min: 0, max: PCT });
  set("a-ktop", { placeholder: lim.basket_top, min: 1, max: lim.max_basket_top });
  set("a-swin", { min: 3, max: lim.max_window });
  set("a-sk", { placeholder: lim.mad_k, min: 0, max: lim.max_mad_k });
  set("a-sq", { placeholder: lim.scale_q, min: lim.min_scale_q, max: lim.max_scale_q });
}
const showSeasonWindow = () => { $("a-swin").placeholder = SERVER.analysis.season_windows[$("a-sunit").value]; };
$("a-sunit").onchange = showSeasonWindow;
showSeasonWindow();
$("a-pagg").onchange = () => $("a-pvalbox").classList.toggle("hidden", $("a-pagg").value === "count");
// A/B의 대조군: 그룹 컬럼의 값(많이 나온 순)을 받아 고르게 한다
let ctrlCall = 0;
async function loadCtrlValues() {
  const column = $("a-bgroup").value, mine = ++ctrlCall, was = $("a-bctrl").value, blank = "대조군을 고르세요";
  fill($("a-bctrl"), [], blank);
  if (!column || !prof) return;
  try {
    const d = await api("/api/analyze", { recipe: anaRecipe(), how: { kind: "distribution", column }, sample, sample_how: sampleHow() });
    if (mine !== ctrlCall) return;
    fill($("a-bctrl"), d.values.labels, blank);
    if (d.values.labels.includes(was)) $("a-bctrl").value = was;
  } catch (e) { if (mine === ctrlCall) say("err-ana", e.message); }
}
$("a-bgroup").onchange = loadCtrlValues;
// 흐름(퍼널): 단계 칩(끌어서 순서 바꾸기, ×로 빼기, 최대 max_steps개)과 단계 컬럼의 값 목록
const FLOW = { steps: [], values: [] };
function showSteps() {
  $("a-fsteps").replaceChildren(...FLOW.steps.map((name, i) => {
    const chip = el("span", { className: "f-step", draggable: true, title: "끌어서 순서를 바꿀 수 있어요" }, `${i + 1}. ${name}`,
      el("button", { className: "ghost", textContent: "×", title: "이 단계 빼기", onclick: () => { FLOW.steps.splice(i, 1); showSteps(); } }));
    chip.ondragstart = e => { e.dataTransfer.setData("text/plain", String(i)); e.dataTransfer.effectAllowed = "move"; };
    chip.ondragover = e => { e.preventDefault(); chip.classList.add("over"); };
    chip.ondragleave = () => chip.classList.remove("over");
    chip.ondrop = e => {
      e.preventDefault();
      const from = +e.dataTransfer.getData("text/plain");
      if (Number.isInteger(from) && from !== i) FLOW.steps.splice(i, 0, FLOW.steps.splice(from, 1)[0]);
      showSteps();
    };
    return chip;
  }));
}
function addStep() {
  const value = $("a-fvalue").value.trim();
  if (!value) return;
  if (FLOW.steps.includes(value)) return say("err-ana", `같은 단계(${value})를 두 번 넣을 수 없어요.`);
  if (FLOW.steps.length >= SERVER.analysis.max_steps) return say("err-ana", `단계는 ${SERVER.analysis.max_steps}개까지 고를 수 있어요.`);
  FLOW.steps.push(value);
  $("a-fvalue").value = ""; closeMenu();
  say("err-ana", "");
  showSteps();
}
$("a-fadd").onclick = addStep;
$("a-fvalue").onkeydown = e => { if (e.key === "Enter" && !e.ctrlKey && !e.metaKey && !e.isComposing && !(menuFor === e.target && menuAt)) addStep(); };  // 한글 조합을 확정하는 Enter와, 목록이 가리킨 항목을 고르는 Enter(common.js)는 단계로 넣지 않는다
// 단계 컬럼을 고르면 그 컬럼의 값(많이 나온 순, 20개까지)을 목록으로 받는다. 단계 컬럼이 바뀌면 고른 단계는 비운다.
let anaCall = 0, stepCall = 0, anaRun = 0;  // 늦게 온 옛 응답이 새 선택을 덮지 않게(anaRun: [구하기], 연결을 바꾸면 올린다)
async function loadStepValues() {
  const column = $("a-fstep").value, mine = ++stepCall;
  FLOW.values = [];
  $("a-fvalues").replaceChildren();
  if (!column || !prof) return;
  try {
    const d = await api("/api/analyze", { recipe: anaRecipe(), how: { kind: "distribution", column }, sample, sample_how: sampleHow() });
    if (mine !== stepCall) return;
    FLOW.values = d.values.labels;
    $("a-fvalues").replaceChildren(...FLOW.values.map(v => el("option", { value: v })));
  } catch (e) { if (mine === stepCall) say("err-ana", e.message); }
}
$("a-fstep").onchange = () => { FLOW.steps = []; showSteps(); loadStepValues(); };
// 조건 단계 퍼널(서버 kind "stages"): 단계 줄 {name, conds(여러 줄 글), revisit(글)}. 검사는 서버(funnel.check_stages)가 한다
FLOW.stages = [];
function showStages() {
  $("a-fstage-rows").replaceChildren(...FLOW.stages.map((s, i) => el("div", { className: "f-stage", role: "group", ariaLabel: `단계 ${i + 1}` },
    el("input", { value: s.name, placeholder: "단계 이름", ariaLabel: "단계 이름", oninput: e => { s.name = e.target.value; } }),
    el("textarea", { rows: 2, value: s.conds, placeholder: "한 줄에 하나, 줄끼리 and. 예: event in purchase, refund", ariaLabel: "조건 줄", oninput: e => { s.conds = e.target.value; } }),
    el("div", { className: "row" },
      el("input", { type: "number", min: 1, max: SERVER.analysis.max_revisit, step: 1, value: s.revisit, placeholder: "재방문 N일(비우면 아님)", ariaLabel: "재방문 일수", oninput: e => { s.revisit = e.target.value; } }),
      el("button", { className: "ghost", textContent: "위", ariaLabel: "위로", disabled: i === 0, onclick: () => { FLOW.stages.splice(i - 1, 0, FLOW.stages.splice(i, 1)[0]); showStages(); } }),
      el("button", { className: "ghost", textContent: "×", ariaLabel: "이 단계 빼기", onclick: () => { FLOW.stages.splice(i, 1); showStages(); } })))));
  $("a-fstage-add").disabled = FLOW.stages.length >= SERVER.analysis.max_steps;
}
$("a-fstage-add").onclick = () => { FLOW.stages.push({ name: "", conds: "", revisit: "" }); showStages(); };
$("a-fdefine").onchange = () => {
  const stages = $("a-fdefine").value === "stages";
  $("a-fvalues-box").classList.toggle("hidden", stages); $("a-fstages-box").classList.toggle("hidden", !stages);
  if (stages && !FLOW.stages.length) $("a-fstage-add").onclick();
};
$("a-ftrim").max = SERVER.analysis.max_trim * PCT; $("a-ftrim").placeholder = String(SERVER.analysis.trim * PCT);
const akind = () => document.querySelector("input[name=akind]:checked").value;
// 분석할 표: 체크하면 지금 전처리(recipe), 아니면 원래 테이블
const anaRecipe = () => $("a-prep").checked ? recipe() : { schema: S.schema, base: S.base };
function showAnaForm() {
  document.querySelectorAll(".a-form").forEach(f => f.classList.toggle("hidden", f.dataset.kind !== akind()));
  $("a-heat").classList.toggle("hidden", $("a-cmode").value !== "heatmap");
  $("a-scat").classList.toggle("hidden", $("a-cmode").value === "heatmap");
}
document.querySelectorAll("input[name=akind]").forEach(b => b.onchange = showAnaForm);
$("a-cmode").onchange = showAnaForm;
$("a-prep").onchange = loadAnaColumns;
// 분석할 표의 컬럼(전처리 결과의 이름·타입)을 표본으로 받아 고르는 칸들을 채운다.
async function loadAnaColumns() {
  const mine = ++anaCall;
  let cols = [], err = "전처리 탭에서 먼저 테이블을 골라 주세요.";
  if (prof) {
    try { cols = (await api("/api/analyze", { recipe: anaRecipe(), how: { kind: "columns" }, sample, sample_how: sampleHow() })).columns; err = ""; }
    catch (e) { err = e.message; }
  }
  let h = null;  // 해시·UUID 컬럼(common.js). 못 받으면 빼지 않고 그 오류를 보인다
  if (prof && !err) { try { h = await hashedOf(S.schema, S.base); } catch (e) { err = e.message; } }
  if (mine !== anaCall) return;
  A.cols = cols;
  A.hashed = h;
  say("err-ana", err);
  if (h?.on) for (const id of ["a-gby", "a-gval", "a-tval", "a-fby"]) if (h.columns.includes($(id).value) && !h.restored.includes($(id).value)) h.restored.push($(id).value);  // 이미 고른 컬럼은 남긴다
  const names = k => A.cols.filter(c => !k || c[1] === k).map(c => c[0]), nums = names("num");
  const grouped = hashedOut(names(), h);
  hashedNote($("hashed-note-a"), h, grouped.hidden, loadAnaColumns);
  const keep = (id, list, blank) => { const now = $(id).value; fill($(id), list, blank); if (list.includes(now)) $(id).value = now; };
  keep("a-col", names());
  keep("a-sx", nums);
  keep("a-sy", nums);
  if ($("a-sy").value === $("a-sx").value && nums[1]) $("a-sy").value = nums[1];
  keep("a-gby", grouped.names);
  fillAnaValues();
  keep("a-tcol", names("date"));
  keep("a-fuser", names());
  keep("a-fby", grouped.names, "나누지 않아요");
  const before = $("a-fstep").value;
  keep("a-fstep", names());
  keep("a-ftime", names("date"));
  if ($("a-fstep").value !== before || !FLOW.values.length) loadStepValues();
  const dates = names("date"), ctrlBefore = $("a-bgroup").value;
  keep("a-rwho", names()); keep("a-rtime", dates);
  keep("a-mwho", names()); keep("a-mtime", dates); keep("a-mamt", nums); keep("a-mtx", names(), "고르지 않음 — 빈도 = 행 수");
  keep("a-pitem", names()); keep("a-pval", nums);
  keep("a-xrow", grouped.names); keep("a-xcol", grouped.names);
  keep("a-bgroup", grouped.names); keep("a-bval", names()); keep("a-bwho", names(), "고르지 않음 — 한 행 = 한 사람");
  keep("a-kkey", names()); keep("a-kitem", names());
  keep("a-dtime", dates); keep("a-dgroup", grouped.names); keep("a-dval", nums);
  keep("a-stime", dates);
  if ($("a-bgroup").value !== ctrlBefore || $("a-bctrl").options.length < 2) loadCtrlValues();
  const picked = new Set([...$("a-corr-cols").querySelectorAll("input:checked")].map(b => b.value));
  $("a-corr-cols").replaceChildren(...nums.map((n, i) => el("label", {}, el("input", { type: "checkbox", value: n, checked: picked.size ? picked.has(n) : i < 8 }), " " + n)));
  $("a-tcol").title = names("date").length ? "" : "날짜 컬럼이 없어요. 전처리에서 타입을 날짜로 바꾸면 고를 수 있어요";
}
// 오른쪽 패널에서 고른 분석(서버의 how)
function anaHow() {
  const k = akind();
  if (k === "distribution") return { kind: k, column: $("a-col").value };
  if (k === "correlation") return $("a-cmode").value === "heatmap"
    ? { kind: "correlation", columns: [...$("a-corr-cols").querySelectorAll("input:checked")].map(b => b.value) }
    : { kind: "scatter", x: $("a-sx").value, y: $("a-sy").value };
  const pick = (key, value) => ({ agg: pickers[key].get().agg, ...(pickers[key].get().opts ? { opts: pickers[key].get().opts } : {}), value });
  if (k === "groups") return { kind: k, by: $("a-gby").value, ...pick("groups", $("a-gval").value) };
  const v = id => $(id).value, orNull = id => v(id) === "" ? null : +v(id), pct = id => v(id) === "" ? null : +v(id) / PCT, text = id => v(id) || null;
  if (k === "funnel") {
    const common = { who: v("a-fuser"), time: v("a-ftime"), window: v("a-fwin") === "" ? null : { n: +v("a-fwin"), unit: v("a-funit") }, trim: pct("a-ftrim") };
    return v("a-fdefine") === "stages"
      ? { kind: "stages", ...common, count: v("a-fcount"), stages: FLOW.stages.map(s => ({ name: s.name.trim(), conds: s.conds.split(/\r?\n/).map(l => l.trim()).filter(Boolean), ...(s.revisit.trim() ? { revisit: +s.revisit } : {}) })) }
      : { kind: k, ...common, step: v("a-fstep"), steps: [...FLOW.steps], ...(v("a-fby") ? { by: v("a-fby") } : {}) };
  }
  if (k === "retention") return { kind: k, who: v("a-rwho"), time: v("a-rtime"), unit: v("a-runit"), span: orNull("a-rspan") };
  if (k === "rfm") return { kind: k, who: v("a-mwho"), time: v("a-mtime"), amount: v("a-mamt"), asof: text("a-masof"), tx: text("a-mtx") };
  if (k === "pareto") return { kind: k, item: v("a-pitem"), agg: v("a-pagg"), value: v("a-pagg") === "sum" ? v("a-pval") : null, a: pct("a-pa"), b: pct("a-pb") };
  if (k === "chisq") return { kind: k, row: v("a-xrow"), col: v("a-xcol") };
  if (k === "abtest") return { kind: k, group: v("a-bgroup"), metric: v("a-bmetric"), value: v("a-bval"), control: text("a-bctrl"), conf: orNull("a-bconf"), who: text("a-bwho") };
  if (k === "basket") return { kind: k, key: v("a-kkey"), item: v("a-kitem"), support: pct("a-ksup"), top: orNull("a-ktop") };
  if (k === "decompose") return { kind: k, time: v("a-dtime"), base: { start: v("a-db0"), end: v("a-db1") }, compare: { start: v("a-dc0"), end: v("a-dc1") }, group: v("a-dgroup"), value: v("a-dval") };
  if (k === "seasonal") return { kind: k, time: v("a-stime"), unit: v("a-sunit"), ...pick("seasonal", v("a-sval")), window: orNull("a-swin"), k: orNull("a-sk"), q: orNull("a-sq") };
  return { kind: k, column: $("a-tcol").value, unit: $("a-tunit").value, ...pick("trend", $("a-tval").value) };
}
// 구하기(표본) 또는 전체로 다시 구하기(마지막 분석을 테이블 전체로)
async function runAna(full) {
  if (!prof) return say("err-ana", "전처리 탭에서 먼저 테이블을 골라 주세요.");
  if (full && !A.result) return say("err-ana", "먼저 [구하기]로 표본을 분석해 주세요.");
  const call = ++anaRun;
  try {
    const how = full ? A.result.how : anaHow(), prepped = full ? A.result.prepped : $("a-prep").checked;
    const base = full ? A.result.base : S.base, r = full ? A.result.recipe : prepped ? recipe() : { schema: S.schema, base };  // 기다리는 동안 테이블이 바뀔 수 있어 시작 때 값을 잡아 둔다. 전체로는 마지막 분석의 테이블·전처리 그대로
    if (how.kind === "abtest" && !how.control) return say("err-ana", "대조군을 골라 주세요.");  // 서버의 첫 그룹 기본값은 화면에서 쓰지 않는다
    const used = full ? A.result.sample : sample;
    const auto = $("a-all").checked ? { ...anaAuto, tail: false, density: false } : anaAuto;
    const asked = ANA_AUTO_KINDS.includes(how.kind) && Object.keys(auto).length ? { ...how, auto } : how;  // 저장하는 how에는 넣지 않는다
    const d = await timedHere("analyze", () => api("/api/analyze", { recipe: r, how: asked, sample, full, sample_how: sampleHow() }));
    if (call !== anaRun) return;  // 기다리는 사이 연결을 바꿨다
    const views = d.kind === "distribution" ? (d.histogram ? ["hist", "box", "values"] : ["values"])
      : { correlation: ["heat"], scatter: ["scatter"], groups: ["groups"], trend: ["trend"], funnel: ["funnel"], stages: ["funnel"], retention: ["cohort", "curve"], rfm: ["rfm"], pareto: ["pareto"], chisq: ["chi"], abtest: ["ab"], basket: ["basket"], decompose: ["waterfall"], seasonal: ["season"] }[d.kind];
    A.result = { ...d, how, prepped, base, recipe: r, views, sample: used };  // 구할 때의 표본 수(카드가 최신화 때 같은 표본으로 다시 구한다)
    A.view = views.includes(A.view) ? A.view : views[0];
    say("err-ana", "");
    drawAna();
  } catch (e) { if (call === anaRun) say("err-ana", e.message); }  // 연결을 바꾼 뒤 옛 분석의 늦은 오류는 보이지 않는다
}
$("a-run").onclick = () => busy($("a-run"), () => runAna(false));
$("a-full").onclick = () => busy($("a-full"), () => runAna(true));
// 분석 결과의 제목과 아래 줄(어느 데이터로 구했는지, 줄인 것)
// 새 분석 8가지: 제목·안내 글·표. 숫자 기본값·상한은 SERVER.analysis에서 받고 화면에는 적지 않는다.
const NEW_KINDS = ["retention", "rfm", "pareto", "chisq", "abtest", "basket", "decompose", "seasonal"];
const SIG = 4;  // 표·안내에 보일 유효 숫자
const sig = v => v == null ? "-" : +Number(v).toPrecision(SIG);
const pctText = v => v == null ? "-" : `${(v * PCT).toFixed(1)}%`;
const pText = p => p == null ? "-" : Number(p).toPrecision(SIG);
const AB_TESTS = { z: "두 비율 z 검정", welch: "Welch t 검정", anova: "분산분석(ANOVA)", chi2: "카이제곱 동질성 검정" };
const NEW_TITLES = {
  cohort: d => `${d.who} ${UNIT_NAMES[d.unit]}별 코호트 리텐션`, curve: d => `${d.who} 평균 유지율`, rfm: d => `${d.who} RFM 세그먼트`,
  pareto: d => `${d.item} 파레토(${d.agg === "count" ? "행 수" : `합계 ${d.value}`})`, chi: d => `${d.row} × ${d.col} 관계 검정`,
  ab: d => `${d.group}별 ${d.value} A/B 검정`, basket: d => `${d.item} 연관 쌍`, waterfall: d => `${d.group}별 ${d.value} 변화 요인`, season: d => `${d.time} ${UNIT_NAMES[d.unit]}별 시계열 분해` };
const NEW_NOTES = {
  rfm: d => [`기준일 ${d.asof}`, `사용자 ${d.users.toLocaleString()}명`],
  pareto: d => [d.dropped ? `0 이하·빈 값 항목 ${d.dropped.toLocaleString()}개 뺌` : "", d.more ? `표와 그림에는 앞 ${SERVER.analysis.pareto_show}개만 보임` : ""],
  chisq: d => [`χ² = ${sig(d.chi2)} · 자유도 ${d.df} · p = ${pText(d.p)} · Cramér's V = ${sig(d.v)}`, d.warn ? `기대 빈도가 작은 칸이 ${pctText(d.low_share)}라 결과를 믿기 어려워요` : ""],
  abtest: d => [`${AB_TESTS[d.test.kind]} p = ${pText(d.test.p)}`, d.conf != null ? `신뢰 수준 ${d.conf}%` : "", d.small ? "표본이 적어 근사 검정을 믿기 어려워요" : "", d.groups.length > 2 ? "쌍 p값은 Bonferroni 보정" : ""],
  basket: d => [d.note ?? ""], decompose: d => [d.truncated ? "그룹이 많아 나머지는 기타로 묶음" : ""],
  seasonal: d => [d.note ?? "", d.more ? `최근 ${SERVER.analysis.max_periods}개 기간만 보임` : ""] };
const RFM_COLS = ["세그먼트", "사람 수", "비율", "평균 최근(일)", "평균 빈도", "평균 금액", "매출 비중"];
const NEW_TABLES = {
  cohort: d => ({ columns: ["코호트", "크기", ...d.periods.map(p => `+${p}`)], rows: d.cohorts.map(c => ({ 코호트: c.start, 크기: c.size, ...Object.fromEntries(d.periods.map((p, i) => [`+${p}`, pctText(c.retained[i])])) })) }),
  curve: d => ({ columns: ["N", "평균 유지율"], rows: d.average.map((v, i) => ({ N: `+${i}`, "평균 유지율": pctText(v) })) }),
  rfm: d => {
    const blank = Object.fromEntries(RFM_COLS.map(c => [c, ""]));
    return { columns: RFM_COLS, rows: [...d.segments.map(s => ({ 세그먼트: s.segment, "사람 수": s.users, 비율: pctText(s.share), "평균 최근(일)": sig(s.recency), "평균 빈도": sig(s.frequency), "평균 금액": sig(s.monetary), "매출 비중": pctText(s.revenue_share) })),
      ...["r", "f", "m"].map(k => ({ ...blank, 세그먼트: `${k.toUpperCase()} 분위 경계`, "사람 수": d.cuts[k].map(sig).join(", ") }))] };
  },
  pareto: d => ({ columns: ["순위", "항목", "값", "비중", "누적 비중", "등급"], rows: d.rows.map((r, i) => ({ 순위: i + 1, 항목: r.item, 값: sig(r.v), 비중: pctText(r.share), "누적 비중": pctText(r.cum_share), 등급: r.grade })) }),
  chi: d => {
    const head = `${d.row} \\ ${d.col}`;
    return { columns: [head, ...d.cols], rows: d.rows.map((r, i) => ({ [head]: r, ...Object.fromEntries(d.cols.map((c, j) => [c, `${d.observed[i][j]} (${d.expected[i][j].toFixed(1)})`])) })) };
  },
  ab: d => { const m = d.metric === "rate" ? "비율" : "평균"; return { columns: ["그룹", "n", m, "표준편차"], rows: d.groups.map(g => ({ 그룹: g.name, n: g.n, [m]: sig(g.mean), 표준편차: sig(g.sd) })) }; },
  basket: d => ({ columns: ["A", "B", "함께 산 거래", "지지도", "신뢰도 A→B", "신뢰도 B→A", "향상도"], rows: d.pairs.map(p => ({ A: p.a, B: p.b, "함께 산 거래": p.n, 지지도: pctText(p.support), "신뢰도 A→B": pctText(p.conf_ab), "신뢰도 B→A": pctText(p.conf_ba), 향상도: sig(p.lift) })) }),
  waterfall: d => {
    const cols = ["그룹", "n0", "n1", "평균0", "평균1", "구성", "그룹 안", "상호작용", "합계"], sum = k => d.groups.reduce((a, g) => a + g[k], 0);
    return { columns: cols, rows: [...d.groups.map(g => ({ 그룹: g.group, n0: g.n0, n1: g.n1, 평균0: sig(g.m0), 평균1: sig(g.m1), 구성: sig(g.mix), "그룹 안": sig(g.rate), 상호작용: sig(g.interaction), 합계: sig(g.total) })),
      { 그룹: "전체", n0: sum("n0"), n1: sum("n1"), 평균0: sig(d.before), 평균1: sig(d.after), 구성: sig(d.mix), "그룹 안": sig(d.rate), 상호작용: sig(d.interaction), 합계: sig(d.change) }] };
  },
  season: d => ({ columns: ["기간", "값", "추세", "계절", "나머지", "이상"], rows: d.labels.map((l, i) => ({ 기간: l, 값: sig(d.values[i]), 추세: sig(d.trend[i]), 계절: sig(d.seasonal[i]), 나머지: sig(d.remainder[i]), 이상: d.flags.includes(l) ? "●" : "" })) }) };
// A/B 쌍 표(그룹 표 아래)와 파레토 등급 요약 줄
const abPairs = d => ({ columns: ["그룹", "차이", "신뢰구간", "Lift", "Lift 구간", "p", "보정 p"], rows: d.pairs.map(p => ({ 그룹: p.name, 차이: sig(p.diff), 신뢰구간: p.ci.map(sig).join(" ~ "), Lift: pctText(p.lift), "Lift 구간": p.lift_ci ? p.lift_ci.map(pctText).join(" ~ ") : "-", p: pText(p.p), "보정 p": pText(p.p_adj) })) });
function pairBlock(d) {
  const host = el("div", { className: "table-wrap" });
  sortedTable(host, abPairs(d), (h, x, s) => resultTable(h, x, s));
  return [el("div", { className: "hint", textContent: `${d.control} 대비` }), host];
}
const gradeLine = d => d.classes.map(c => `${c.grade} ${c.items.toLocaleString()}개 ${pctText(c.share)}`).join(" · ");
// (Phase 20) 서버가 준 주의 글(d.notes, A/B 검정·RFM)을 줄마다. 없으면 아무것도 그리지 않는다.
function noteLines(d) {
  return (d.notes ?? []).map(t => el("div", { className: "hint warn", textContent: t }));
}
function anaTitle(d, view) {
  if (NEW_TITLES[view]) return NEW_TITLES[view](d);
  const value = x => x.agg === "count" ? "개수" : `${x.value} ${aggName(x.agg)}`;
  return { hist: `${d.column} 분포`, box: `${d.column} 상자 그림`, values: `${d.column} 값 빈도`, heat: "상관관계",
           scatter: `${d.x} × ${d.y}`, groups: `${d.by}별 ${value(d)}`, trend: `${d.column} ${UNIT_NAMES[d.unit]}별 ${value(d)}`, funnel: `${d.step ?? "조건 단계"} 퍼널(${d.steps?.length}단계)` }[view];
}
function anaNote(d) {
  const data = d.data.full ? "테이블 전체로 구함" : `표본 ${d.data.rows.toLocaleString()}행으로 구함, 읽은 방법은 ${SERVER.sample_methods[d.data.method] ?? d.data.method}`;
  const lim = SERVER.analysis, more = [
    d.kind === "groups" && d.more ? `행이 많은 ${lim.max_groups}개 그룹만 보임` : "",
    d.kind === "trend" && d.more ? `최근 ${lim.max_periods}개 기간만 보임` : "",
    d.kind === "scatter" ? `점 ${d.points.length.toLocaleString()}개, 상관계수 ${d.r == null ? "-" : d.r.toFixed(3)}(${d.n.toLocaleString()}행)` : "",
    d.kind === "correlation" ? `${d.n.toLocaleString()}행으로 계산` : "",
    d.kind === "funnel" ? `${windowText(d.window)}, 같은 시각의 두 단계는 도달로 봄` : d.kind === "stages" ? `${windowText(d.window)}, ${d.count === "each" ? "단계마다 따로" : "순서대로"} 셈` : "",
    ...(NEW_NOTES[d.kind]?.(d) ?? []), d.excluded ? `빈 값으로 뺀 행 ${d.excluded.toLocaleString()}개` : ""];
  return [`${d.base}의 ${d.prepped ? "전처리한 결과" : "원래 데이터"}`, data, ...more].filter(Boolean).join(", ") + (d.data?.notes?.length ? " " + d.data.notes.join(" ") : "");
}
// 화면에서 자르는 범위(받은 결과를 줄여 보일 뿐, 서버에는 다시 묻지 않는다). 칸 수 = 시계열·그룹은 기간·그룹, 히스토그램은 구간, 값 빈도는 값 종류
function anaSpan(d, view) {
  return { trend: () => d.labels.length, groups: () => d.labels.length, season: () => d.labels.length, pareto: () => d.rows.length, hist: () => d.histogram.counts.length, values: () => d.values.labels.length }[view]?.() ?? 0;
}
// lo(0부터)~hi(미포함)만 남긴 그림용 사본
function anaSlice(d, view, lo, hi) {
  const cut = a => a.slice(lo, hi);
  if (view === "trend" || view === "groups") return { ...d, labels: cut(d.labels), values: cut(d.values), ...(d.counts ? { counts: cut(d.counts) } : {}) };
  if (view === "pareto") return { ...d, rows: cut(d.rows) };
  if (view === "season") return { ...d, labels: cut(d.labels), values: cut(d.values), trend: cut(d.trend), seasonal: cut(d.seasonal), remainder: cut(d.remainder) };
  if (view === "hist") return { ...d, histogram: { edges: d.histogram.edges.slice(lo, hi + 1), counts: cut(d.histogram.counts) } };
  if (view === "values") return { ...d, values: { ...d.values, total: d.values.total ?? d.values.counts.reduce((a, b) => a + b, 0) + d.values.nulls + d.values.other, labels: cut(d.values.labels), counts: cut(d.values.counts), nulls: hi < d.values.labels.length ? 0 : d.values.nulls, other: hi < d.values.labels.length ? 0 : d.values.other } };
  return d;
}
// 그래프 아래의 표
function anaTable(d, view) {
  if (NEW_TABLES[view]) return NEW_TABLES[view](d);
  const pct = n => d.values ? `${(n * 100 / Math.max(1, d.values.total ?? d.values.counts.reduce((a, b) => a + b, 0) + d.values.nulls + d.values.other)).toFixed(1)}%` : "";  // total: 범위로 자른 표도 비율은 전체 기준
  if (view === "hist" || view === "box") {
    const s = d.stats, b = d.box;
    return { columns: ["통계", "값"], rows: [["행 수(빈 값 빼고)", s.n], ["최소", s.min], ["Q1", s.q1], ["중앙값", s.median], ["Q3", s.q3], ["최대", s.max], ["평균", s.mean], ["표준편차", s.std],
      ["이상값(1.5×IQR 밖)", b.outliers], ["빈 값", d.values.nulls]].map(([k, v]) => ({ 통계: k, 값: v })) };
  }
  if (view === "values") return { columns: ["값", "행 수", "비율"], rows: [
    ...d.values.labels.map((l, i) => ({ 값: l, "행 수": d.values.counts[i], 비율: pct(d.values.counts[i]) })),
    ...(d.values.nulls ? [{ 값: "(빈 값)", "행 수": d.values.nulls, 비율: pct(d.values.nulls) }] : []),
    ...(d.values.other ? [{ 값: `그 밖의 값 ${(d.values.unique - d.values.labels.length).toLocaleString()}종류`, "행 수": d.values.other, 비율: pct(d.values.other) }] : [])] };
  if (view === "heat") return { columns: ["", ...d.columns], rows: d.matrix.map((row, i) => ({ "": d.columns[i], ...Object.fromEntries(row.map((r, j) => [d.columns[j], r == null ? "-" : +r.toFixed(3) || 0])) })) };
  if (view === "scatter") return { columns: ["", "값"], rows: [["상관계수 r", d.r == null ? "-" : +d.r.toFixed(4) || 0], ["쓴 행 수", d.n], ["그린 점 수", d.points.length]].map(([k, v]) => ({ "": k, 값: v })) };
  if (view === "funnel") return { columns: FUNNEL_COLUMNS, rows: funnelRows(d.steps) };
  const name = view === "groups" ? d.by : UNIT_NAMES[d.unit], value = d.agg === "count" ? "개수" : `${d.value} ${aggName(d.agg)}`;
  return { columns: [name, value, ...(d.counts ? ["행 수"] : [])], rows: d.labels.map((l, i) => ({ [name]: l, [value]: d.values[i], ...(d.counts ? { "행 수": d.counts[i] } : {}) })) };
}
// 정렬한 표를 자른다: n이 0이면 그대로, 아니면 앞 n행(값 줄)에서 lo~hi만 남기고, 끝까지 볼 때(hi >= n)만 고정 행(빈 값·그 밖의 값)을 붙인다.
function anaRows(rows, n, lo, hi) {
  return n ? [...rows.slice(0, n).slice(lo, hi), ...(hi >= n ? rows.slice(n) : [])] : rows;
}
// 퍼널을 컬럼 값별로 나눈 단계 표들(전체 표 아래). 나눠 보지 않았으면 없다.
function segTables(segments) {
  return (segments ?? []).flatMap(g => {
    const host = el("div", { className: "table-wrap" });
    sortedTable(host, { columns: FUNNEL_COLUMNS, rows: funnelRows(g.steps) }, (h, d, s) => resultTable(h, d, s));
    return [el("div", { className: "hint", textContent: g.name }), host, ...gapTable(g.gaps)];
  });
}
// 단계 사이에 걸린 시간 표(응답의 gaps). 없으면 표를 만들지 않는다.
function gapTable(gaps) {
  if (!gaps) return [];
  const host = el("div", { className: "table-wrap" });
  resultTable(host, { columns: GAP_COLUMNS, rows: gapRows(gaps) });
  return [host];
}
// 마지막 분석 결과를 보고 있는 그래프로 그린다(화면 모드가 바뀌어도).
function drawAna() {
  const keep = arguments[0] === true;  // true면 슬라이더 범위를 그대로 둔다(슬라이더·보일 행 수를 만질 때)
  const d = A.result;
  const text = !!d && ["groups", "trend"].includes(A.view) && d.values.some(v => v != null && typeof v !== "number");  // 최빈값·이어 붙이기 같은 글자 결과는 그래프 없이 표로만
  const flow = !!d && A.view === "funnel";
  $("a-box").classList.toggle("hidden", !d || text || flow);
  $("a-tools").classList.toggle("hidden", !d || text || flow);
  $("a-funnel").classList.toggle("hidden", !flow);
  $("a-funnel-text").classList.toggle("hidden", !flow);
  $("a-funnel").replaceChildren();
  $("a-segs").replaceChildren(...(flow ? [...gapTable(d.gaps), ...segTables(d.segments)] : d && A.view === "ab" ? pairBlock(d) : []));
  $("a-notes").replaceChildren(...(d ? noteLines(d) : []));
  $("a-tabs").classList.toggle("hidden", !d || d.views.length < 2);
  $("a-full").disabled = !d;
  $("a-rows-box").classList.toggle("hidden", !d);  // 결과가 없으면 보일 행 수 칸도 숨긴다
  $("a-full").classList.toggle("hidden", SERVER.analysis.whole.includes(d?.kind) || ["funnel", "stages"].includes(d?.kind));  // 퍼널과 새 분석 6종은 늘 테이블 전체로 구한다(prep_app.whole)
  const noCard = NEW_KINDS.includes(d?.kind);  // 새 분석 8종은 이번에는 대시보드 카드로 담지 않는다
  $("a-add").disabled = !d || text || noCard;
  $("a-add").title = noCard ? "이 분석은 아직 대시보드 카드로 담지 않아요" : ADD_TITLE;
  if (!d) { $("a-title").textContent = "분석"; $("a-table").innerHTML = ""; $("a-data").textContent = ""; return; }
  $("a-tabs").replaceChildren(...d.views.map(v => el("button", { textContent: VIEW_NAMES[v], className: v === A.view ? "on" : "", onclick: () => { A.view = v; drawAna(); } })));
  $("a-title").textContent = anaTitle(d, A.view);
  $("a-sub").textContent = A.view === "pareto" ? gradeLine(d) : "";
  $("a-data").textContent = anaNote(d);
  $("a-box").style.setProperty("--ratio", VIEW_RATIOS[A.view] ?? 16 / 9);
  const n = text ? 0 : anaSpan(d, A.view);
  if (!keep) { A.lo = 0; A.hi = n; for (const id of ["a-lo", "a-hi"]) { $(id).max = n; } $("a-lo").value = 1; $("a-hi").value = n; }
  $("a-range").classList.toggle("hidden", n < 2);
  showRange(d, n);
  const limit = parseInt($("a-rows").value, 10);  // 보일 행 수: 범위 안에서 앞쪽 몇 행만
  const pinned = A.view === "values" ? (r, i) => i >= d.values.labels.length : A.view === "rfm" ? (r, i) => i >= d.segments.length : A.view === "waterfall" ? (r, i) => i >= d.groups.length : undefined;  // "(빈 값)"·"그 밖의 값" 줄
  const cut = (A.view === "groups" || A.view === "trend") && d.more ? { total: null } : A.view === "values" && d.values.other ? { total: d.values.unique } : undefined;
  const cells = A.view === "hist" ? 0 : n;  // 히스토그램의 표는 통계 표라 구간 범위로 자르지 않는다
  sortedTable($("a-table"), anaTable(d, A.view), (h, x, s) => resultTable(h, x, s), { pinned, cut, slice: rows => anaRows(rows, cells, A.lo, limit > 0 ? Math.min(A.hi, A.lo + limit) : A.hi) });
  if (flow) {
    $("a-funnel").append(sankeySvg(d.steps, { text: getComputedStyle(document.documentElement).getPropertyValue("--text").trim() || "currentColor" }));
    $("a-funnel-text").textContent = funnelSentence(d.steps);
    return;
  }
  if ($("ana").classList.contains("hidden") || text) return;  // 감춰져 있으면 보일 때 그린다
  anaDraw({ kind: "ana", payload: n ? anaSlice(d, A.view, A.lo, A.hi) : d, opts: { view: A.view, ratio: VIEW_RATIOS[A.view] ?? 16 / 9, auto: anaAuto, all: $("a-all").checked } });
}
// 슬라이더 글(화면 읽기용 aria-valuetext 포함). 시계열·그룹은 이름, 히스토그램은 구간 번호
function showRange(d, n) {
  if (n < 2) return;
  const name = i => A.view === "hist" ? `구간 ${i}` : A.view === "values" ? `${i}번째 값` : A.view === "pareto" ? `${i}번째 항목` : String(["trend", "groups", "season"].includes(A.view) ? d.labels[i - 1] : i);
  $("a-lo").setAttribute("aria-valuetext", `${name(A.lo + 1)}부터`);
  $("a-hi").setAttribute("aria-valuetext", `${name(A.hi)}까지`);
  $("a-rtext").textContent = `${name(A.lo + 1)} ~ ${name(A.hi)} (${A.hi - A.lo}/${n})`;
}
// 슬라이더를 움직이면 시작이 끝을 넘지 않게 맞추고 그림·표만 다시 그린다(슬라이더는 그대로 두어 키보드 조작이 끊기지 않는다).
function moveRange(fromLo) {
  let lo = +$("a-lo").value, hi = +$("a-hi").value;
  if (lo > hi) { if (fromLo) hi = lo; else lo = hi; $("a-lo").value = lo; $("a-hi").value = hi; }
  A.lo = lo - 1; A.hi = hi;
  drawAna(true);
}
$("a-lo").oninput = () => moveRange(true);
$("a-hi").oninput = () => moveRange(false);
$("a-rall").onclick = () => drawAna();
$("a-rows").oninput = () => drawAna(true);

// ---------- 대시보드에 추가: 지금 보이는 분석 그래프를 카드로 ----------
// 카드 = 다시 구할 기록(source: 테이블·전처리 recipe·분석 how)과 지금 결과(data.values). 저장한 대시보드나 새 대시보드에 서버가 더해 저장한다(연결의 key·label은 서버가 채운다).
toggleDrop($("a-add"), $("a-pop"));
document.addEventListener("click", e => { if (!e.target.closest(".pop, .menu")) $("a-pop").classList.add("hidden"); });
const NEW_DASH = "";
async function fillDashboards() {
  const found = (await api("/api/dash")).dashboards;
  $("a-dash").replaceChildren(el("option", { value: NEW_DASH, textContent: "새 대시보드" }), ...found.map(d => el("option", { value: d.id, textContent: `${d.name} (카드 ${d.cards}개)` })));
  $("a-dash-name").classList.toggle("hidden", $("a-dash").value !== NEW_DASH);
}
$("a-add").addEventListener("click", () => fillDashboards().catch(e => say("err-ana", e.message)));
$("a-dash").onchange = () => $("a-dash-name").classList.toggle("hidden", $("a-dash").value !== NEW_DASH);
$("a-dash-go").onclick = () => busy($("a-dash-go"), async () => {
  const d = A.result;
  if (!d) return;
  if (String(d.base || "").startsWith("@")) return say("err-ana", "쿼리에서 보낸 검색 결과는 프로그램을 켠 동안만 있어서 대시보드 카드로 만들 수 없어요. 가상 테이블로 저장한 뒤 넣어 주세요.");  // F08: 보낸 검색은 메모리에만
  const { how, prepped, base, recipe, views, sample: used, ...values } = d;  // 구한 값만 data에 둔다
  const flow = ["funnel", "stages"].includes(d.kind), card = { kind: flow ? "funnel" : "analysis", title: `${base} · ${anaTitle(d, A.view)}`, colors: { series: {}, parts: {}, order: [], parts_order: [] },
    view: { type: "", ratio: VIEW_RATIOS[A.view] ?? 16 / 9, analysis_view: A.view, ...(Object.keys(anaAuto).length ? { auto: anaAuto } : {}), ...($("a-all").checked ? { all_data: true } : {}) }, source: { schema: recipe.schema || "", table: base, recipe, analysis: how, full: !!values.data?.full, sample: used, ...(flow ? { date: how.time } : {}) },
    data: { values, computed_at: new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 19), note: anaNote(d) } };
  try {
    const added = await api("/api/dash/card/add", { dashboard: $("a-dash").value, name: $("a-dash-name").value, card });
    $("a-pop").classList.add("hidden");
    showAdded("err-ana", added.dashboard);
  } catch (e) { say("err-ana", e.message); }
});
// 자동 규칙 판·[모든 데이터]·그림 저장. 끈 규칙은 쿼리 화면과 같은 query_auto로 기억한다. 값이 달라지는 규칙(구간 수·꼬리·점 밀도)은 같은 분석을 다시 구한다.
const ANA_RULES = ["bins", "tail", "density", "num"], ANA_AUTO_KINDS = ["distribution", "scatter"], ANA_AUTO_VIEWS = ["hist", "scatter"];
const otherRules = o => Object.fromEntries(Object.entries(o).filter(([k]) => !ANA_RULES.includes(k)));  // 이 판이 다루지 않는 규칙
let anaAuto = { ...(SERVER.dash.viz.query_auto || {}) };
const reAna = () => A.result && ANA_AUTO_VIEWS.includes(A.view) ? runAna(!!A.result.data?.full) : drawAna(true);
autoPanel($("a-auto"), ANA_RULES, anaAuto, async changed => {
  anaAuto = { ...otherRules(anaAuto), ...changed };
  try { await saveVizPrefs({ ...SERVER.dash.viz, query_auto: { ...otherRules(SERVER.dash.viz.query_auto || {}), ...changed } }); }
  catch (e) { say("err-ana", e.message); }
  reAna();
});
$("a-all").onchange = reAna;
document.querySelectorAll("[data-aimage]").forEach(b => b.onclick = () => busy(b, async () => {
  try { download(await anaDraw.picture($("a-image").value, b.dataset.aimage, $("a-export-all").checked), "analysis." + imageExt(b.dataset.aimage)); }
  catch (e) { say("err-ana", e.message); }
}));
$("theme").addEventListener("click", () => drawAna(true));  // common.js의 onclick이 화면 모드를 바꾼 뒤에 다시 그린다.
showAnaForm();
drawAna();
