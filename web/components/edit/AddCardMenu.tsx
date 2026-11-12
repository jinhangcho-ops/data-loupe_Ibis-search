"use client";
// N5 — 카드 추가 메뉴(옛 dash_menu.js initCardMenu): 16종 카드, 테이블(api.tables)·값 줄(api.columns)·날짜 컬럼·목표(api.getTargets)·차트 종류(상수 dash.types)·단위 칸(view.unit_text).
// 고른 것으로 카드(Partial<Card>, layout은 calc.nextLayout)를 만들어 onAdd에 넘긴다. 부른 쪽(Toolbar)이 api.addCard 뒤 대시보드에 넣는다.
// fill을 주면 빈 틀 채우기(fillCard): 같은 메뉴를 그 카드의 종류로 열고, 자리·id·이름은 그대로 두는 조각(kind·view·source·data·colors)만 onAdd에 넘긴다.
// 칸 모양은 menu/build.ts(buildCard, 순수)가 카드 JSON으로 바꾼다. Esc로 닫고 닫으면 열기 전 초점으로 돌아간다.
import { useEffect, useRef, useState } from "react";
import { nextLayout, parseLinkLine, parseRuleLine, parseStateLine } from "@/lib/calc";
import { columns as fetchColumns, getTargets, hashed as fetchHashed, myMetrics, setTargets, tables as fetchTables, values as fetchValues } from "@/lib/api";
import { useConstants } from "@/lib/constants";
import { withoutHashed } from "@/lib/hashed";
import { useDash } from "@/lib/store";
import type { Card, CardKind, CardView, Constants, Targets } from "@/lib/types";
import { Block, Check, LineBox, Sel, Txt } from "./menu/Fields";
import { CloudFields } from "./panel/Tools";
import { FUNNEL_TEMPLATES, MAX_STAGES, REVISIT_CHOICES, buildCard, condLines, emptyForm, formFromCard, templateStages, type Form, type StageRow } from "./menu/build";
import { CARD_KINDS, DATE_X, WATCH, WITH_DATE, colsOfType, kindGroups, parseBlankLine, unitForDate } from "./menu/kinds";

export type AddCardMenuProps = { open: boolean; onClose: () => void; onAdd: (card: Partial<Card>) => void; fill?: Card };

const FORMATS: [string, string][] = [["number", "숫자"], ["percent", "% (0~1 값)"], ["currency", "금액"]];
const NUM = ["정수", "실수"], DATE = ["날짜", "날짜시간"];
const BUCKET_TIMED = "hour", FUNNEL_TIMED = "minute";  // 날짜시간 컬럼일 때 편차 표 시간대 묶음·퍼널/생키 기간 제한의 기본 단위(날짜만 있는 컬럼은 일)
const REVISIT_CUSTOM = "custom", REVISIT_CUSTOM_START = "0";  // 재방문 목록의 "직접 입력" 값과, 고르면 입력 칸에 먼저 들어가는 값(0은 stagesOf가 거부해 직접 적게 한다)
/** P14 새 종류의 body.x 칸 이름(boxplot은 비우면 전체 한 상자) */
const X_LABEL: Partial<Record<CardKind, string>> = { boxplot: "그룹 컬럼", heatmap: "행 컬럼", treemap: "윗단계 컬럼", sunburst: "윗단계 컬럼", wordcloud: "글자 컬럼", bump: "대상 컬럼" };

/** 닫혀 있으면 아무것도 그리지 않는다. 열 때마다 새로 마운트되어 처음 모양으로 시작한다 */
export function AddCardMenu(props: AddCardMenuProps) {
  const consts = useConstants();
  return props.open && consts ? <Menu {...props} consts={consts} /> : null;
}

