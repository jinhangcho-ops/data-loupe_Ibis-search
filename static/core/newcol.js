// [+] 새 컬럼 상자(P9). 쿼리·전처리 화면이 함께 쓴다. 왼쪽 방식 13개, 오른쪽 폼, 아래 미리보기(5행)·이름·[추가].
// - NewCol.open({ columns: () => [[이름, 종류], …], screen: "query"|"prep", funcs: Funcs, groupable, values?: 컬럼 => Promise<값[] | {values, more, partial}>, onPreview: lines => Promise<{columns, rows}>, onAdd: ({ name, lines, joins }) => void })
//   values는 값 바꿔 쓰기 상자(PrepMenu.replaceBox)의 값 목록(쿼리는 /api/values, 전처리는 표본 분포).
//   onAdd에 계산 줄 목록(lines, calc.py 문법: `식 …`, `그룹집계 …`, `전처리 …`, 창 함수 줄, `펼치기 …`)과 조인 줄(joins, `<테이블> <종류> <키>=<키>`, 쿼리 화면만)을 준다.
//   onAdd가 끝나면 닫는다. onAdd가 실패(예외·거절된 Promise)하거나 false를 돌려주면 상자를 두고 오류를 상자 안에 보인다.
// - NewCol.lines(form): 폼 값 → 계산 줄 문자열 배열(순수 함수, 시험이 node로 부른다). 칸이 비거나 새 컬럼 이름이 규칙(글자·숫자·밑줄, 빈칸 없이)에 안 맞으면 Error(한국어 안내). 조인은 "조인 "으로 시작하는 줄.
//   form.how는 METHODS의 id(calc·cond·date·text·window·group·stat·encode·struct·behavior·geo·join·expr), name은 새 컬럼 이름. 방식별 칸은 각 폼의 read()가 정한다.
// common.js(el·resultTable)·funcs.js(Funcs)·prepmenu.js(PrepMenu: 종류 이름·치환 상자)가 먼저 불려 있어야 한다.
const NewCol = (() => {
  const UNITS = ["year", "quarter", "month", "week", "day", "hour", "minute", "second"];
  const KO = { year: "년", quarter: "분기", month: "개월", week: "주", day: "일", hour: "시간", minute: "분", second: "초" };
  // 한 칸 글자(②)는 같은 묶음의 단위끼리만: 달 묶음(월 수로 셈)과 시간 묶음(초 수로 셈). 연·월은 가득 찬 수라 날·시와 섞으면 나머지를 셀 수 없다.
  const SIZE = { year: [0, 12], quarter: [0, 3], month: [0, 1], week: [1, 604800], day: [1, 86400], hour: [1, 3600], minute: [1, 60], second: [1, 1] };
  const RANKS = ["순위", "row_number", "dense_rank", "percent_rank"];
  const TEXT_FUNCS = ["length", "upper", "lower", "trim", "left", "right", "split_part", "contains", "find", "regex_extract", "regex_replace", "lpad", "replace"];
  const STRUCT_FUNCS = ["array_length", "array_get", "json_get", "struct_field"];
  const GEO_FUNCS = ["haversine", "within_km", "in_box", "grid_id"];

  // ---- 계산 줄 만들기(순수) ----
  const need = (v, what) => { if (v === undefined || v === null || String(v).trim() === "") throw new Error(`칸을 채워 주세요: ${what}`); return String(v).trim(); };
  // 식 안에서 컬럼 이름으로 쓸 수 없는 낱말: calc.split_words가 식 안에서도 읽는 옵션 낱말과, 식을 파이썬 문법으로 읽어 깨지는 예약어·None·True·False
  const WORDS = new Set(["이름", "기준", "그룹", "False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del", "elif", "else", "except",
    "finally", "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while", "with", "yield"]);
  const ref = c => /^[\p{L}_][\p{L}\p{N}_]*$/u.test(c) && !WORDS.has(c) ? c : `col(${JSON.stringify(c)})`;  // 식 안의 컬럼: 공백 있는 이름·WORDS와 같은 이름은 col("…")
  const gcol = c => /[(),]/.test(c) ? (c.includes("'") ? `"${c}"` : `'${c}'`) : c;  // 그룹집계 괄호 안의 값 컬럼: 괄호·쉼표가 든 이름은 따옴표로(서버가 parse.unquote로 푼다)
  const SPLIT = /(^|\s)(이름|기준|그룹)(\s|$)/;  // 낱말 줄에서 옵션으로 읽히는 낱말(따로 떨어진 것)
  const NAME = /^[A-Za-z_가-힣][A-Za-z0-9_가-힣]{0,62}$/;  // 새 컬럼 이름: 서버 prep_derive.check_name(NAME)과 같은 정규식(영문·한글·숫자·밑줄, 글자로 시작, 63자까지)
  const GSPLIT = /(^|\s)(조건|기준)(\s|$)/;  // 그룹집계 줄(calc GROUP_PARTS)에서 그룹 컬럼 뒤 옵션으로 읽히는 낱말
  const q = s => { s = String(s).replace(/\\/g, "\\\\"); return !s.includes("'") ? `'${s}'` : !s.includes('"') ? `"${s}"` : `'${s.replace(/'/g, "\\'")}'`; };  // 식 안의 글자
  const val = v => { const s = String(v ?? "").trim(); return s === "" ? "None" : /^(['"]).*\1$/s.test(s) || /^-?\d+(\.\d+)?$/.test(s) ? s : q(s); };  // 숫자 모양이면 수, 따옴표로 감쌌으면 그대로
  const lit = v => v && typeof v === "object" ? ref(v.col) : typeof v === "number" ? String(v) : q(v);  // 함수 인자: {col}은 컬럼, 수는 수, 그 밖은 글자
  const csv = t => String(t ?? "").split(",").map(s => s.trim()).filter(Boolean);
  const OPS = { "=": "==" };

  function lines(f) {
    const X = String(f.name ?? "").trim() || "새컬럼", as = ` 이름 ${X}`, E = body => `식 ${body}${as}`;
    if (!NAME.test(X)) throw new Error("새 컬럼 이름은 글자·숫자·밑줄로, 빈칸 없이 63자까지 써요(숫자로 시작하지 않게).");  // 여러 줄 방식이 반만 들어가지 않게 줄을 만들기 전에 막는다
    const by = (o, dir) => ` 기준 ${need(o, "기준 컬럼")}${dir === "desc" ? " desc" : ""}`;
    const grp = g => { const v = [].concat(g ?? []).filter(Boolean); return v.length ? ` 그룹 ${v.join(", ")}` : ""; };
    const fn = (id, args) => { const a = [...(args ?? [])]; if (a.some(x => x === undefined || x === "")) throw new Error("함수 칸을 채워 주세요."); return E(`${id}(${a.map(lit).join(", ")})`); };
    const c = () => ref(need(f.col, "컬럼"));
    // 창 함수·행동 분석·백분위 줄은 낱말 줄이라(calc.split_words) 컬럼 이름 안에 따로 떨어진 이름·기준·그룹 낱말을 옵션으로 읽는다: 줄을 만들기 전에 막는다
    const gwords = (...cols) => { for (const c of cols.flat()) { const w = String(c ?? "").match(GSPLIT)?.[2]; if (w) throw new Error(`'${c}' 컬럼 이름에는 '${w}' 낱말이 있어 그룹별 집계의 그룹으로 못 써요. 컬럼 이름을 바꿔 주세요.`); } };
    const words = (...cols) => { for (const c of cols.flat()) { const w = String(c ?? "").match(SPLIT)?.[2]; if (w) throw new Error(`'${c}' 컬럼 이름에는 '${w}' 낱말이 있어 이 방식으로는 못 써요. 컬럼 이름을 바꾸거나 그룹별 집계를 쓰세요.`); } };
    switch (f.how) {
      case "calc": {
        const a = ref(need(f.a, "A")), b = f.b ? ref(f.b) : val(need(f.value, "B"));
        if (f.op === "ratio") return [E(`safe_divide(${a}, ${b})`)];
        if (f.op === "concat") return [E(`concat(${a}, ${q(f.sep ?? "")}, ${b})`)];
        return [E(`${a} ${need(f.op, "계산")} ${b}`)];
      }
      case "cond": {
        if (f.kind === "case") {
          const rules = (f.rules ?? []).filter(r => r.col && String(r.value ?? "").trim() !== "");
          if (!rules.length) throw new Error("조건 줄을 하나 이상 채워 주세요.");
          // case의 결과는 한 종류여야 한다: 글자 결과가 하나라도 있으면 숫자 모양 결과도 글자로
          const words = [...rules.map(r => r.then), f.otherwise].some(v => val(v).startsWith("'") || val(v).startsWith('"'));
          const out = v => words && /^-?\d+(\.\d+)?$/.test(String(v ?? "").trim()) ? q(String(v).trim()) : val(v);
          return [E(`case(${[...rules.flatMap(r => [`${ref(r.col)} ${OPS[r.op] ?? r.op} ${r.text ? q(String(r.value).trim()) : val(r.value)}`, out(r.then)]), out(f.otherwise)].join(", ")})`)];
        }
        if (f.kind === "bucket") {
          const edges = csv(need(f.bounds, "경계값")), names = csv(f.labels);
          if (names.length && names.length !== edges.length + 1) throw new Error(`구간 이름은 경계보다 하나 많게 써요(첫 경계 아래·사이·마지막 경계 위). 경계 ${edges.length}개면 이름 ${edges.length + 1}개예요.`);
          return [E(`bucket(${c()}, [${edges.join(", ")}]${names.length ? `, [${names.map(q).join(", ")}]` : ""})`)];
        }
        if (f.kind === "isin") return [E(`isin(${c()}, ${csv(need(f.values, "값")).map(v => f.text ? q(v) : val(v)).join(", ")})`)];
        if (f.kind === "between") return [E(`between(${c()}, ${f.text ? q(need(f.low, "최소")) : val(need(f.low, "최소"))}, ${f.text ? q(need(f.high, "최대")) : val(need(f.high, "최대"))})`)];
        return [E(`isnull(${c()})`)];
      }
      case "date": {
        const k = f.kind;
        if (k === "part") return [E(`${need(f.part, "뽑을 것")}(${c()})`)];
        if (k === "since") return [E(`${f.ref === "today" ? "diff_today" : "diff_now"}(${c()}, ${q(f.unit ?? "day")})`)];
        if (k === "add") return [E(`date_add(${c()}, ${need(f.n, "더할 수")}, ${q(f.unit ?? "day")})`)];
        if (k === "trunc") return [E(`truncate(${c()}, ${q(f.unit ?? "month")})`)];
        if (k === "tz") return [E(`to_timezone(${c()}, ${q(need(f.zone, "시간대"))})`)];
        if (k === "parse") return [E(`parse_date(${c()}, ${q(need(f.format, "형식"))})`)];
        const a = ref(need(f.a, "시작 날짜")), b = ref(need(f.b, "끝 날짜")), diff = u => `date_diff(${a}, ${b}, ${q(u)})`;
        if (f.style === "one") return [E(diff(f.unit ?? "day"))];
        const units = UNITS.filter(u => (f.units ?? []).includes(u));
        if (!units.length) throw new Error("단위를 하나 이상 고르세요.");
        if (f.style !== "text") return units.map(u => `식 ${diff(u)} 이름 ${X}_${KO[u]}`);  // ① 단위마다 한 컬럼
        if (new Set(units.map(u => SIZE[u][0])).size > 1) throw new Error("한 칸 글자는 연·분기·월끼리, 또는 주·일·시간·분·초끼리만 묶을 수 있어요.");
        const small = units.at(-1), S = diff(small), size = u => SIZE[u][1] / SIZE[small][1];  // 가장 작은 단위의 수에서 큰 단위부터 나눠 낸다(② 1년 2개월)
        const bits = units.flatMap((u, i) => {
          // 시작이 끝보다 늦으면 S가 음수: floor(내림)와 %(0 쪽 자름)가 어긋나지 않게 절댓값으로 나누고 부호를 붙인다(-14개월 → -1년 -2개월)
          const n = size(u), p = i && size(units[i - 1]), part = !i ? (n === 1 ? S : `sign(${S}) * floor(abs(${S}) / ${n})`) : n === 1 ? `sign(${S}) * (abs(${S}) % ${p})` : `sign(${S}) * floor(abs(${S}) % ${p} / ${n})`;
          return [part, q(KO[u] + (i < units.length - 1 ? " " : ""))];
        });
        return [E(`concat(${bits.join(", ")})`)];
      }
      case "text": case "geo": return [fn(need(f.fn, "함수"), f.args)];
      case "struct": {
        if (STRUCT_FUNCS.includes(f.kind)) return [fn(f.kind, f.args)];
        const col = need(f.col, "컬럼");
        if (f.kind === "unnest") return [`펼치기 ${col}`];
        if (f.kind === "expand") return [`전처리 expand ${col} true`];
        return [`전처리 counts ${col} ${JSON.stringify(f.sep ? { sep: f.sep } : {})}`];
      }
      case "window": {
        words(f.col, f.order, f.group);
        const k = need(f.kind, "계산"), g = grp(f.group), o = by(f.order, f.dir);
        if (k === "share_of_group") return [`share_of_group ${need(f.col, "값 컬럼")}${g}${as}`];
        if (RANKS.includes(k)) return [`${k}${o}${g}${as}`];
        const col = need(f.col, "값 컬럼");
        if (k === "이동평균" || k === "이동합계") return [`${k} ${col} ${need(f.size, "크기")}${o}${g}${as}`];
        if (k === "lag" || k === "lead") return [`${k} ${col} ${need(f.n, "n")}${o}${g}${as}`];
        return [`${k} ${col}${o}${g}${as}`];
      }
      case "group": {
        const gs = [].concat(f.groups ?? []).filter(Boolean);
        if (gs.length > 3) throw new Error("그룹은 3개까지 고를 수 있어요.");
        gwords(gs);
        const col = !f.combo && f.agg === "count" && !f.col ? "" : need(f.col, "값 컬럼"), g = grp(gs);
        const agg = (id, name, arg, extra = "") => `그룹집계 ${id}(${gcol(col)}${arg !== undefined && arg !== "" ? `, ${arg}` : ""})${g}${extra} 이름 ${name}`;
        if (f.combo === "share") return [agg("sum", `${X}_합`), E(`safe_divide(${ref(col)}, ${ref(`${X}_합`)})`)];
        if (f.combo === "z") return [agg("mean", `${X}_평균`), agg("std", `${X}_표준편차`), E(`(${ref(col)} - ${ref(`${X}_평균`)}) / ${ref(`${X}_표준편차`)}`)];
        return [agg(need(f.agg, "집계"), X, f.arg, (f.order ? by(f.order, f.dir) : "") + (String(f.where ?? "").trim() ? ` 조건 ${f.where.trim()}` : ""))];
      }
      case "stat": {
        const x = c(), gs = [].concat(f.groups ?? []).filter(Boolean), g = grp(gs), col = need(f.col, "컬럼");
        if (gs.length && (f.kind === "z" || f.kind === "minmax")) gwords(gs);
        if (f.kind === "z") return gs.length ? [`그룹집계 mean(${gcol(col)})${g} 이름 ${X}_평균`, `그룹집계 std(${gcol(col)})${g} 이름 ${X}_표준편차`, E(`(${x} - ${ref(`${X}_평균`)}) / ${ref(`${X}_표준편차`)}`)]
          : [E(`(${x} - mean(${x})) / std(${x})`)];
        if (f.kind === "minmax") return gs.length ? [`그룹집계 min(${gcol(col)})${g} 이름 ${X}_최소`, `그룹집계 max(${gcol(col)})${g} 이름 ${X}_최대`, E(`(${x} - ${ref(`${X}_최소`)}) / (${ref(`${X}_최대`)} - ${ref(`${X}_최소`)})`)]
          : [E(`(${x} - min(${x})) / (max(${x}) - min(${x}))`)];
        if (f.kind === "log") return [E(`ln(${x})`)];
        if (f.kind === "pct") { words(col, ...gs); return [`percent_rank 기준 ${col}${g}${as}`]; }
        if (f.method === "iqr") {
          const k = String(f.k ?? "").trim() || "1.5", q1 = `quantile(${x}, 0.25)`, q3 = `quantile(${x}, 0.75)`;
          return [E(`(${x} < ${q1} - ${k} * (${q3} - ${q1})) or (${x} > ${q3} + ${k} * (${q3} - ${q1}))`)];
        }
        return [E(`abs(${x} - mean(${x})) > ${String(f.k ?? "").trim() || "3"} * std(${x})`)];
      }
      case "encode": {
        const col = need(f.col, "컬럼");
        if (f.kind === "rare") { const min = +need(f.min, "최소 횟수"); if (!Number.isInteger(min) || min < 2) throw new Error("최소 횟수는 2 이상의 정수예요."); return [`전처리 rare ${col} ${JSON.stringify({ min, label: f.label || "기타" })}`]; }
        if (f.kind === "replace") { if (!f.spec) throw new Error("[바꿀 값 정하기]로 바꿀 값을 정해 주세요."); return [`전처리 replace ${col} ${JSON.stringify(f.spec)}`]; }
        return [`전처리 ${f.kind === "onehot" ? "onehot" : "label"} ${col} true`];
      }
      case "behavior": {
        words(f.user, f.time);
        const u = need(f.user, "사람(그룹) 컬럼"), t = need(f.time, "시각 컬럼"), unit = q(f.unit ?? "minute"), g = ` 그룹 ${u}`, o = ` 기준 ${t}`;
        const prev = `${X}_prev`, first = `${X}_first`;
        if (f.kind === "session") {  // 직전 행동과 N분 넘게 떨어지면 새 세션: 직전 시각 → 새 세션 표시(1/0) → 그 누적 합이 세션 번호
          const n = need(f.minutes, "세션을 나눌 분");
          return [`lag ${t} 1${o}${g} 이름 ${prev}`, `식 iif(isnull(${ref(prev)}) or date_diff(${ref(prev)}, ${ref(t)}, 'minute') > ${n}, 1, 0) 이름 ${X}_new`, `누적합 ${X}_new${o}${g}${as}`];
        }
        if (f.kind === "gap") return [`lag ${t} 1${o}${g} 이름 ${prev}`, E(`date_diff(${ref(prev)}, ${ref(t)}, ${unit})`)];
        if (f.kind === "since_first") return [`first_value ${t}${o}${g} 이름 ${first}`, E(`date_diff(${ref(first)}, ${ref(t)}, ${unit})`)];
        if (f.kind === "cohort") gwords(u);
        if (f.kind === "cohort") return [`그룹집계 min(${gcol(t)})${g} 이름 ${first}`, E(`truncate(${ref(first)}, ${q(f.unit ?? "month")})`)];
        if (f.kind === "nth") return [`순위${o}${g}${as}`];
        if (f.kind === "revisit") return [`순위${o}${g} 이름 ${X}_n`, E(`${ref(`${X}_n`)} > 1`)];
        throw new Error("고를 수 없는 방식이에요.");
      }
      case "join": return [`조인 ${need(f.table, "테이블")} ${f.kind || "left"} ${need(f.keys, "키")}`];
      case "expr": return [E(need(f.text, "식"))];
    }
    throw new Error("방식을 고르세요.");
  }

  // ---- 상자 ----
  const METHODS = [["calc", "계산·조합"], ["cond", "조건·분류"], ["date", "날짜·시각", ["date", "str"]], ["text", "글자", ["str"]], ["window", "순서·창"], ["group", "그룹별 집계 붙이기"],
    ["stat", "통계 변환", ["num"]], ["encode", "인코딩·치환"], ["struct", "구조", ["struct", "list", "str"]], ["behavior", "행동 분석 틀", ["date"]], ["geo", "지리", ["num"]], ["join", "다른 테이블"], ["expr", "식 직접"]];
  const KIND_NAMES = { date: "날짜", str: "글자", num: "숫자", struct: "구조", list: "목록" };
  // 쿼리 화면에서 막는 단계(표 전체 통계·값 목록을 먼저 읽어 서버 calc.reads_stats가 거절. PrepMenu statsOnly와 같은 규칙, 단어 개수 counts는 된다)
  const QUERY_STATS = { encode: ["label", "onehot", "rare"] };
  const TO_PREP = "쿼리에서는 전처리 화면으로 넘겨서 하세요.";

  function open({ columns, screen, funcs, groupable, values, onPreview, onAdd }) {
    const kindOf = name => PrepMenu.kindOf(columns().find(c => c[0] === name)?.[1]);
    const opt = o => { const [v, t] = [].concat(o); return el("option", { value: v, textContent: t ?? v }); };
    const named = (node, label) => { node.setAttribute("aria-label", label); node.title = label; return node; };
    const pick = (label, options) => named(el("select", {}, ...options.map(opt)), label);
    const text = (label, value = "") => named(el("input", { placeholder: label, value }), label);
    const field = (label, ctrl) => el("label", { className: "nc-field" }, el("span", { textContent: label }), ctrl);
    const colPick = (kinds, label, blank) => pick(label, [...(blank ? [["", blank]] : []), ...columns().filter(c => !kinds || kinds.includes(PrepMenu.kindOf(c[1]))).map(c => c[0])]);
    const groupPicks = () => [1, 2, 3].map(i => colPick(null, `그룹 ${i}`, `그룹 ${i} 없음`));
    const show = (node, on) => (node.closest?.(".nc-field") ?? node).classList.toggle("hidden", !on);
    const unitPick = (label, def) => { const s = pick(label, UNITS.map(u => [u, KO[u]])); s.value = def; return s; };
    // 함수 인자 칸(함수 사전 argFields): 컬럼 인자는 {col}, 나머지는 칸의 값(수 칸은 수)
    function argsBox(id) {
      const f = funcs?.get?.(id);
      if (!f) return { el: el("span", { className: "hint", textContent: "함수 사전을 불러오지 못했어요." }), read: () => [undefined] };
      const fields = funcs.argFields(f, { columns, skip: ["where"] }), got = () => fields.read();
      return { el: fields.el, read: () => {
        const v = got(), args = f.args.filter(a => fields.names.includes(a.name)).map(a => a.kind === "column" ? (v[a.name] ? { col: v[a.name] } : undefined) : v[a.name]);
        while (args.length && args.at(-1) === undefined) args.pop();
        return args;
      } };
    }
    function funcForm(how, ids) {
      const fnSel = pick("함수", ids.map(id => [id, funcs?.get?.(id)?.label ?? id])), box = el("div", { className: "nc-args" });
      let args = null;
      const draw = () => { args = argsBox(fnSel.value); box.replaceChildren(args.el); };
      fnSel.onchange = draw; draw();
      return { el: el("div", {}, field("함수", fnSel), box), read: () => ({ how, fn: fnSel.value, args: args.read() }) };
    }
    const FORMS = {
      calc() {
        const op = pick("계산", [["+", "더하기 (A + B)"], ["-", "빼기 (A - B)"], ["*", "곱하기 (A × B)"], ["/", "나누기 (A ÷ B)"], ["ratio", "비율 (0으로 나누면 빈 값)"], ["concat", "글자 잇기"]]);
        const a = colPick(null, "A"), b = colPick(null, "B", "값 직접 쓰기"), value = text("B 값"), sep = text("사이 글자", "-");
        return { el: el("div", {}, field("계산", op), field("A", a), field("B", b), field("B 값", value), field("사이 글자", sep)),
          sync: () => { show(value, !b.value); show(sep, op.value === "concat"); },
          read: () => ({ how: "calc", op: op.value, a: a.value, b: b.value, value: value.value, sep: sep.value }) };
      },
      cond() {
        const kind = pick("방식", [["case", "조건마다 값 (case)"], ["bucket", "구간 나누기"], ["isin", "값 목록에 있는지"], ["between", "범위 안에 있는지"], ["isnull", "빈 값인지"]]);
        const rules = el("div", { className: "nc-rules" });
        const rule = () => {
          const c = colPick(null, "컬럼"), o = pick("비교", ["=", "!=", ">", ">=", "<", "<="]), v = text("값"), t = text("결과");
          const row = el("div", { className: "row nc-rule" }, c, o, v, el("span", { textContent: "→" }), t, el("button", { type: "button", className: "ghost", textContent: "×", title: "이 줄 빼기", onclick: () => row.remove() }));
          row.read = () => ({ col: c.value, op: o.value, value: v.value, then: t.value, text: kindOf(c.value) === "str" });
          return row;
        };
        rules.append(rule());
        const caseBox = el("div", {}, rules, el("button", { type: "button", className: "btn", textContent: "줄 더하기", onclick: () => rules.append(rule()) }));
        const otherwise = text("아니면 (비우면 빈 값)"), col = colPick(null, "컬럼"), bounds = text("경계값, 예: 0, 10, 100"), labels = text("구간 이름(쉼표로, 경계보다 하나 많게, 비우면 자동)");
        const values = text("값들, 예: A, B"), low = text("최소"), high = text("최대");
        return { el: el("div", {}, field("방식", kind), caseBox, field("아니면", otherwise), field("컬럼", col), field("경계값", bounds), field("구간 이름", labels), field("값들", values), field("최소", low), field("최대", high)),
          sync: () => { const k = kind.value; show(caseBox, k === "case"); show(otherwise, k === "case"); show(col, k !== "case"); show(bounds, k === "bucket"); show(labels, k === "bucket"); show(values, k === "isin"); show(low, k === "between"); show(high, k === "between"); },
          read: () => kind.value === "case" ? { how: "cond", kind: "case", rules: [...rules.children].map(r => r.read()), otherwise: otherwise.value }
            : { how: "cond", kind: kind.value, col: col.value, bounds: bounds.value, labels: labels.value, values: values.value, low: low.value, high: high.value, text: kindOf(col.value) === "str" } };
      },
      date() {
        const kind = pick("방식", [["part", "날짜에서 뽑기"], ["diff", "두 날짜의 차이"], ["since", "지금까지 지난 시간"], ["add", "날짜 더하기"], ["trunc", "단위로 맞추기(월초 등)"], ["tz", "시간대 바꾸기"], ["parse", "글자를 날짜로"]]);
        const col = colPick(["date"], "날짜 컬럼"), scol = colPick(["str"], "글자 컬럼"), a = colPick(["date"], "시작 날짜"), b = colPick(["date"], "끝 날짜");
        const part = pick("뽑을 것", [["year", "연"], ["month", "월"], ["day", "일"], ["weekday", "요일"], ["hour", "시"]]);
        const style = pick("보이는 꼴", [["lines", "① 단위마다 컬럼"], ["text", "② 한 칸 글자(1년 2개월)"], ["one", "③ 한 단위 숫자"]]);
        const units = el("div", { className: "nc-units" }, ...UNITS.map(u => el("label", { className: "check" }, el("input", { type: "checkbox", value: u, checked: u === "day" }), KO[u])));
        const unit = unitPick("단위", "day"), ref_ = pick("기준", [["now", "지금 시각"], ["today", "오늘 날짜"]]), n = text("더할 수(음수면 빼기)"), zone = text("시간대, 예: Asia/Seoul", "Asia/Seoul"), format = text("형식, 예: %Y-%m-%d", "%Y-%m-%d");
        return { el: el("div", {}, field("방식", kind), field("날짜 컬럼", col), field("글자 컬럼", scol), field("시작 날짜", a), field("끝 날짜", b), field("뽑을 것", part), field("보이는 꼴", style), field("단위들", units), field("단위", unit), field("기준", ref_), field("더할 수", n), field("시간대", zone), field("형식", format)),
          sync: () => {
            const k = kind.value, diff = k === "diff";
            show(col, ["part", "since", "add", "trunc", "tz"].includes(k)); show(scol, k === "parse"); show(a, diff); show(b, diff); show(part, k === "part"); show(style, diff);
            show(units, diff && style.value !== "one"); show(unit, ["since", "add", "trunc"].includes(k) || (diff && style.value === "one")); show(ref_, k === "since"); show(n, k === "add"); show(zone, k === "tz"); show(format, k === "parse");
          },
          read: () => ({ how: "date", kind: kind.value, col: kind.value === "parse" ? scol.value : col.value, a: a.value, b: b.value, part: part.value, style: style.value,
            units: [...units.querySelectorAll("input:checked")].map(i => i.value), unit: unit.value, ref: ref_.value, n: n.value, zone: zone.value, format: format.value }) };
      },
      text: () => funcForm("text", TEXT_FUNCS),
      geo: () => funcForm("geo", GEO_FUNCS),
      window() {
        const kind = pick("계산", [["순위", "순위"], ["row_number", "행 번호"], ["dense_rank", "빈틈없는 순위"], ["percent_rank", "백분위 순위"], ["이동평균", "이동 평균"], ["이동합계", "이동 합계"],
          ["누적합", "누적 합"], ["이전값", "이전 값"], ["차이", "이전 값과의 차이"], ["lag", "n행 앞 값"], ["lead", "n행 뒤 값"], ["share_of_group", "그룹 안 비중"]]);
        const col = colPick(null, "값 컬럼"), size = text("크기", "7"), sizeUnit = pick("크기 단위", ["행", "일"]), n = text("n", "1"), order = colPick(null, "기준(순서)"), dir = pick("방향", [["asc", "작은 것부터"], ["desc", "큰 것부터"]]), group = colPick(null, "그룹", "그룹 없음");
        return { el: el("div", {}, field("계산", kind), field("값 컬럼", col), field("크기", el("span", { className: "row" }, size, sizeUnit)), field("n", n), field("기준", order), field("방향", dir), field("그룹", group)),
          sync: () => { const k = kind.value; show(col, !RANKS.includes(k)); show(size.parentNode, k === "이동평균" || k === "이동합계"); show(n, k === "lag" || k === "lead"); show(order, k !== "share_of_group"); show(dir, k !== "share_of_group"); },
          read: () => ({ how: "window", kind: kind.value, col: col.value, size: size.value.trim() + sizeUnit.value, n: n.value, order: order.value, dir: dir.value, group: group.value }) };
      },
      group() {
        const aggs = (funcs?.cat?.funcs ?? []).filter(f => f.aggregate).map(f => [f.id, f.label]);
        const agg = pick("집계", aggs), col = colPick(null, "값 컬럼", "값 컬럼 없음(개수)"), arg = text("인자(예: 분위수 0.9)");
        const gs = groupPicks(), order = colPick(null, "기준(순서)", "기준 없음"), dir = pick("방향", [["asc", "작은 것부터"], ["desc", "큰 것부터"]]), where = text("조건(예: 금액 > 0)");
        const combo = pick("붙일 값", [["", "집계 값 그대로"], ["share", "값 ÷ 그룹 합계(비중)"], ["z", "그룹 안 Z점수"]]);
        return { el: el("div", {}, field("붙일 값", combo), field("집계", agg), field("값 컬럼", col), field("인자", arg), field("그룹", el("span", { className: "row" }, ...gs)), field("기준", order), field("방향", dir), field("조건", where)),
          sync: () => { const plain = !combo.value; show(agg, plain); show(arg, plain); show(order, plain); show(dir, plain && !!order.value); show(where, plain); },
          read: () => ({ how: "group", combo: combo.value, agg: agg.value, col: col.value, arg: arg.value.trim(), groups: gs.map(g => g.value), order: order.value, dir: dir.value, where: where.value }) };
      },
      stat() {
        const kind = pick("변환", [["z", "Z점수"], ["minmax", "0~1로(min-max)"], ["log", "로그 ln"], ["pct", "백분위(percent_rank)"], ["outlier", "이상치 표시"]]);
        const col = colPick(["num"], "숫자 컬럼"), gs = groupPicks(), method = pick("이상치 기준", [["std", "평균에서 표준편차 k배 밖"], ["iqr", "사분위수 범위(IQR) k배 밖"]]), k = text("k (비우면 3 또는 1.5)");
        return { el: el("div", {}, field("변환", kind), field("컬럼", col), field("그룹(전체면 비움)", el("span", { className: "row" }, ...gs)), field("이상치 기준", method), field("k", k)),
          sync: () => { const v = kind.value; show(gs[0].parentNode, ["z", "minmax", "pct"].includes(v) && (groupable || v === "pct")); show(method, v === "outlier"); show(k, v === "outlier"); },
          read: () => ({ how: "stat", kind: kind.value, col: col.value, groups: gs.map(g => g.value), method: method.value, k: k.value }) };
      },
      encode() {
        const kind = pick("방식", [["label", "값을 번호로(라벨 인코딩)"], ["onehot", "값마다 컬럼으로(원-핫)"], ["rare", "드문 값 묶기"], ["replace", "값 바꿔 쓰기"]]);
        if (screen === "query") { [...kind.options].forEach(o => { if (QUERY_STATS.encode.includes(o.value)) Object.assign(o, { disabled: true, title: TO_PREP }); }); kind.value = "replace"; }  // 서버가 쿼리 줄로는 거절한다(calc.reads_stats)
        const col = colPick(null, "컬럼"), min = text("이보다 적게 나오면 묶기(2 이상)"), label = text("묶은 값 이름", "기타"), said = el("span", { className: "hint" });
        let spec = null;
        const open_ = el("button", { type: "button", className: "btn", textContent: "바꿀 값 정하기…", onclick: () => PrepMenu.replaceBox({ column: col.value, type: kindOf(col.value), values: () => values?.(col.value), onApply: s => { spec = s; said.textContent = `${s.pairs.length}가지를 정했어요.`; } }) });
        return { el: el("div", {}, field("방식", kind), field("컬럼", col), field("최소 횟수", min), field("묶은 값 이름", label), el("div", { className: "row nc-replace" }, open_, said),
          el("p", { className: "hint", textContent: "새 컬럼 이름은 단계가 정해요(예: 컬럼_label)." })),
          sync: () => { show(min, kind.value === "rare"); show(label, kind.value === "rare"); show(open_.parentNode, kind.value === "replace"); },
          read: () => ({ how: "encode", kind: kind.value, col: col.value, min: min.value, label: label.value, spec }) };
      },
      struct() {
        const kind = pick("방식", [["array_length", "목록 길이"], ["array_get", "목록의 n번째"], ["json_get", "JSON 값 꺼내기"], ["struct_field", "구조의 필드"], ["unnest", "목록을 행으로 펼치기"], ["expand", "컬럼 여러 개로 펼치기"], ["counts", "단어별 개수 세기"]]);
        const col = colPick(null, "컬럼"), sep = text("구분자(비우면 공백)"), box = el("div", { className: "nc-args" });
        let args = null, drawn = "";
        return { el: el("div", {}, field("방식", kind), box, field("컬럼", col), field("구분자", sep)),
          sync: () => {
            const k = kind.value, isFn = STRUCT_FUNCS.includes(k);
            if (isFn && drawn !== k) { args = argsBox(k); box.replaceChildren(args.el); drawn = k; }
            show(box, isFn); show(col, !isFn); show(sep, k === "counts");
          },
          read: () => ({ how: "struct", kind: kind.value, col: col.value, sep: sep.value, args: STRUCT_FUNCS.includes(kind.value) ? args.read() : undefined }) };
      },
      behavior() {
        const kind = pick("틀", [["session", "세션 번호"], ["gap", "직전 행동과의 간격"], ["since_first", "첫 행동 이후 경과"], ["cohort", "코호트(첫 날짜의 기간)"], ["nth", "몇 번째 행동"], ["revisit", "재방문인지"]]);
        const user = colPick(null, "사람(그룹) 컬럼"), time = colPick(["date"], "시각 컬럼"), minutes = text("이 분보다 떨어지면 새 세션", "30"), unit = unitPick("단위", "minute");
        return { el: el("div", {}, field("틀", kind), field("사람", user), field("시각", time), field("세션 간격(분)", minutes), field("단위", unit),
          el("p", { className: "hint", textContent: "누적 고유 수(지금까지 나온 서로 다른 값 수)는 계산 줄로 만들 수 없어 빠졌어요. 그룹별 집계의 고유 개수를 써 보세요." })),
          sync: () => { show(minutes, kind.value === "session"); show(unit, ["gap", "since_first", "cohort"].includes(kind.value)); },
          read: () => ({ how: "behavior", kind: kind.value, user: user.value, time: time.value, minutes: minutes.value, unit: unit.value }) };
      },
      join() {
        const table = text("붙일 테이블 이름"), kind = pick("조인 종류", [["left", "기준 행 모두(left)"], ["inner", "양쪽에 있는 행만(inner)"]]), keys = text("키, 예: 고객ID=id");
        return { el: el("div", {}, field("테이블", table), field("종류", kind), field("키", keys), el("p", { className: "hint", textContent: "조인 목록에 더해요. 붙인 테이블의 컬럼이 새로 생겨요." })),
          read: () => ({ how: "join", table: table.value.trim(), kind: kind.value, keys: keys.value.trim() }) };
      },
      expr() {
        const area = named(el("textarea", { rows: 3, placeholder: "예: 금액 * 수량, iif(금액 >= 10000, '고액', '일반')" }), "식");
        const panel = funcs?.cat ? funcs.panel({ columns, onpick: f => { const at = area.selectionStart ?? area.value.length; area.value = area.value.slice(0, at) + f.example + area.value.slice(area.selectionEnd ?? at); area.focus(); } }).el : el("p", { className: "hint", textContent: "함수 사전을 불러오지 못했어요. 식은 그대로 쓸 수 있어요." });
        return { el: el("div", {}, area, panel), read: () => ({ how: "expr", text: area.value.trim() }) };
      },
    };

    // 컬럼 종류에 안 맞거나 이 화면에서 못 쓰는 방식은 흐리게(누르면 이유를 보인다)
    const kinds = new Set(columns().map(c => PrepMenu.kindOf(c[1])));
    const why = (id, want) => id === "join" && screen === "prep" ? "쿼리 화면에서만 써요. 쿼리에서 조인한 뒤 전처리로 넘겨 주세요."
      : id === "group" && !groupable ? "이 화면에서는 그룹별 집계를 붙일 수 없어요."
        : want && !want.some(k => kinds.has(k)) ? `${want.map(k => KIND_NAMES[k]).join("·")} 컬럼이 없어요.` : "";
    const list = el("div", { className: "nc-methods" }), formBox = el("div", { className: "nc-form" }), note = el("div", { className: "hint" });
    list.setAttribute("role", "listbox"); list.setAttribute("aria-label", "만드는 방식");
    const nameIn = text("새 컬럼 이름"), msg = el("div", { className: "err" }), preview = el("div", { className: "nc-preview" });
    let cur = null;
    function choose(id) {
      [...list.children].forEach(b => b.setAttribute("aria-selected", String(b.dataset.id === id)));
      const reason = why(id, METHODS.find(m => m[0] === id)[2]);
      msg.textContent = "";
      if (reason) { cur = null; formBox.replaceChildren(el("p", { className: "hint", textContent: reason })); return; }
      cur = FORMS[id]();
      formBox.replaceChildren(cur.el);
      cur.sync?.();
    }
    formBox.addEventListener("change", () => cur?.sync?.());
    list.append(...METHODS.map(([id, label, want]) => {
      const reason = why(id, want);
      const b = el("button", { type: "button", className: "nc-method" + (reason ? " dim" : ""), textContent: label, title: reason || label, onclick: () => choose(id) });
      b.dataset.id = id; b.setAttribute("role", "option");
      if (reason) b.setAttribute("aria-disabled", "true");
      return b;
    }));
    const current = () => {
      if (!cur) throw new Error("이 방식은 쓸 수 없어요.");
      const form = cur.read();
      if (screen === "query" && ((form.how === "encode" && QUERY_STATS.encode.includes(form.kind)) || (form.how === "struct" && form.kind === "expand" && kindOf(form.col) !== "struct")))
        throw new Error(`표 전체 값을 먼저 읽는 단계예요. ${TO_PREP}`);  // 구조 컬럼이 아닌 펼치기는 키를 찾으려 표를 읽는다(서버 reads_stats와 같은 규칙)
      const all = lines({ ...form, name: nameIn.value });
      return { lines: all.filter(l => !l.startsWith("조인 ")), joins: all.filter(l => l.startsWith("조인 ")).map(l => l.slice(3)) };
    };
    const look = () => {
      try {
        const got = current();
        if (got.joins.length) { preview.replaceChildren(el("p", { className: "hint", textContent: "조인은 추가한 뒤 결과 표에서 보여요." })); return; }
        msg.textContent = "";
        preview.replaceChildren(el("p", { className: "hint", textContent: "미리 보는 중…" }));
        Promise.resolve(onPreview?.(got.lines)).then(d => d ? resultTable(preview, d) : preview.replaceChildren()).catch(e => { preview.replaceChildren(); msg.textContent = e.message; });
      } catch (e) { msg.textContent = e.message; }
    };
    const add = () => {  // onAdd가 끝난 뒤 닫는다: 실패하거나 false를 돌려주면 입력을 그대로 두고 안내
      let got;
      try { got = current(); } catch (e) { msg.textContent = e.message; return; }
      Promise.resolve().then(() => onAdd({ name: nameIn.value.trim(), ...got })).then(ok => { if (ok !== false) close(); }, e => { msg.textContent = e.message; });
    };
    const box = el("div", { className: "modal-box nc-box" }, el("h3", { textContent: "새 컬럼 만들기" }), el("div", { className: "nc-body" }, list, formBox), note,
      el("div", { className: "row nc-foot" }, field("이름", nameIn), el("button", { type: "button", className: "btn", textContent: "미리보기", onclick: look }), el("button", { type: "button", textContent: "추가", onclick: add })), msg, preview);
    box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true"); box.setAttribute("aria-label", "새 컬럼 만들기");
    const back = el("div", { className: "modal" }, box), close = () => back.remove();
    back.onclick = e => { if (e.target === back) close(); };  // Esc도 common.js가 바깥 누르기로 보낸다
    document.body.append(back);
    note.textContent = "함수 사전을 읽는 중…";
    Promise.resolve(funcs?.load?.()).catch(() => {}).then(() => {  // 사전을 받는 사이 이미 방식을 골랐으면 그 폼·입력·안내를 덮지 않는다
      note.textContent = "";
      if (!list.querySelector('[aria-selected="true"]')) { choose("calc"); list.firstChild.focus(); }
    });
    return { el: box, close, choose };
  }
  return { open, lines, gcol, METHODS };  // gcol: 쿼리 화면 찾기(openLookup)의 그룹집계 값 컬럼도 같은 규칙
})();
