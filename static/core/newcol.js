// [+] 새 컬럼 상자(P9). 쿼리·전처리 화면이 함께 쓴다. 왼쪽 방식 16개, 오른쪽 폼, 아래 미리보기(5행)·이름·[추가].
// - NewCol.open({ columns: () => [[이름, 종류], …], screen: "query"|"prep", funcs: Funcs, groupable, values?: (컬럼, 찾을 글자) => Promise<값[] | {values, more, partial, find?}>, onPreview: lines => Promise<{columns, rows}>, onAdd: ({ name, lines, joins }) => void })
//   values는 값 바꿔 쓰기 상자(PrepMenu.replaceBox)의 값 목록(쿼리는 /api/values, 전처리는 표본 분포).
//   onAdd에 계산 줄 목록(lines, calc.py 문법: `식 …`, `그룹집계 …`, `전처리 …`, 창 함수 줄, `펼치기 …`)과 조인 줄(joins, `<테이블> <종류> <키>=<키>`, 쿼리 화면만)을 준다.
//   onAdd가 끝나면 닫는다. onAdd가 실패(예외·거절된 Promise)하거나 false를 돌려주면 상자를 두고 오류를 상자 안에 보인다.
// - NewCol.lines(form): 폼 값 → 계산 줄 문자열 배열(순수 함수, 시험이 node로 부른다). 칸이 비거나 새 컬럼 이름이 규칙(글자·숫자·밑줄, 빈칸 없이)에 안 맞으면 Error(한국어 안내). 조인은 "조인 "으로 시작하는 줄.
//   form.how는 METHODS의 id(calc·row·cond·flag·bin·date·text·window·group·stat·encode·struct·behavior·geo·join·expr), name은 새 컬럼 이름. 방식별 칸은 각 폼의 read()가 정한다.
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
  const P6 = () => typeof SERVER === "undefined" ? {} : SERVER.prep6 ?? {};  // 서버가 주는 Phase 6 기본값 안내
  const CAL = () => typeof SERVER === "undefined" ? {} : SERVER.calendar ?? {};  // 서버가 주는 날짜 설정 기본값 안내(설정을 따르는 칸의 placeholder)

  // ---- 계산 줄 만들기(순수) ----
  const need = (v, what) => { if (v === undefined || v === null || String(v).trim() === "") throw new Error(`칸을 채워 주세요: ${what}`); return String(v).trim(); };
  // 식 안에서 컬럼 이름으로 쓸 수 없는 낱말: calc.split_words가 식 안에서도 읽는 옵션 낱말과, 식을 파이썬 문법으로 읽어 깨지는 예약어·None·True·False
  const WORDS = new Set(["이름", "기준", "그룹", "False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del", "elif", "else", "except",
    "finally", "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while", "with", "yield"]);
  const ref = c => /^[\p{L}_][\p{L}\p{N}_]*$/u.test(c) && !WORDS.has(c) ? c : `col(${JSON.stringify(c)})`;  // 식 안의 컬럼: 공백 있는 이름·WORDS와 같은 이름은 col("…")
  const gcol = c => /[(),]/.test(c) ? (c.includes("'") ? `"${c}"` : `'${c}'`) : c;  // 그룹집계 괄호 안의 값 컬럼: 괄호·쉼표가 든 이름은 따옴표로(서버가 parse.unquote로 푼다)
  const NAME = /^[A-Za-z_가-힣][A-Za-z0-9_가-힣]{0,62}$/;  // 새 컬럼 이름: 서버 prep_derive.check_name(NAME)과 같은 정규식(영문·한글·숫자·밑줄, 글자로 시작, 63자까지)
  const MARK = new Set(["이름", "기준", "그룹", "조건", "테이블", "키"]);  // 서버 calc OPTIONS·MARKERS 낱말
  const bq = c => { const s = String(c); if (/[`,]/.test(s)) throw new Error(`'${s}' 컬럼 이름에는 ${s.includes("`") ? "백틱(`)이" : "쉼표가"} 있어 계산 줄로는 못 써요. 전처리 화면에서 이름을 바꾸거나 식 직접 쓰기의 col("…")로 쓰세요.`); return /\s/.test(s) || MARK.has(s) ? `\`${s}\`` : s; };  // 낱말 줄의 컬럼: 빈칸이 있거나 칸 낱말과 같으면 백틱으로
  const TOKEN = /`([^`]*)`|`|[^\s`]+/g;  // 서버 calc.TOKEN과 같다
  const tokens = line => [...String(line).matchAll(TOKEN)].map(m => { if (m[0] === "`") throw new Error("백틱(`)이 닫히지 않았어요."); return { word: m[1] ?? m[0], quoted: m[1] !== undefined }; });  // 쿼리 화면이 계산 줄의 대상 컬럼을 읽는다(빈 백틱·쉼표 검사는 서버 몫)
  const q = s => { s = String(s).replace(/\\/g, "\\\\"); return !s.includes("'") ? `'${s}'` : !s.includes('"') ? `"${s}"` : `'${s.replace(/'/g, "\\'")}'`; };  // 식 안의 글자
  const val = v => { const s = String(v ?? "").trim(); return s === "" ? "None" : /^(['"]).*\1$/s.test(s) || /^-?\d+(\.\d+)?$/.test(s) ? s : q(s); };  // 숫자 모양이면 수, 따옴표로 감쌌으면 그대로
  const lit = v => v && typeof v === "object" ? ref(v.col) : typeof v === "number" ? String(v) : q(v);  // 함수 인자: {col}은 컬럼, 수는 수, 그 밖은 글자
  const csv = t => String(t ?? "").split(",").map(s => s.trim()).filter(Boolean);
  const OPS = { "=": "==" };

  function lines(f) {
    const X = String(f.name ?? "").trim() || "새컬럼", as = ` 이름 ${X}`, E = body => `식 ${body}${as}`;
    if (!NAME.test(X)) throw new Error("새 컬럼 이름은 글자·숫자·밑줄로, 빈칸 없이 63자까지 써요(숫자로 시작하지 않게).");  // 여러 줄 방식이 반만 들어가지 않게 줄을 만들기 전에 막는다
    const by = (o, dir) => ` 기준 ${bq(need(o, "기준 컬럼"))}${dir === "desc" ? " desc" : ""}`;
    const grp = g => { const v = [].concat(g ?? []).filter(Boolean); return v.length ? ` 그룹 ${v.map(bq).join(", ")}` : ""; };
    const fn = (id, args) => { const a = [...(args ?? [])]; if (a.some(x => x === undefined || x === "")) throw new Error("함수 칸을 채워 주세요."); return E(`${id}(${a.map(lit).join(", ")})`); };
    const c = () => ref(need(f.col, "컬럼"));
    switch (f.how) {
      case "calc": {
        const a = ref(need(f.a, "A")), b = f.b ? ref(f.b) : val(need(f.value, "B"));
        if (f.op === "ratio") return [E(`safe_divide(${a}, ${b})`)];
        if (f.op === "concat") return [E(`concat(${a}, ${q(f.sep ?? "")}, ${b})`)];
        return [E(`${a} ${need(f.op, "계산")} ${b}`)];
      }
      case "row": {
        const cs = [].concat(f.cols ?? []).filter(Boolean), k = need(f.kind, "계산");
        if (cs.length < 2) throw new Error("컬럼을 2개 이상 고르세요.");
        const all = cs.map(ref).join(", ");
        if (k === "weighted") { const w = csv(need(f.weights, "가중치")).map(Number); if (w.length !== cs.length || w.some(x => !Number.isFinite(x))) throw new Error(`가중치는 고른 컬럼 수(${cs.length}개)만큼 수로 써요.`); return [E(cs.map((c, i) => `${w[i]} * ${ref(c)}`).join(" + "))]; }
        if (k === "ratio") return cs.map(c => { const n = `${X}_${c}`; if (!NAME.test(n)) throw new Error(`'${n}'은 새 컬럼 이름으로 못 써요. 이름을 짧게 하거나 컬럼 이름을 바꿔 주세요.`); return `식 safe_divide(${ref(c)}, row_sum(${all})) 이름 ${n}`; });
        if (k === "dominant") return [E(`safe_divide(row_max(${all}), row_sum(${all}))`), `식 row_argmax(${all}) 이름 ${X}_컬럼`];
        if (k === "row_dist" || k === "row_cosine") {
          const consts = csv(f.consts).map(Number), bs = consts.length ? (consts.every(Number.isFinite) ? consts.map(String) : null) : [].concat(f.others ?? []).filter(Boolean).map(ref);
          if (!bs || bs.length !== cs.length) throw new Error(`B는 A와 같은 수(${cs.length}개)의 컬럼이나 기준 값으로 골라요.`);
          return [E(`${k}(${all}, ${bs.join(", ")})`)];
        }
        return [E(`${k}(${all})`)];
      }
      case "flag": {
        const k = need(f.kind, "방식"), col = () => need(f.col, "컬럼"), pre = spec => [`전처리 flags ${col()} ${JSON.stringify(spec)}`];
        const v = s => { const t = String(s ?? "").trim(); return !f.text && /^-?\d+(\.\d+)?$/.test(t) ? +t : t; };
        if (k === "zero") return pre({ zero: true });
        if (k === "cmp") return pre({ cmp: [need(f.op, "비교"), v(need(f.value, "값"))] });
        if (k === "top") { const p = csv(f.tops).map(Number); if (p.some(x => !(x > 0 && x < 100))) throw new Error("상위 %는 0보다 크고 100보다 작은 수로 써요."); return pre({ top: p.length ? p : true }); }
        if (k === "above") {
          const gs = [].concat(f.groups ?? []).filter(Boolean), how = f.stat === "median" ? "median" : "mean";
          if (!gs.length) return pre({ above: how });
          return [`그룹집계 ${how}(${gcol(col())})${grp(gs)} 이름 ${X}_기준`, E(`iif(${c()} > ${ref(`${X}_기준`)}, 1, 0)`)];
        }
        if (k === "between") return pre({ between: [v(need(f.low, "최소")), v(need(f.high, "최대"))] });
        if (k === "isin") return pre({ isin: csv(need(f.values, "값")).map(x => v(x)) });
        if (k === "outlier") { const kk = String(f.k ?? "").trim(); if (kk && !(+kk > 0)) throw new Error("k는 0보다 큰 수예요."); return pre({ outlier: { by: f.by || "iqr", ...(kk ? { k: +kk } : {}) } }); }
        if (k === "combo") {
          const rs = (f.rules ?? []).filter(r => r.col && String(r.value ?? "").trim() !== "");
          if (rs.length < 2) throw new Error("조건 줄을 둘 이상 채워 주세요.");
          const parts = rs.map(r => { const p = `(${ref(r.col)} ${OPS[r.op] ?? r.op} ${r.text ? q(String(r.value).trim()) : val(r.value)})`; return r.not ? `not ${p}` : p; });
          if (f.join === "xor") return [E(`iif((${parts.map(p => `iif(${p}, 1, 0)`).join(" + ")}) % 2 == 1, 1, 0)`)];
          return [E(`iif(${parts.join(f.join === "or" ? " or " : " and ")}, 1, 0)`)];
        }
        const g = grp(f.group);
        if (k === "change") return [`lag ${bq(col())} 1${by(f.order, f.dir)}${g} 이름 ${X}_prev`, E(`iif(isnull(${ref(`${X}_prev`)}), 0, iif(${c()} != ${ref(`${X}_prev`)}, 1, 0))`)];
        if (k === "first" || k === "last") { const dir = k === "last" ? (f.dir === "desc" ? "asc" : "desc") : f.dir; return [`row_number${by(f.order, dir)}${g} 이름 ${X}_n`, E(`iif(${ref(`${X}_n`)} == 1, 1, 0)`)]; }
        throw new Error("고를 수 없는 방식이에요.");
      }
      case "bin": {
        const col = need(f.col, "숫자 컬럼"), copy = E(ref(col)), k = need(f.kind, "방식");
        const opt = { ...(f.closed && f.closed !== "left" ? { closed: f.closed } : {}), ...(f.label && f.label !== "range" ? { label: f.label } : {}) };
        if (k === "custom") {
          const e = csv(need(f.bounds, "경계값")).map(Number);
          if (e.length < 2 || e.some((x, i) => !Number.isFinite(x) || (i && x <= e[i - 1]))) throw new Error("경계값은 작은 것부터 2개 이상의 수로 써요.");
          return [copy, `전처리 bins ${X} ${JSON.stringify(Object.keys(opt).length ? { edges: e, ...opt } : e)}`];
        }
        const spec = { how: k }, n = String(f.n ?? "").trim();
        if (n && !["floor", "log"].includes(k)) { if (!/^\d+$/.test(n) || +n < 2) throw new Error("구간 수는 2 이상의 정수예요."); spec.n = +n; }
        if (k === "floor") { const w = +need(f.width, "단위 폭"); if (!(w > 0)) throw new Error("단위 폭은 0보다 큰 수예요."); spec.width = w; }
        if (k === "tails" && String(f.tail ?? "").trim()) { const p = +f.tail; if (!(p > 0 && p < 50)) throw new Error("양끝 %는 0보다 크고 50보다 작은 수예요."); spec.tail = p; }
        if (k === "best") { spec.target = need(f.target, "타겟 컬럼"); spec.method = f.method || "iv"; }
        return [copy, `전처리 cut ${X} ${JSON.stringify({ ...spec, ...opt })}`];
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
        const T = v => String(v ?? "").trim(), ints = (what, ...vs) => { if (vs.some(v => v !== "" && !/^-?\d+$/.test(v))) throw new Error(`${what}은 정수로 써요.`); };
        if (k === "bucket") { const bn = T(f.bn); ints("간격", bn); return [E(`time_bucket(${c()}${bn ? `, ${bn}` : ""})`)]; }
        if (k === "part") {
          const part = need(f.part, "뽑을 것");
          if (part === "time_of_day" || part === "month_phase") {
            const b = csv(f.bounds), l = csv(f.labels);
            if (l.length && !b.length) throw new Error("구간 이름을 바꾸려면 경계도 써 주세요.");
            if (b.some(x => !/^-?\d+$/.test(x))) throw new Error("경계는 정수로 써 주세요.");
            return [E(`${part}(${c()}${b.length ? `, [${b.join(", ")}]` : ""}${l.length ? `, [${l.map(q).join(", ")}]` : ""})`)];
          }
          if (part === "work_hours") {
            const s = T(f.start), en = T(f.end); ints("시작·끝 시", s, en);
            return [E(`work_hours(${c()}, ${q(f.days || "weekdays")}${s || en ? `, ${s || "None"}, ${en || "None"}` : ""})`)];  // 한쪽만 쓰면 다른 쪽은 None = 설정
          }
          return [E(`${part}(${c()})`)];
        }
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
        if (f.kind === "unnest") return [`펼치기 ${bq(col)}`];
        if (f.kind === "expand") return [`전처리 expand ${col} true`];
        return [`전처리 counts ${col} ${JSON.stringify(f.sep ? { sep: f.sep } : {})}`];
      }
      case "window": {
        const k = need(f.kind, "계산"), g = grp(f.group), o = by(f.order, f.dir);
        if (k === "share_of_group") return [`share_of_group ${bq(need(f.col, "값 컬럼"))}${g}${as}`];
        if (RANKS.includes(k)) return [`${k}${o}${g}${as}`];
        const col = bq(need(f.col, "값 컬럼"));
        if (k === "moving_std") {
          const m = /^(\d+)(행|일)$/.exec(need(f.size, "크기"));
          if (!m) throw new Error("크기는 '7행'·'7일'처럼 써요.");
          return [`moving_std ${col} ${m[1]} ${m[2] === "일" ? "days" : "rows"}${o}${g}${as}`];
        }
        if (k === "ewma") {
          const s = String(f.span ?? "").trim();
          if (s && !Number.isFinite(+s)) throw new Error("N 또는 α는 숫자로 써요.");
          return [`ewma ${col}${s ? " " + s : ""}${o}${g}${as}`];
        }
        if (k === "이동평균" || k === "이동합계") return [`${k} ${col} ${need(f.size, "크기")}${o}${g}${as}`];
        if (k === "lag" || k === "lead") return [`${k} ${col} ${need(f.n, "n")}${o}${g}${as}`];
        return [`${k} ${col}${o}${g}${as}`];
      }
      case "group": {
        const gs = [].concat(f.groups ?? []).filter(Boolean);
        if (gs.length > 3) throw new Error("그룹은 3개까지 고를 수 있어요.");
        const col = !f.combo && f.agg === "count" && !f.col ? "" : need(f.col, "값 컬럼"), g = grp(gs);
        const agg = (id, name, arg, extra = "") => `그룹집계 ${id}(${gcol(col)}${arg !== undefined && arg !== "" ? `, ${arg}` : ""})${g}${extra} 이름 ${name}`;
        if (f.combo === "share") return [agg("sum", `${X}_합`), E(`safe_divide(${ref(col)}, ${ref(`${X}_합`)})`)];
        if (f.combo === "center") return [agg("mean", `${X}_평균`), E(`${ref(col)} - ${ref(`${X}_평균`)}`)];
        if (f.combo === "z") return [agg("mean", `${X}_평균`), agg("std", `${X}_표준편차`), E(`(${ref(col)} - ${ref(`${X}_평균`)}) / ${ref(`${X}_표준편차`)}`)];
        return [agg(need(f.agg, "집계"), X, f.arg, (f.order ? by(f.order, f.dir) : "") + (String(f.where ?? "").trim() ? ` 조건 ${f.where.trim()}` : ""))];
      }
      case "stat": {
        const x = c(), gs = [].concat(f.groups ?? []).filter(Boolean), g = grp(gs), col = need(f.col, "컬럼");
        if (f.kind === "z") return gs.length ? [`그룹집계 mean(${gcol(col)})${g} 이름 ${X}_평균`, `그룹집계 std(${gcol(col)})${g} 이름 ${X}_표준편차`, E(`(${x} - ${ref(`${X}_평균`)}) / ${ref(`${X}_표준편차`)}`)]
          : [E(`(${x} - mean(${x})) / std(${x})`)];
        if (f.kind === "minmax") return gs.length ? [`그룹집계 min(${gcol(col)})${g} 이름 ${X}_최소`, `그룹집계 max(${gcol(col)})${g} 이름 ${X}_최대`, E(`(${x} - ${ref(`${X}_최소`)}) / (${ref(`${X}_최대`)} - ${ref(`${X}_최소`)})`)]
          : [E(`(${x} - min(${x})) / (max(${x}) - min(${x}))`)];
        if (f.kind === "log10") return [E(`log10(${x} + 1)`)];
        if (f.kind === "minmax100") return [E(`(${x} - min(${x})) / (max(${x}) - min(${x})) * 100`)];
        if (f.kind === "robust") return [E(`(${x} - median(${x})) / (quantile(${x}, 0.75) - quantile(${x}, 0.25))`)];
        if (["boxcox", "yeojohnson", "power"].includes(f.kind)) {
          const spec = { how: f.kind };
          if (f.kind === "power") { const p = +need(f.p, "p"); if (!Number.isFinite(p) || p === 0) throw new Error("p는 0이 아닌 수예요."); spec.p = p; }
          else if (String(f.lam ?? "").trim()) { const l = +f.lam; if (!Number.isFinite(l)) throw new Error("λ는 수로 써요."); spec.lambda = l; }
          return [E(x), `전처리 power ${X} ${JSON.stringify(spec)}`];
        }
        if (f.kind === "winsor") { const lo = String(f.p_low ?? "").trim(), hi = String(f.p_high ?? "").trim(); return [E(x), `전처리 outlier ${X} ${JSON.stringify({ how: "clip", by: "pct", ...(lo ? { p_low: +lo } : {}), ...(hi ? { p_high: +hi } : {}) })}`]; }
        if (f.kind === "log") return [E(`ln(${x})`)];
        if (f.kind === "pct") return [`percent_rank 기준 ${bq(col)}${g}${as}`];
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
        if (f.kind === "onehot_top") { const k = need(f.k, "K"); if (!/^\d+$/.test(k) || +k < 1) throw new Error("K는 1 이상의 정수예요."); return [`전처리 onehot ${col} ${JSON.stringify({ top: +k, ...(f.others ? { others: true } : {}) })}`]; }
        if (f.kind.startsWith("others_")) {
          const how = f.kind.slice(7), key = { top: "k", share: "share", pareto: "cum" }[how], raw = String(f[key] ?? "").trim(), spec = { how, label: f.label || "기타" };
          if (raw) { const x = +raw; if (how === "top" ? !(Number.isInteger(x) && x >= 1) : !(x > 0 && x < 100)) throw new Error(how === "top" ? "K는 1 이상의 정수예요." : "%는 0보다 크고 100보다 작은 수예요."); spec[key] = x; }
          return [E(ref(col)), `전처리 others ${X} ${JSON.stringify(spec)}`];
        }
        if (f.kind === "freq_count" || f.kind === "freq_pct") return [`전처리 freq ${col} ${JSON.stringify({ how: f.kind.slice(5) })}`];
        if (f.kind === "target") { const m = String(f.smooth ?? "").trim(); if (m && !(+m >= 0)) throw new Error("평활 m은 0 이상의 수예요."); return [`전처리 target ${col} ${JSON.stringify({ target: need(f.target, "타겟 컬럼"), ...(m ? { smooth: +m } : {}) })}`]; }
        if (f.kind === "ordinal") {
          const pairs = csv(need(f.map, "값=번호")).map(p => p.split("="));
          if (pairs.some(p => p.length !== 2 || !p[0].trim() || p[1].trim() === "" || !Number.isFinite(+p[1]))) throw new Error("값=번호를 쉼표로 이어 써요. 예: 하=1, 중=2, 상=3");
          const e = String(f.else ?? "").trim(); if (e && !Number.isFinite(+e)) throw new Error("목록에 없는 값의 번호는 수로 써요.");
          return [`전처리 ordinal ${col} ${JSON.stringify({ map: Object.fromEntries(pairs.map(([a, b]) => [a.trim(), +b])), ...(e ? { else: +e } : {}) })}`];
        }
        return [`전처리 ${f.kind === "onehot" ? "onehot" : "label"} ${col} true`];
      }
      case "behavior": {
        const u = need(f.user, "사람(그룹) 컬럼"), t = need(f.time, "시각 컬럼"), unit = q(f.unit ?? "minute"), g = ` 그룹 ${bq(u)}`, o = ` 기준 ${bq(t)}`;
        const first = `${X}_first`;
        if (f.kind === "session") return [`session_id ${bq(t)} ${need(f.minutes, "세션을 나눌 분")}${g}${as}`];  // 시각 자신이 순서라 기준은 없다
        if (f.kind === "gap") return [`since_prev ${bq(t)} ${f.unit ?? "minute"}${o}${g}${as}`];
        if (f.kind === "next") return [`until_next ${bq(t)} ${f.unit ?? "minute"}${o}${g}${as}`];
        if (f.kind === "episode") { const vs = csv(need(f.values, "나누는 값")); return [`식 episode_id(${ref(need(f.event, "이벤트 컬럼"))}, ${q(f.at || "start")}, ${vs.map(v => f.text ? q(v) : val(v)).join(", ")})${o}${g}${as}`]; }
        if (f.kind === "streak_days") return [`streak ${bq(t)} ${f.period || "days"}${g}${as}`];
        if (f.kind === "streak_flag") return [`식 ${need(f.cond, "조건 식")} 이름 ${X}_ok`, `streak ${X}_ok flag${o}${g}${as}`];
        if (f.kind === "path") {
          const lim = need(f.limit, "최대 단계 수");
          if (!/^\d+$/.test(lim)) throw new Error("최대 단계 수는 0 이상의 정수예요.");
          return [`event_path ${bq(need(f.event, "이벤트 컬럼"))} ${f.dedupe || "yes"} ${lim}${o}${g}${as}`];
        }
        if (f.kind === "distinct") return [`cum_distinct ${bq(need(f.event, "이벤트 컬럼"))}${o}${g}${as}`];
        if (f.kind === "since_first") return [`first_value ${bq(t)}${o}${g} 이름 ${first}`, E(`date_diff(${ref(first)}, ${ref(t)}, ${unit})`)];
        if (f.kind === "cohort") return [`그룹집계 min(${gcol(t)})${g} 이름 ${first}`, E(`truncate(${ref(first)}, ${q(f.unit ?? "month")})`)];
        if (f.kind === "same_day") return [`식 truncate(${ref(t)}, ${q("day")}) 이름 ${X}_날`, `순위${o}${g}, ${X}_날${as}`];  // 같은 사람·같은 날 안의 시각 순위(같은 시각은 같은 번호)
        if (f.kind === "cum_count") return [`식 iif(isnull(${ref(t)}), 0, 1) 이름 ${X}_1`, `누적합 ${X}_1${o}${g}${as}`];  // 같은 시각의 행은 모두 그 시각까지의 건수, 빈 시각은 세지 않는다
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
  const METHODS = [["calc", "계산·조합"], ["row", "여러 컬럼 압축·거리", ["num"]], ["cond", "조건·분류"], ["flag", "표시(0/1) 플래그"], ["bin", "구간 나누기(자동)", ["num"]], ["date", "날짜·시각", ["date", "str"]], ["text", "글자", ["str"]], ["window", "순서·창"], ["group", "그룹별 집계 붙이기"],
    ["stat", "통계 변환", ["num"]], ["encode", "인코딩·치환"], ["struct", "구조", ["struct", "list", "str"]], ["behavior", "행동 분석 틀", ["date"]], ["geo", "지리", ["num"]], ["join", "다른 테이블"], ["expr", "식 직접"]];
  const KIND_NAMES = { date: "날짜", str: "글자", num: "숫자", struct: "구조", list: "목록" };

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
      row() {
        const kind = pick("계산", [["row_sum", "합"], ["row_mean", "평균"], ["row_std", "표준편차"], ["row_min", "최소"], ["row_max", "최대"], ["row_count", "빈 값 아닌 수"], ["row_positive", "0보다 큰 수"],
          ["row_argmax", "가장 큰 컬럼 이름"], ["row_argmin", "가장 작은 컬럼 이름"], ["weighted", "가중 합(w1×X1 + …)"], ["ratio", "구성비(각 컬럼 ÷ 합)"], ["dominant", "지배 비율(최대 ÷ 합)과 그 컬럼"],
          ["row_dist", "유클리드 거리(A와 B)"], ["row_cosine", "코사인 유사도(A와 B)"]]);
        const checks = () => el("div", { className: "nc-units" }, ...columns().filter(c => PrepMenu.kindOf(c[1]) === "num").map(c => el("label", { className: "check" }, el("input", { type: "checkbox", value: c[0] }), c[0])));
        const a = checks(), b = checks(), consts = text("B 대신 기준 값들(쉼표로, A와 같은 수)"), weights = text("가중치들(쉼표로, A의 컬럼 차례대로)");
        const picked = box => [...box.querySelectorAll("input:checked")].map(i => i.value);
        return { el: el("div", {}, field("계산", kind), field("컬럼(A)", a), field("B", b), field("기준 값", consts), field("가중치", weights),
          el("p", { className: "hint", textContent: "새 컬럼 이름은 구성비면 이름_컬럼, 지배 비율이면 이름과 이름_컬럼이에요." })),
          sync: () => { const d = ["row_dist", "row_cosine"].includes(kind.value); show(b, d); show(consts, d); show(weights, kind.value === "weighted"); },
          read: () => ({ how: "row", kind: kind.value, cols: picked(a), others: picked(b), consts: consts.value, weights: weights.value }) };
      },
      flag() {
        const kind = pick("방식", [["zero", "0이면 1"], ["cmp", "임계치와 비교(X ≥ 값 등)"], ["top", "상위 %에 들면 1"], ["above", "평균·중앙값보다 크면 1(그룹별도)"], ["between", "범위 안이면 1(A ≤ X ≤ B)"], ["isin", "값 목록에 있으면 1"],
          ["outlier", "이상치면 1(IQR·Z·MAD)"], ["combo", "여러 조건 조합(AND·OR·XOR·NOT)"], ["change", "직전 값과 달라지면 1"], ["first", "그룹의 첫 행이면 1"], ["last", "그룹의 마지막 행이면 1"]]);
        const col = colPick(null, "컬럼"), op = pick("비교", [">=", ">", "<=", "<", "==", "!="]), value = text("값"), tops = text(`상위 %들(비우면 ${(P6().flag_top ?? []).join(", ")})`), stat = pick("기준", [["mean", "평균"], ["median", "중앙값"]]);
        const gs = groupPicks(), low = text("최소"), high = text("최대"), values = text("값들, 예: A, B"), by = pick("이상치 기준", [["iqr", "사분위수(IQR)"], ["std", "Z-Score(σ)"], ["mad", "수정 Z-Score(MAD)"]]), kk = text("k (비우면 저장한 진단 기준의 배수)");
        const join = pick("조합", [["and", "모두 참(AND)"], ["or", "하나라도 참(OR)"], ["xor", "홀수 개가 참(XOR)"]]);
        const rules = el("div", { className: "nc-rules" });
        const rule = () => {
          const c = colPick(null, "컬럼"), o = pick("비교", ["=", "!=", ">", ">=", "<", "<="]), v = text("값"), not = el("input", { type: "checkbox" });
          const row = el("div", { className: "row nc-rule" }, el("label", { className: "check" }, not, "NOT"), c, o, v, el("button", { type: "button", className: "ghost", textContent: "×", title: "이 줄 빼기", onclick: () => row.remove() }));
          row.read = () => ({ col: c.value, op: o.value, value: v.value, not: not.checked, text: kindOf(c.value) === "str" });
          return row;
        };
        rules.append(rule(), rule());
        const comboBox = el("div", {}, rules, el("button", { type: "button", className: "btn", textContent: "줄 더하기", onclick: () => rules.append(rule()) }));
        const order = colPick(null, "기준(순서)"), dir = pick("방향", [["asc", "작은 것부터"], ["desc", "큰 것부터"]]), group = colPick(null, "그룹", "그룹 없음");
        return { el: el("div", {}, field("방식", kind), field("컬럼", col), field("비교", op), field("값", value), field("상위 %", tops), field("기준", stat), field("그룹(전체면 비움)", el("span", { className: "row" }, ...gs)), field("최소", low), field("최대", high),
          field("값들", values), field("이상치 기준", by), field("k", kk), field("조합", join), comboBox, field("기준(순서)", order), field("방향", dir), field("그룹", group),
          el("p", { className: "hint", textContent: "한 컬럼 표시(0·임계치·상위 %·범위·목록·이상치)는 새 컬럼 이름을 단계가 정해요(예: 금액_is_zero)." })),
          sync: () => {
            const k = kind.value, seq = ["change", "first", "last"].includes(k);
            show(col, k !== "combo"); show(op, k === "cmp"); show(value, k === "cmp"); show(tops, k === "top"); show(stat, k === "above"); show(gs[0].parentNode, k === "above"); show(low, k === "between"); show(high, k === "between");
            show(values, k === "isin"); show(by, k === "outlier"); show(kk, k === "outlier"); show(join, k === "combo"); show(comboBox, k === "combo"); show(order, seq); show(dir, seq); show(group, seq);
          },
          read: () => ({ how: "flag", kind: kind.value, col: col.value, op: op.value, value: value.value, tops: tops.value, stat: stat.value, groups: gs.map(g => g.value), low: low.value, high: high.value, values: values.value, by: by.value, k: kk.value,
            join: join.value, rules: [...rules.children].map(r => r.read()), order: order.value, dir: dir.value, group: group.value, text: kindOf(col.value) === "str" }) };
      },
      bin() {
        const kind = pick("방식", [["width", "등간격(N개 같은 폭)"], ["quantile", "등빈도(분위)"], ["zero", "0 따로 + 양수 등빈도"], ["log", "로그 스케일(1, 10, 100 …)"], ["floor", "단위로 내림(10대·20대)"],
          ["tails", "양끝 묶고 가운데 등간격"], ["best", "타겟으로 최적 구간(IV·카이제곱)"], ["custom", "경계값 직접"]]);
        const col = colPick(["num"], "숫자 컬럼"), n = text("구간 수(비우면 기본)"), width = text("단위 폭, 예: 10"), tail = text(`양끝 %(비우면 ${P6().cut_tail ?? ""})`), target = colPick(null, "타겟(0/1) 컬럼");
        const method = pick("최적 기준", [["iv", "IV 최대"], ["chi", "카이제곱 병합"]]), bounds = text("경계값, 예: 0, 10, 100");
        const closed = pick("경계 포함", [["left", "[a, b) 왼쪽 포함"], ["right", "(a, b] 오른쪽 포함"]]), label = pick("구간 표시", [["range", "구간 글자"], ["mid", "구간 가운데 값"], ["index", "구간 번호"]]);
        return { el: el("div", {}, field("방식", kind), field("컬럼", col), field("구간 수", n), field("단위 폭", width), field("양끝 %", tail), field("타겟", target), field("최적 기준", method), field("경계값", bounds), field("경계 포함", closed), field("구간 표시", label)),
          sync: () => { const k = kind.value; show(n, !["floor", "log", "custom"].includes(k)); show(width, k === "floor"); show(tail, k === "tails"); show(target, k === "best"); show(method, k === "best"); show(bounds, k === "custom"); },
          read: () => ({ how: "bin", kind: kind.value, col: col.value, n: n.value, width: width.value, tail: tail.value, target: target.value, method: method.value, bounds: bounds.value, closed: closed.value, label: label.value }) };
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
        const kind = pick("방식", [["part", "날짜에서 뽑기"], ["diff", "두 날짜의 차이"], ["since", "지금까지 지난 시간"], ["add", "날짜 더하기"], ["trunc", "단위로 맞추기(월초 등)"], ["tz", "시간대 바꾸기"], ["parse", "글자를 날짜로"], ["bucket", "N분 간격으로 묶기"]]);
        const col = colPick(["date"], "날짜 컬럼"), scol = colPick(["str"], "글자 컬럼"), a = colPick(["date"], "시작 날짜"), b = colPick(["date"], "끝 날짜");
        const part = pick("뽑을 것", [["year", "연"], ["month", "월"], ["day", "일"], ["weekday", "요일"], ["hour", "시"],
          ["weekday_num", "요일 번호(1~7)"], ["weekday_name", "요일 이름"], ["is_weekend", "주말인지"], ["time_of_day", "시간대"], ["work_hours", "업무시간인지"], ["month_phase", "월초·월중·월말"], ["is_month_end", "그 달 마지막 날인지"]]);
        const style = pick("보이는 꼴", [["lines", "① 단위마다 컬럼"], ["text", "② 한 칸 글자(1년 2개월)"], ["one", "③ 한 단위 숫자"]]);
        const units = el("div", { className: "nc-units" }, ...UNITS.map(u => el("label", { className: "check" }, el("input", { type: "checkbox", value: u, checked: u === "day" }), KO[u])));
        const unit = unitPick("단위", "day"), ref_ = pick("기준", [["now", "지금 시각"], ["today", "오늘 날짜"]]), n = text("더할 수(음수면 빼기)"), zone = text("시간대, 예: Asia/Seoul", "Asia/Seoul"), format = text("형식, 예: %Y-%m-%d", "%Y-%m-%d");
        const dflt = (id, name) => [].concat(funcs?.get?.(id)?.args?.find(a => a.name === name)?.default ?? []).join(", ");  // 기본값은 함수 사전에서
        const bounds = text("경계(쉼표로)"), labels = text("구간 이름(쉼표로, 비우면 기본)"), start = text(`시작 시(비우면 설정${CAL().work_hours ? `, 기본 ${CAL().work_hours[0]}` : ""})`), end = text(`끝 시(비우면 설정${CAL().work_hours ? `, 기본 ${CAL().work_hours[1]}` : ""})`);
        const days = pick("요일", [["weekdays", "평일만"], ["all", "모든 요일"]]), bn = text(`간격(분, 비우면 설정${CAL().bucket_minutes ? `, 기본 ${CAL().bucket_minutes}` : ""})`);
        return { el: el("div", {}, field("방식", kind), field("날짜 컬럼", col), field("글자 컬럼", scol), field("시작 날짜", a), field("끝 날짜", b), field("뽑을 것", part), field("경계", bounds), field("구간 이름", labels), field("시작 시", start), field("끝 시", end), field("요일", days), field("보이는 꼴", style), field("단위들", units), field("단위", unit), field("기준", ref_), field("더할 수", n), field("간격", bn), field("시간대", zone), field("형식", format)),
          sync: () => {
            const k = kind.value, diff = k === "diff", p = k === "part" ? part.value : "";
            bounds.placeholder = `경계(쉼표로${dflt(p, "bounds") ? `, 비우면 ${dflt(p, "bounds")}` : ""})`;
            show(bounds, ["time_of_day", "month_phase"].includes(p)); show(labels, ["time_of_day", "month_phase"].includes(p)); show(start, p === "work_hours"); show(end, p === "work_hours"); show(days, p === "work_hours");
            show(bn, k === "bucket");
            show(col, ["part", "since", "add", "trunc", "tz", "bucket"].includes(k)); show(scol, k === "parse"); show(a, diff); show(b, diff); show(part, k === "part"); show(style, diff);
            show(units, diff && style.value !== "one"); show(unit, ["since", "add", "trunc"].includes(k) || (diff && style.value === "one")); show(ref_, k === "since"); show(n, k === "add"); show(zone, k === "tz"); show(format, k === "parse");
          },
          read: () => ({ how: "date", kind: kind.value, col: kind.value === "parse" ? scol.value : col.value, a: a.value, b: b.value, part: part.value, style: style.value,
            units: [...units.querySelectorAll("input:checked")].map(i => i.value), unit: unit.value, ref: ref_.value, n: n.value, zone: zone.value, format: format.value,
            bounds: bounds.value, labels: labels.value, start: start.value, end: end.value, days: days.value, bn: bn.value }) };
      },
      text: () => funcForm("text", TEXT_FUNCS),
      geo: () => funcForm("geo", GEO_FUNCS),
      window() {
        const kind = pick("계산", [["순위", "순위"], ["row_number", "행 번호"], ["dense_rank", "빈틈없는 순위"], ["percent_rank", "백분위 순위"], ["이동평균", "이동 평균"], ["이동합계", "이동 합계"], ["moving_std", "이동 표준편차"],
          ["누적합", "누적 합"], ["ewma", "지수 가중 이동 평균"],["이전값", "이전 값"], ["차이", "이전 값과의 차이"], ["lag", "n행 앞 값"], ["lead", "n행 뒤 값"], ["share_of_group", "그룹 안 비중"]]);
        const col = colPick(null, "값 컬럼"), size = text("크기", "7"), sizeUnit = pick("크기 단위", ["행", "일"]), n = text("n", "1"), order = colPick(null, "기준(순서)"), dir = pick("방향", [["asc", "작은 것부터"], ["desc", "큰 것부터"]]), group = colPick(null, "그룹", "그룹 없음");
        const span = text(`N 또는 α(비우면 설정 N${CAL().ewma_span ? `, 기본 ${CAL().ewma_span}` : ""})`);
        return { el: el("div", {}, field("계산", kind), field("값 컬럼", col), field("크기", el("span", { className: "row" }, size, sizeUnit)), field("N 또는 α", span), field("n", n), field("기준", order), field("방향", dir), field("그룹", group)),
          sync: () => { const k = kind.value; show(col, !RANKS.includes(k)); show(size.parentNode, ["이동평균", "이동합계", "moving_std"].includes(k)); show(span, k === "ewma"); show(n, k === "lag" || k === "lead"); show(order, k !== "share_of_group"); show(dir, k !== "share_of_group"); },
          read: () => ({ how: "window", kind: kind.value, col: col.value, size: size.value.trim() + sizeUnit.value, span: span.value, n: n.value, order: order.value, dir: dir.value, group: group.value }) };
      },
      group() {
        const aggs = (funcs?.cat?.funcs ?? []).filter(f => f.aggregate).map(f => [f.id, f.label]);
        const agg = pick("집계", aggs), col = colPick(null, "값 컬럼", "값 컬럼 없음(개수)"), arg = text("인자(예: 분위수 0.9)");
        const gs = groupPicks(), order = colPick(null, "기준(순서)", "기준 없음"), dir = pick("방향", [["asc", "작은 것부터"], ["desc", "큰 것부터"]]), where = text("조건(예: 금액 > 0)");
        const combo = pick("붙일 값", [["", "집계 값 그대로"], ["share", "값 ÷ 그룹 합계(비중)"], ["z", "그룹 안 Z점수"], ["center", "값 − 그룹 평균(편차)"]]);
        return { el: el("div", {}, field("붙일 값", combo), field("집계", agg), field("값 컬럼", col), field("인자", arg), field("그룹", el("span", { className: "row" }, ...gs)), field("기준", order), field("방향", dir), field("조건", where)),
          sync: () => { const plain = !combo.value; show(agg, plain); show(arg, plain); show(order, plain); show(dir, plain && !!order.value); show(where, plain); },
          read: () => ({ how: "group", combo: combo.value, agg: agg.value, col: col.value, arg: arg.value.trim(), groups: gs.map(g => g.value), order: order.value, dir: dir.value, where: where.value }) };
      },
      stat() {
        const kind = pick("변환", [["z", "Z점수"], ["minmax", "0~1로(min-max)"], ["log", "로그 ln"], ["pct", "백분위(percent_rank)"], ["outlier", "이상치 표시"],
          ["log10", "로그 log10(x+1)"], ["minmax100", "0~100으로(min-max)"], ["robust", "로버스트((x−중앙값)/IQR)"], ["boxcox", "Box-Cox"], ["yeojohnson", "Yeo-Johnson"], ["power", "거듭제곱 x^p"], ["winsor", "백분위로 자르기(윈저라이징)"]]);
        const col = colPick(["num"], "숫자 컬럼"), gs = groupPicks(), method = pick("이상치 기준", [["std", "평균에서 표준편차 k배 밖"], ["iqr", "사분위수 범위(IQR) k배 밖"]]), k = text("k (비우면 3 또는 1.5)");
        const lam = text("λ (비우면 자동)"), p = text("p"), plow = text(`하위 %(비우면 ${P6().outlier_pct?.[0] ?? ""})`), phigh = text(`상위 %(비우면 ${P6().outlier_pct?.[1] ?? ""})`);
        return { el: el("div", {}, field("변환", kind), field("컬럼", col), field("그룹(전체면 비움)", el("span", { className: "row" }, ...gs)), field("이상치 기준", method), field("k", k), field("λ", lam), field("p", p), field("하위 %", plow), field("상위 %", phigh)),
          sync: () => { const v = kind.value; show(gs[0].parentNode, ["z", "minmax", "pct"].includes(v) && (groupable || v === "pct")); show(method, v === "outlier"); show(k, v === "outlier"); show(lam, ["boxcox", "yeojohnson"].includes(v)); show(p, v === "power"); show(plow, v === "winsor"); show(phigh, v === "winsor"); },
          read: () => ({ how: "stat", kind: kind.value, col: col.value, groups: gs.map(g => g.value), method: method.value, k: k.value, lam: lam.value, p: p.value, p_low: plow.value, p_high: phigh.value }) };
      },
      encode() {
        const kind = pick("방식", [["label", "값을 번호로(라벨 인코딩)"], ["onehot", "값마다 컬럼으로(원-핫)"], ["rare", "드문 값 묶기"], ["replace", "값 바꿔 쓰기"],
          ["onehot_top", "많이 나온 K개만 컬럼으로(원-핫 Top-K)"], ["others_top", "많이 나온 K개만 남기고 묶기"], ["others_share", "비중 X% 미만 묶기"], ["others_pareto", "누적 비중 X%까지만 남기기"],
          ["freq_count", "등장 횟수로(빈도)"], ["freq_pct", "등장 비율(%)로"], ["target", "타겟 평균으로(타겟 인코딩)"], ["ordinal", "정한 순서의 번호로(순서형)"]]);
        const col = colPick(null, "컬럼"), min = text("이보다 적게 나오면 묶기(2 이상)"), label = text("묶은 값 이름", "기타"), said = el("span", { className: "hint" });
        const kk = text("K"), share = text(`X %(비우면 ${P6().others?.share ?? ""})`), cum = text(`누적 %(비우면 ${P6().others?.cum ?? ""})`), target = colPick(null, "타겟 컬럼"), smooth = text("평활 m(비우면 0)");
        const map = text("값=번호, 예: 하=1, 중=2, 상=3"), els = text("목록에 없는 값의 번호(비우면 빈 값)"), others = el("input", { type: "checkbox" }), leak = el("p", { className: "hint", textContent: P6().target_note ?? "" });
        let spec = null;
        const open_ = el("button", { type: "button", className: "btn", textContent: "바꿀 값 정하기…", onclick: () => PrepMenu.replaceBox({ column: col.value, type: kindOf(col.value), values: q => values?.(col.value, q), onApply: s => { spec = s; said.textContent = `${s.pairs.length}가지를 정했어요.`; } }) });
        return { el: el("div", {}, field("방식", kind), field("컬럼", col), field("최소 횟수", min), field("묶은 값 이름", label), el("div", { className: "row nc-replace" }, open_, said),
          field("K", kk), field("X %", share), field("누적 %", cum), field("타겟", target), field("평활 m", smooth), field("값=번호", map), field("목록에 없는 값", els), field("나머지 값은 기타 컬럼으로", others), leak,
          el("p", { className: "hint", textContent: "새 컬럼 이름은 단계가 정해요(예: 컬럼_label)." })),
          sync: () => {
            const k = kind.value;
            show(min, k === "rare"); show(label, k === "rare" || k.startsWith("others_")); show(open_.parentNode, k === "replace");
            show(kk, k === "onehot_top" || k === "others_top"); show(share, k === "others_share"); show(cum, k === "others_pareto"); show(target, k === "target"); show(smooth, k === "target");
            show(map, k === "ordinal"); show(els, k === "ordinal"); show(others, k === "onehot_top"); show(leak, k === "target");
          },
          read: () => ({ how: "encode", kind: kind.value, col: col.value, min: min.value, label: label.value, spec, k: kk.value, share: share.value, cum: cum.value, target: target.value, smooth: smooth.value, map: map.value, else: els.value, others: others.checked }) };
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
        const kind = pick("틀", [["session", "세션 번호"], ["gap", "직전 행동과의 간격"], ["next", "다음 행동까지 시간"], ["since_first", "첫 행동 이후 경과"], ["cohort", "코호트(첫 날짜의 기간)"], ["nth", "몇 번째 행동"], ["revisit", "재방문인지"],
          ["episode", "이벤트 기준 에피소드"], ["streak_days", "연속 기간(일·주·월)"], ["streak_flag", "조건이 이어진 횟수"], ["path", "경로 문자열"], ["distinct", "누적 고유 개수"], ["same_day", "같은 날 몇 번째"], ["cum_count", "누적 건수"]]);
        const dflt = (id, name) => funcs?.get?.(id)?.args?.find(a => a.name === name)?.default;  // 기본값은 함수 사전에서
        const user = colPick(null, "사람(그룹) 컬럼"), time = colPick(["date"], "시각 컬럼"), minutes = text("이 분보다 떨어지면 새 세션", dflt("session_id", "gap") ?? ""), unit = unitPick("단위", "minute");
        const event = colPick(null, "이벤트(값) 컬럼"), values = text("나누는 값, 예: 로그인, 결제완료", [].concat(dflt("episode_id", "values") ?? []).join(", ")), cond = text("조건 식, 예: 결과 == '승'"), limit = text("최대 단계 수(0이면 모두)", "0");
        const at = pick("나누는 때", [["start", "그 값의 행부터 새 번호"], ["after", "그 값의 다음 행부터 새 번호"]]), dedupe = pick("연속 중복", [["yes", "빼기"], ["no", "두기"]]), period = pick("연속 단위", [["days", "일"], ["weeks", "주"], ["months", "월"]]);
        return { el: el("div", {}, field("틀", kind), field("사람", user), field("시각", time), field("이벤트", event), field("세션 간격(분)", minutes), field("단위", unit), field("나누는 값", values), field("나누는 때", at), field("조건", cond), field("연속 중복", dedupe), field("최대 단계 수", limit), field("연속 단위", period)),
          sync: () => {
            const k = kind.value;
            show(minutes, k === "session"); show(unit, ["gap", "next", "since_first", "cohort"].includes(k)); show(event, ["episode", "path", "distinct"].includes(k)); show(values, k === "episode"); show(at, k === "episode");
            show(cond, k === "streak_flag"); show(dedupe, k === "path"); show(limit, k === "path"); show(period, k === "streak_days");
          },
          read: () => ({ how: "behavior", kind: kind.value, user: user.value, time: time.value, minutes: minutes.value, unit: unit.value, event: event.value, values: values.value, at: at.value, cond: cond.value, dedupe: dedupe.value, limit: limit.value, period: period.value, text: kindOf(event.value) === "str" }) };
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
  return { open, lines, gcol, bq, tokens, METHODS };  // gcol: 쿼리 화면 찾기(openLookup)의 그룹집계 값 컬럼도 같은 규칙
})();