function Menu({ onClose, onAdd, fill, consts }: AddCardMenuProps & { consts: Constants }) {
  const { state } = useDash();
  const aggs = consts.dash.aggs, firstAgg = aggs.includes("sum") ? "sum" : aggs[0];
  // 종류의 최소~최대에 값 줄 수를 맞춘다
  const fit = (rows: Form["rows"], kind: CardKind) => {
    const [, low, high] = CARD_KINDS[kind]!, out = rows.slice(0, high);
    while (out.length < low) out.push({ label: "", y: "", agg: firstAgg });
    return out;
  };
  const [f, setF] = useState<Form>(() => {
    const kind: CardKind = fill ? (fill.kind === "analysis" || fill.kind === "metric" ? "kpi" : fill.kind) : "kpi";
    return { ...emptyForm(kind, fill?.view?.type ?? ""), ...(fill ? formFromCard(fill) : {}), table: fill?.source?.table ?? "", rows: fit([], kind) };  // 다시 정할 때는 카드의 모양 설정을 지킨다
  });
  const [names, setNames] = useState<string[]>([]);       // 연결의 테이블 목록
  const [colsFor, setColsFor] = useState<{ table: string; cols: [string, string][] }>({ table: "", cols: [] });  // 고른 테이블의 [컬럼, 한글 타입]
  const [gColsFor, setGColsFor] = useState<{ table: string; cols: [string, string][] }>({ table: "", cols: [] });  // 영역 테이블의 컬럼(이름·도형 후보)
  const [hFor, setHFor] = useState<{ table: string; on: boolean; columns: string[] }>({ table: "", on: false, columns: [] });  // 고른 테이블의 해시·UUID 컬럼(/api/diag/hashed)
  const [rs, setRs] = useState<{ table: string; list: string[] }>(() => ({ table: f.table, list: [f.group, f.split, f.entity, f.timeCol, f.kindCol, f.linkCol, f.cX, f.fUser, f.fStep].filter(Boolean) }));  // 다시 넣은 컬럼(저장하지 않음). 고쳐 여는 카드가 이미 쓰는 이름은 처음부터 넣는다
  const [metrics, setMetrics] = useState<string[]>([]);
  const [targets, setT] = useState<Targets | null>(null);
  const [path, setPath] = useState("");
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [tpl, setTpl] = useState("");  // 고른 틀(FUNNEL_TEMPLATES 키, ""는 직접)
  const [vals, setVals] = useState<string[]>([]);  // 자동 흐름의 값 목록(저장하지 않음)
  const [more, setMore] = useState(false);
  const [loadingVals, setLoadingVals] = useState(false);
  const [cloud, setCloud] = useState<NonNullable<CardView["cloud"]>>(() => fill?.view?.cloud ?? { mode: "values" });  // 워드클라우드 방식(view.cloud)
  const root = useRef<HTMLDivElement>(null);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF(p => ({ ...p, [k]: v }));
  const aggOpts: [string, string][] = aggs.map(a => [a, consts.dash.agg_names?.[a] ?? a]);
  const cols = colsFor.table === f.table ? colsFor.cols : [], gCols = gColsFor.table === f.gTable ? gColsFor.cols : [];  // 늦게 온 앞 테이블의 컬럼은 버린다

  // 열 때: 테이블 목록·지표 이름·목표 파일을 받고, 열기 전 초점을 기억했다가 닫으면 돌려준다
  useEffect(() => {
    const back = document.activeElement as HTMLElement | null;
    fetchTables().then(r => setNames(r.tables)).catch(e => setErr(e.message));
    myMetrics().then(r => setMetrics(r.legacy.map(m => m.name))).catch(() => setMetrics([]));  // KPI의 지표는 지표 파일의 이름(서버 metric_scalar가 metric_file에서 찾는다, 옛 /api/metrics)
    getTargets().then(t => { setT(t); setPath(t.path); }).catch(e => setNote(e.message));
    root.current?.querySelector<HTMLElement>("select, input")?.focus();
    return () => back?.focus?.();
  }, []);

  // 테이블을 고르면 그 컬럼을 받아 날짜 컬럼이 하나뿐일 때 자동으로 채운다
  useEffect(() => {
    if (!f.table) return;
    let live = true;
    fetchColumns(f.table).then(r => {
      if (!live) return;
      setColsFor({ table: f.table, cols: r.columns });
      setErr("");
      const dates = colsOfType(r.columns, DATE), one = dates.length === 1 ? dates[0] : "";
      const has = (c: string, list: string[]) => !!c && list.includes(c), names = r.columns.map(c => c[0]);  // 새 테이블에 있는 컬럼이면 그대로(다시 정할 때 되돌린 값)
      setF(p => ({ ...p, rows: fit([], p.kind), group: has(p.group, names) ? p.group : "", date: has(p.date, dates) ? p.date : one, fTime: has(p.fTime, dates) ? p.fTime : one, timeCol: has(p.timeCol, names) ? p.timeCol : one }));
    }).catch(e => live && setErr(e.message));
    fetchHashed(f.table).then(r => live && setHFor({ table: f.table, ...r })).catch(e => live && setErr(e.message));
    return () => { live = false; };
  }, [f.table]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!f.gTable) return;
    let live = true;
    fetchColumns(f.gTable).then(r => {
      if (!live) return;
      setGColsFor({ table: f.gTable, cols: r.columns });
      const text = r.columns.filter(c => c[1] === "문자").map(c => c[0]);
      setF(p => ({ ...p, gName: p.gName || (text.find(n => /name|nm|명|이름/i.test(n)) ?? text.find(n => n !== "geometry" && n !== "geom_type") ?? ""), gGeom: r.columns.some(c => c[0] === "geometry") ? "geometry" : p.gGeom }));
    }).catch(e => live && setErr(e.message));
    return () => { live = false; };
  }, [f.gTable]);

  const [, low, high] = CARD_KINDS[f.kind]!;
  const h = hFor.table === f.table ? hFor : { on: false, columns: [] }, restored = rs.table === f.table ? rs.list : [];
  const { names: all, hidden } = withoutHashed(cols.map(c => c[0]), h.columns, h.on, restored);
  const dates = colsOfType(cols, DATE), nums = colsOfType(cols, NUM);
  const is = (...k: CardKind[]) => k.includes(f.kind);
  const firstDates = useRef([f.date, f.fTime]);  // 처음 날짜 칸(다시 정하기로 연 카드의 저장된 단위를 지킨다)
  // 날짜 칸을 바꾸면 날짜만 있는 컬럼은 일, 시각이 있으면 기본 단위로 맞춘다
  useEffect(() => {
    if (colsFor.table !== f.table || (firstDates.current[0] === f.date && firstDates.current[1] === f.fTime)) return;
    setF(p => ({ ...p, bucket: is("deviation") ? unitForDate(cols, p.date, BUCKET_TIMED) : p.bucket, fUnit: is("funnel", "sankey") ? unitForDate(cols, p.fTime, FUNNEL_TIMED) : p.fUnit }));
  }, [f.date, f.fTime, colsFor]);  // eslint-disable-line react-hooks/exhaustive-deps
  const setRow = (i: number, patch: Partial<Form["rows"][number]>) => setF(p => ({ ...p, rows: p.rows.map((r, j) => (j === i ? { ...r, ...patch } : r)) }));
  const close = () => { onClose(); };
  const go = async () => {
    setBusy(true);
    try {
      const card = buildCard({ ...f, title: f.title || (fill?.title ?? "") }, aggs, a => a !== "count", consts.agg_names, consts.dash.max_bands, consts.dash.max_bins);
      if (f.kind === "wordcloud" && cloud.mode !== "values") card.view = { ...card.view, cloud } as CardView;  // 컬럼 값 방식은 키 없음(기본)
      if (fill) { delete card.title; if (f.title.trim() || !fill.title) card.title = f.title.trim() || card.title; }
      else card.layout = nextLayout((state.dash?.cards ?? []).map(c => ({ id: c.id, layout: c.layout })));
      setErr("");
      onAdd(card);
      close();
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };
  const saveTargets = async () => {
    try { const t = await setTargets(path.trim()); setT(t); setNote(t.error || `목표 ${t.rows.length}개를 읽었어요`); } catch (e) { setNote((e as Error).message); }
  };
  const setStage = (i: number, patch: Partial<StageRow>) => set("fStages", f.fStages.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const moveStage = (i: number, d: number) => { const out = [...f.fStages]; [out[i], out[i + d]] = [out[i + d]!, out[i]!]; set("fStages", out); };
  const shownVals = [...new Set([...vals, ...f.fStarts, ...f.fMiddles, ...f.fEnds])];
  const setRole = (v: string, role: string) => setF(p => {
    const not = (l: string[]) => l.filter(x => x !== v);
    return { ...p, fStarts: role === "start" ? [...not(p.fStarts), v] : not(p.fStarts), fMiddles: role === "middle" ? [...not(p.fMiddles), v] : not(p.fMiddles), fEnds: role === "end" ? [...not(p.fEnds), v] : not(p.fEnds) };
  });
  const loadValues = async () => {
    setLoadingVals(true);
    try {
      const r = await fetchValues({ schema: "", base: f.table, conditions: condLines(f.fConds) }, f.fStep);
      setVals(r.values); setMore(r.more); setErr("");
    } catch (e) { setErr((e as Error).message); }
    setLoadingVals(false);
  };
  const targetNames = [...new Set((targets?.rows ?? []).map(r => String(r.name)))];
  // 글 속 단어는 뽑을 행·(고른 태그의 행이면) 맞춤을 기본값 없이 고르게 한다: 고르기 전에는 [카드 추가]를 잠근다
  const cloudWhy = is("wordcloud") && cloud.mode === "words"
    ? (!cloud.rows ? "단어를 뽑을 행을 고르세요" : cloud.rows === "tagged" && !cloud.match ? "하나라도·모두 중 하나를 고르세요" : "") : "";

  return (
    <div id={fill ? undefined : "d-add"} className={fill ? "pop cardmenu" : "d-add cardmenu"} ref={root} role="dialog" aria-label={fill ? "카드 데이터 정하기" : "카드 추가"}
      onKeyDown={e => { if (e.key === "Escape") { e.stopPropagation(); close(); } }}>
      {!fill && <div className="phead"><b>카드 추가</b><button type="button" className="ghost" aria-label="카드 추가 닫기" title="닫기" style={{ minWidth: 24, minHeight: 24 }} onClick={close}>×</button></div>}
      <div className="d-add-body">
      <Sel label="테이블" title="카드가 읽을 테이블. 먼저 데이터에 연결해야 해요" value={f.table} onChange={v => set("table", v)} options={names} blank="(테이블을 고르세요)" need />
      {hidden.length > 0 && <div className="hint">ID·해시로 보이는 컬럼 {hidden.join(", ")}을(를) 목록에서 뺐어요 <button type="button" className="linkish" onClick={() => setRs(p => ({ table: f.table, list: [...(p.table === f.table ? p.list : []), ...hidden] }))}>다시 넣기</button></div>}
      <div className="hint cm-kinds" role="group" aria-label="카드 종류">카드 종류
        {kindGroups().map(([group, kinds]) => (
          <div key={group} className="cm-kind-group"><span className="cm-kind-name">{group}</span>
            {kinds.map(k => (
              <button key={k} type="button" className={"btype" + (f.kind === k ? " on" : "")} aria-pressed={f.kind === k} title={CARD_KINDS[k]![3]}
                onClick={() => { setErr(""); setF(p => ({ ...p, kind: k, rows: fit(p.rows, k) })); }}>{CARD_KINDS[k]![0]}</button>
            ))}
          </div>
        ))}
      </div>
      {high > 0 && (
        <div className="cm-rows" role="group" aria-label="값">
          {f.rows.map((r, i) => (
            <div key={i} className="cm-row">
              <input className="cm-label" aria-label="이름(칸 제목)" placeholder="이름(칸 제목)" value={r.label} onChange={e => setRow(i, { label: e.target.value })} />
              <select aria-label="컬럼" aria-required={is("histogram", "boxplot", "candle") && i === 0} value={r.y} onChange={e => setRow(i, { y: e.target.value })}>
                <option value="">(컬럼 없음: 개수)</option>
                {all.map(n => <option key={n} value={n}>{n}</option>)}
              </select>
              <select aria-label="집계" value={r.agg} onChange={e => setRow(i, { agg: e.target.value })}>
                {aggOpts.map(([v, n]) => <option key={v} value={v}>{n}</option>)}
              </select>
              {f.rows.length > low && <button type="button" className="ghost" title="이 줄 빼기" aria-label="이 줄 빼기" style={{ minWidth: 24, minHeight: 24 }} onClick={() => setF(p => ({ ...p, rows: p.rows.filter((_, j) => j !== i) }))}>×</button>}
            </div>
          ))}
        </div>
      )}
      {high > 0 && f.rows.length < high && <button type="button" className="linkish" style={{ minHeight: 24 }} onClick={() => setF(p => ({ ...p, rows: [...p.rows, { label: "", y: "", agg: firstAgg }] }))}>값 추가</button>}

      {is("kpi", "donut_kpi", "summary", "dual_trend", "stripes", "blocks", "target_trend", "gauge", "table", "deviation", "bullet", "calheat") && <Sel label="표시 형식" value={f.format} onChange={v => set("format", v)} options={FORMATS} />}
      {is("kpi", "donut_kpi", "stripes", "bullet", "calheat") && <Txt label="단위" title="값 뒤에 붙일 글자. 사람 수는 명, 횟수는 회처럼 적어요. 돈이면 표시 형식 금액을 고르거나 원을 적어요" placeholder="단위 (예: 명, 회, 원)" value={f.unitText} onChange={v => set("unitText", v.slice(0, 10))} width={130} />}
      {is("kpi") && <Sel label="지표" title="지표 하나를 값으로 써요. 비율 지표는 분자와 분모도 작게 보여요" value={f.metric} onChange={v => set("metric", v)} options={metrics} blank="(쓰지 않음)" />}
      {is("gauge", "summary") && <Sel label="나눌 컬럼" title="게이지는 이 컬럼의 값마다 한 조각으로 나눠요. 요약 문장은 변화에 가장 크게 기여한 값을 찾을 때 써요" value={f.group} onChange={v => set("group", v)} options={all} blank="(없음)" need={is("gauge")} />}
      {X_LABEL[f.kind] && <Sel label={X_LABEL[f.kind]!} value={f.group} onChange={v => set("group", v)} options={all} blank={is("boxplot") ? "(없음: 전체)" : "(고르세요)"} need={!is("boxplot")} />}
      {is("heatmap", "treemap", "sunburst") && <Sel label={is("heatmap") ? "열 컬럼" : "아랫단계 컬럼"} value={f.split} onChange={v => set("split", v)} options={all} blank={is("heatmap") ? "(고르세요)" : "(없음)"} need={is("heatmap")} />}
      {is("heatmap") && <div className="hint cm-note">{`행 값 수 × 열 값 수가 ${consts.dash.max_cells.toLocaleString()}칸을 넘으면 그릴 수 없어요. 지역·요일처럼 고유값이 적은 컬럼을 고르세요.`}</div>}
      {is("wordcloud") && (
        <div className="hint cm-field" role="group" aria-label="워드클라우드 방식" style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
          <CloudFields cloud={cloud} schema="" table={f.table} texts={colsOfType(cols, ["문자"])} full={false} tagged={!!fill?.source?.body?.labels} onChange={setCloud} />
        </div>
      )}
      {is("histogram") && <Txt label="구간 수" type="number" min={1} max={consts.dash.max_bins ?? 100} width={60} value={f.bins} onChange={v => set("bins", v)} />}
      {is("bullet") && <Txt label="구간" title="바탕 띠의 경계 값. 작은 것부터 쉼표로 1~6개" placeholder="예: 50, 80, 100" value={f.bands} onChange={v => set("bands", v)} />}
      {is("bump", "candle", "cohort") && <Sel label="기간 단위" value={f.cUnit} onChange={v => set("cUnit", v)} options={Object.entries(consts.dash.units)} />}
      {is(...DATE_X) && <Sel label="날짜 컬럼" title="가로축이 될 날짜(시각) 컬럼. 대시보드 기간도 이 컬럼에 걸려요" value={f.date} onChange={v => set("date", v)} options={dates} blank="(고르세요)" need />}
      {is(...WITH_DATE) && <Sel label="날짜 컬럼" title="대시보드 기간과 기준일을 이 날짜 컬럼에 걸어요. 날짜 컬럼이 하나뿐이면 자동으로 골라요" value={f.date} onChange={v => set("date", v)} options={dates} blank="(없음: 기간을 걸지 않음)" />}
      {is("kpi", "donut_kpi", "summary", "stripes", "blocks", "target_trend", "bullet") && (
        <div className="hint cm-field">목표
          <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input type="number" aria-label="목표 숫자" placeholder="목표 숫자" style={{ width: 110 }} value={f.targetValue} onChange={e => set("targetValue", e.target.value)} /><span className="cm-word">또는</span>
            <select aria-label="목표 이름" title="목표 파일에서 이름으로 찾아요" value={f.targetName} onChange={e => set("targetName", e.target.value)}>
              <option value="">(이름으로 찾지 않음)</option>{targetNames.map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </span>
        </div>
      )}
      {is("donut_kpi") && <Sel label="고리" value={f.ring} onChange={v => set("ring", v)} options={[["stack", "한 고리 누적"], ["double", "겹친 두 고리"], ["half", "반원 게이지"], ["ticks", "고리와 목표 눈금"]]} />}
      {is("stripes") && <Txt label="칸 수" type="number" min={1} max={100} width={60} value={f.cells} onChange={v => set("cells", v)} />}
      {is("blocks") && <Txt label="한 칸 크기" type="number" placeholder="자동" width={90} value={f.size} onChange={v => set("size", v)} />}
      {is("recent_rows", "table") && <Txt label="행 수" type="number" min={1} max={100} width={60} value={f.rowCount} onChange={v => set("rowCount", v)} />}
      {is("table") && <Txt label="묶음 컬럼" placeholder="예: 매장, 날짜 (쉼표, 3개까지)" value={f.groupCols} onChange={v => set("groupCols", v)} />}
      {is("table") && <Check label="합계 줄" checked={f.total} onChange={v => set("total", v)} />}
      {is("table", "sentence") && <LineBox label="색 규칙" value={f.condText} onChange={v => set("condText", v)} parse={parseRuleLine} placeholder={"색 규칙(한 줄에 하나): 컬럼 | 식 | #색 | 화살표 | 칸·줄\n예: 상태 | *지연* | #e03131 | 없음 | 줄"} />}

      {is(...WATCH, "deviation") && <Sel label="대상 컬럼" title="서버, 장비, 매장처럼 상태를 볼 대상의 이름 컬럼" value={f.entity} onChange={v => set("entity", v)} options={all} blank="(고르세요)" need />}
      {is(...WATCH) && (
        <>
          <Sel label="시각 컬럼" title="데이터가 마지막으로 들어온 시각 컬럼. 끊겼는지 볼 때 써요" value={f.timeCol} onChange={v => set("timeCol", v)} options={all} blank="(없음)" />
          <Sel label="종류 컬럼" title="배치도에서 종류별로 줄을 나눌 컬럼" value={f.kindCol} onChange={v => set("kindCol", v)} options={all} blank="(없음)" />
          <Sel label="연결 대상 컬럼" title="이 대상이 영향을 주는 대상의 이름 컬럼. 연결선으로 이어져요" value={f.linkCol} onChange={v => set("linkCol", v)} options={all} blank="(없음)" />
          <Txt label="끊김 기준(분)" type="number" min={1} placeholder="예: 30" width={80} value={f.stale} onChange={v => set("stale", v)} />
          <LineBox label="상태 규칙" value={f.stateText} onChange={v => set("stateText", v)} parse={parseStateLine} placeholder={"상태 규칙(한 줄에 하나): 컬럼 연산자 값 단계\n예: cpu >= 90 이상 / 상태 = DOWN 주의"} />
          <LineBox label="연결선" value={f.linkText} onChange={v => set("linkText", v)} parse={parseLinkLine} placeholder={"연결선(한 줄에 하나): 출발 -> 도착\n예: 라우터1 -> 서버1"} />
        </>
      )}
      {is("map") && <Sel label="배경" value={f.bg} onChange={v => set("bg", v)} options={[["rack", "서버실 칸"], ["network", "네트워크"], ["image", "그림 파일"]]} />}
      {is("map") && <Txt label="그림 경로" title="png, jpg, svg 파일을 8MB까지 쓸 수 있어요. 카드를 저장하면 보여요" placeholder="그림 파일 경로" value={f.imagePath} onChange={v => set("imagePath", v)} />}

      {is("chart") && (
        <>
          <Sel label="차트 종류" title="차트 종류. 자동은 막대로 그려요" value={f.cType} onChange={v => set("cType", v)} options={Object.entries(consts.dash.types)} blank="자동" />
          <Sel label="가로축" title="가로축 컬럼. 날짜 컬럼이면 일, 주, 월, 연으로 바꿔 볼 수 있어요" value={f.cX} onChange={v => set("cX", v)} options={all} blank="(고르세요)" need />
          <Sel label="버블 크기" title="버블 크기로 쓸 숫자 컬럼" value={f.cSize} onChange={v => set("cSize", v)} options={nums} blank="(없음)" />
        </>
      )}
      {is("funnel", "sankey", "cohort") && (
        <>
          <Sel label="사용자 컬럼" title="사용자(사람)를 구분하는 컬럼" value={f.fUser} onChange={v => set("fUser", v)} options={all} blank="(고르세요)" need />
          {!is("cohort") && !(is("funnel") && f.fDefine === "stages") && <Sel label="단계 컬럼" title="단계(행동) 이름이 들어 있는 컬럼" value={f.fStep} onChange={v => set("fStep", v)} options={all} blank="(고르세요)" need />}
          <Sel label="시각 컬럼" title="행동이 일어난 시각 컬럼. 대시보드 기간도 이 컬럼에 걸려요" value={f.fTime} onChange={v => set("fTime", v)} options={dates} blank="(고르세요)" need />
        </>
      )}
      {is("funnel") && <Sel label="단계 정의" title="값 목록은 단계 컬럼의 값을 순서대로, 조건 줄은 단계마다 조건을 적어요" value={f.fDefine} onChange={v => set("fDefine", v as Form["fDefine"])} options={[["values", "값 목록"], ["stages", "조건 줄"]]} />}
      {is("funnel") && f.fDefine === "stages" && (
        <>
          <Sel label="틀" title="고르면 단계 이름과 차례를 채워요. 직접은 지금 줄을 그대로 둬요" value={tpl} onChange={v => { setTpl(v); if (v) set("fStages", templateStages(v as keyof typeof FUNNEL_TEMPLATES)); }} options={Object.entries(FUNNEL_TEMPLATES).map(([k, t]): [string, string] => [k, t.label])} blank="직접" />
          {f.fStages.map((r, i) => (
            <Block key={i}>
              <div role="group" aria-label={`단계 ${i + 1}`} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <input aria-label="단계 이름" placeholder="단계 이름" value={r.name} onChange={e => setStage(i, { name: e.target.value })} />
                <textarea aria-label="조건 줄" rows={2} value={r.conds} placeholder={"한 줄에 하나, 줄끼리 and. 예: event in purchase, refund"} style={{ width: "100%", fontSize: 12 }} onChange={e => setStage(i, { conds: e.target.value })} />
                <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <select aria-label="재방문" title="첫 활동 뒤 N일 안에 다시 온 사람" value={r.revisit === "" ? "" : REVISIT_CHOICES.map(String).includes(r.revisit) ? r.revisit : REVISIT_CUSTOM} onChange={e => setStage(i, { revisit: e.target.value === REVISIT_CUSTOM ? REVISIT_CUSTOM_START : e.target.value })}>
                    <option value="">재방문 없음</option>
                    {REVISIT_CHOICES.map(n => <option key={n} value={String(n)}>{n}일</option>)}
                    <option value={REVISIT_CUSTOM}>직접 입력</option>
                  </select>
                  {r.revisit !== "" && !REVISIT_CHOICES.map(String).includes(r.revisit) && <><input type="number" aria-label="재방문 일수" min={1} step={1} style={{ width: 70 }} value={r.revisit} onChange={e => setStage(i, { revisit: e.target.value })} /><span className="cm-word">N일 안에 다시 온 사람</span></>}
                  <button type="button" className="ghost" aria-label="위로" disabled={i === 0} onClick={() => moveStage(i, -1)}>위</button>
                  <button type="button" className="ghost" aria-label="아래로" disabled={i === f.fStages.length - 1} onClick={() => moveStage(i, 1)}>아래</button>
                  <button type="button" className="ghost" aria-label="빼기" onClick={() => set("fStages", f.fStages.filter((_, j) => j !== i))}>빼기</button>
                </span>
              </div>
            </Block>
          ))}
          {f.fStages.length < MAX_STAGES && <button type="button" className="linkish" style={{ minHeight: 24 }} onClick={() => set("fStages", [...f.fStages, { name: "", conds: "", revisit: "" }])}>단계 더하기</button>}
          <Sel label="세기" title="순서대로는 앞 단계를 거친 사람만, 따로는 단계마다 조건에 맞는 사람을 세요" value={f.fCount} onChange={v => set("fCount", v as Form["fCount"])} options={[["order", "순서대로"], ["each", "단계마다 따로"]]} />
        </>
      )}
      {is("funnel") && <Txt label="절사평균 자르기 %" title="걸린 시간의 절사평균에서 위아래를 합쳐 자를 몫(%). 비우면 10" type="number" min={0} max={50} placeholder="10" width={80} value={f.fTrim} onChange={v => set("fTrim", v)} />}
      {is("sankey") && <Sel label="흐름 방식" value={f.fFlow} onChange={v => set("fFlow", v as Form["fFlow"])} options={[["steps", "단계를 적기"], ["auto", "자동"]]} />}
      {is("sankey") && f.fFlow === "auto" && (
        <>
          <button type="button" className="btn" disabled={!f.table || !f.fStep || loadingVals} style={{ minHeight: 24 }} onClick={loadValues}>값 목록 불러오기</button>
          {more && <div className="hint cm-note">값이 많아 앞 {vals.length}개만 보여요.</div>}
          {shownVals.map(v => (
            <label key={v} className="hint cm-field">{v}
              <select aria-label={`${v} 역할`} value={f.fStarts.includes(v) ? "start" : f.fMiddles.includes(v) ? "middle" : f.fEnds.includes(v) ? "end" : ""} onChange={e => setRole(v, e.target.value)}>
                <option value="">빼기</option><option value="start">시작</option><option value="middle">중간</option><option value="end">끝</option>
              </select>
            </label>
          ))}
          <Txt label="단계 깊이" type="number" min={1} placeholder="기본" width={80} value={f.fDepth} onChange={v => set("fDepth", v)} />
          <div className="hint cm-note">클수록 느려질 수 있어요(기본 6, 최대 50).</div>
          <Sel label="이탈 칸" value={f.fDropout} onChange={v => set("fDropout", v as Form["fDropout"])} options={[["on", "켬"], ["off", "끔"]]} blank="기본 (끝 값을 골랐으면 켬)" />
          <div className="hint cm-note">켜면 고른 값 중 뒤에 더 없는 칸에서 멈춘 사람을 “이탈”로 모아요. 깊이로 잘린 사람과 끝 값에 닿은 사람은 빼요.</div>
        </>
      )}
      {is("sankey") && <Block><label className="hint">조건 줄<textarea rows={2} value={f.fConds} placeholder="한 줄에 하나, 줄끼리 and. 예: platform = iOS" style={{ width: "100%", fontSize: 12 }} onChange={e => set("fConds", e.target.value)} /></label></Block>}
      {is("funnel", "sankey") && (is("funnel") || f.fFlow === "steps") && (
        <>
          {(is("sankey") || f.fDefine === "values") && <Txt label="단계 순서" title={is("sankey") ? "단계 값을 순서대로 쉼표로 나눠 적어요. 2~10개" : "단계 값을 순서대로 쉼표로 나눠 적어요. 1~10개"} placeholder="예: 방문, 장바구니, 결제" value={f.fSteps} onChange={v => set("fSteps", v)} />}
          <div className="hint cm-field">기간 제한
            <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input type="number" aria-label="기간 제한 숫자" min={0} step="any" placeholder="비움" style={{ width: 80 }} value={f.fWin} onChange={e => set("fWin", e.target.value)} />
              <select aria-label="기간 제한 단위" value={f.fUnit} onChange={e => set("fUnit", e.target.value)}><option value="minute">분</option><option value="hour">시간</option><option value="day">일</option></select><span className="cm-word">안에</span>
            </span>
          </div>
          {is("funnel") && f.fDefine === "stages" && f.fCount === "each" && <div className="hint cm-note">단계마다 따로 셀 때 기간 제한은 첫 단계를 처음 만족한 때부터 세요. 첫 단계를 만족하지 않은 사람은 모든 단계에서 빠져요.</div>}
        </>
      )}
      {is("geomap") && (
        <>
          <Sel label="영역 테이블" title="GeoJSON을 연결한 영역 테이블. 없으면 점만 찍어요. 위에서 고른 테이블은 점 테이블이에요" value={f.gTable} onChange={v => set("gTable", v)} options={names} blank="(없음: 점만 찍기)" />
          <Txt label="영역 파일" title="영역 테이블 대신 파일을 써요" placeholder="또는 GeoJSON 파일 경로" value={f.gPath} onChange={v => set("gPath", v)} />
          {f.gPath.trim() ? <Txt label="영역 이름" title="GeoJSON 각 영역의 properties에서 이름이 든 키" placeholder="예: name" value={f.gName} onChange={v => set("gName", v)} need /> : <Sel label="영역 이름" value={f.gName} onChange={v => set("gName", v)} options={gCols.map(c => c[0])} blank="(영역 이름 컬럼)" need={!!f.gTable} />}
          {f.gPath.trim() ? <Txt label="도형 컬럼" title="파일 방식은 비우면 geometry" placeholder="geometry" value={f.gGeom} onChange={v => set("gGeom", v)} /> : <Sel label="도형 컬럼" value={f.gGeom} onChange={v => set("gGeom", v)} options={gCols.map(c => c[0])} blank="geometry" />}
          <Txt label="영역 값 컬럼" placeholder="비우면 점으로 센 값" value={f.gRegion} onChange={v => set("gRegion", v)} />
          <Sel label="위도" title="점의 위도 컬럼" value={f.gLat} onChange={v => set("gLat", v)} options={nums} blank="(없음: 영역만)" />
          <Sel label="경도" title="점의 경도 컬럼" value={f.gLon} onChange={v => set("gLon", v)} options={nums} blank="(없음: 영역만)" />
          <Sel label="점의 값" title="영역마다 합계나 평균을 구할 점의 값 컬럼. 점 수만 세려면 비워 두세요" value={f.gValue} onChange={v => set("gValue", v)} options={nums} blank="(없음)" />
          <Sel label="영역마다" title="영역마다 구할 값. 점의 값 컬럼을 골랐을 때 써요" value={f.gAgg} onChange={v => set("gAgg", v)} options={[["sum", "합계"], ["mean", "평균"], ["min", "최소"], ["max", "최대"], ["count", "점 수"]]} />
          <Check label="점 그리기" checked={f.gPoints} onChange={v => set("gPoints", v)} />
          <Check label="지도 배경" checked={f.gTiles} onChange={v => set("gTiles", v)} />
          <Block><div className="hint cm-note">지도 배경을 켜면 보는 범위가 OpenStreetMap 서버(tile.openstreetmap.org)로 전송돼요. 기본은 꺼져 있어요.</div></Block>
        </>
      )}
      {is("deviation") && (
        <>
          <Sel label="시간대 묶음" value={f.bucket} onChange={v => set("bucket", v)} options={[["hour", "시간"], ["day", "일"], ["week", "주"], ["month", "월"]]} />
          <Txt label="기준 값" type="number" placeholder="비우면 대상 평균" width={130} value={f.baseline} onChange={v => set("baseline", v)} />
          <Txt label="허용 폭" type="number" placeholder="비우면 기준의 20%" width={130} value={f.threshold} onChange={v => set("threshold", v)} />
        </>
      )}
      {is("sentence") && (
        <>
          <Block><label className="hint">문장 틀<b className="cm-need" title="꼭 채울 칸">*</b><textarea rows={2} value={f.template} placeholder="예: 오늘 매출은 {매출}이고 {비교} ({기준일} 기준)" style={{ width: "100%", fontSize: 12 }} onChange={e => set("template", e.target.value)} /></label></Block>
          <LineBox label="빈칸" value={f.blankText} onChange={v => set("blankText", v)} parse={l => parseBlankLine(l, aggs)} placeholder={"빈칸(한 줄에 하나): 이름 = 종류(값·직전·비교·기준일) [컬럼 [집계 [모양]]]\n예: 매출 = 값 amt sum 금액 / 비교 = 비교 amt sum / 기준일 = 기준일"} />
        </>
      )}

      <details className="cm-more"><summary>더 많은 설정</summary>
        <label className="hint cm-field">카드 이름<input value={f.title} placeholder="카드 이름 (비우면 자동)" onChange={e => set("title", e.target.value)} /></label>
        <div className="row">
          <input aria-label="목표 파일 경로" title="xlsx, csv, yaml, json, md, txt 파일을 읽을 수 있어요" placeholder="목표 파일 경로" value={path} onChange={e => setPath(e.target.value)} />
          <button type="button" className="btn" style={{ minHeight: 24 }} onClick={saveTargets}>저장</button>
        </div>
        <div className="hint cm-note">{note || (targets && !targets.path ? "목표 파일을 정하면 이름으로 목표를 찾을 수 있어요" : "")}</div>
      </details>
      <div className="err" role="alert">{err}</div>
      {cloudWhy && <div className="hint cm-note">{cloudWhy}</div>}
      </div>
      <div className="row d-add-foot">
        <button type="button" className="btn p" disabled={busy || !!cloudWhy} style={{ minHeight: 24 }} onClick={go}>{fill ? "이 카드에 채우기" : "카드 추가"}</button>
        <button type="button" className="btn" style={{ minHeight: 24 }} onClick={close}>닫기</button>
      </div>
    </div>
  );
}
