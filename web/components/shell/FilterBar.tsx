"use client";
// 필터 줄(3단계): 대시보드에 정해 둔 필터 칸(dash.filters)마다 값 고르는 칸. 값을 고르면 화면 틀(setFilter)이 모든 카드를 그 조건으로 다시 구해 화면에만 보인다(저장 값은 그대로).
// 값 목록은 연결돼 있을 때 서버에서 읽는다(/api/dash/filter/values). 연결이 없으면 칸을 잠그고 안내를 보인다. 그 컬럼이 없는 테이블의 카드는 그대로이고 카드 아래에 안내가 붙는다(서버 data.note).
// 만드는 곳(build)에서는 필터 칸을 더하고(컬럼 이름: 카드가 쓰는 컬럼을 후보로 보인다) ×로 뺀다. 목록은 대시보드와 함께 저장된다.
import { useEffect, useMemo, useState } from "react";
import { filterValues } from "@/lib/api";
import { useConstants } from "@/lib/constants";
import { pressLabel } from "@/lib/links";
import { useDash } from "@/lib/store";
import type { DashFilter } from "@/lib/types";
import { useShell } from "./ShellProvider";

const SEP = "\x01";  // 컬럼 이름들을 한 글자로 이어 의존값으로 쓸 때의 구분자
type Body = { x?: unknown; split?: unknown; series?: { y?: unknown }[] };

export function FilterBar() {
  const { state, dispatch } = useDash();
  const { conn, build, picks, setFilter, presses, press } = useShell();
  const max = useConstants()?.dash.max_filters ?? Infinity;
  const dash = state.dash, id = dash?.id, key = conn?.key ?? "";
  const filters = useMemo(() => dash?.filters ?? [], [dash?.filters]);
  const columns = filters.map(f => f.column).join(SEP);
  const [values, setValues] = useState<Record<string, string[]>>({});
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  useEffect(() => {
    if (!id || !key || !columns) return;
    let live = true;
    columns.split(SEP).forEach(column => filterValues(id, column).then(
      d => { if (live) setValues(v => ({ ...v, [column]: d.values })); },
      e => { if (live) setError((e as Error).message); }));
    return () => { live = false; };
  }, [id, key, columns]);
  // 카드가 쓰는 컬럼(가로축·나누기·계열, 행 카드의 컬럼)을 필터 후보로
  const known = useMemo(() => [...new Set((dash?.cards ?? []).flatMap(c => {
    const b = (c.source?.body ?? {}) as Body;
    return [b.x, b.split, ...(b.series ?? []).map(s => s.y), ...((c.data as { columns?: unknown } | undefined)?.columns instanceof Array ? (c.data as { columns: unknown[] }).columns : [])];
  }).filter((x): x is string => typeof x === "string" && !!x))].sort(), [dash?.cards]);
  const pressed = dash?.press_chips === false ? [] : presses.filter(p => p.value);  // 차트를 눌러 건 것: 칩으로 보이고 ×로 푼다(대시보드마다 켜고 끔, 없으면 켜짐)
  if (!dash || (!filters.length && !build && !pressed.length)) return null;
  const save = (next: DashFilter[]) => dispatch({ type: "meta", patch: { filters: next } });
  const column = name.trim(), taken = filters.some(f => f.column === column);
  const add = () => { save([...filters, { column, label: "" }]); setName(""); setError(""); };
  return (
    <div className="ctl board-ctl" id="d-filters" role="group" aria-label="필터">
      {filters.map(f => (
        <label key={f.column} className="[display:flex] items-center gap-1.5">
          <span className="hint m-0">{f.label || f.column}</span>
          <select value={picks.find(p => p.column === f.column)?.in[0] ?? ""} disabled={!conn} title={`${f.label || f.column} 값으로 모든 카드를 걸러요. 이 컬럼이 없는 카드는 그대로예요`}
            onChange={e => { setFilter(f.column, e.target.value ? [e.target.value] : []); }}>
            <option value="">전체</option>
            {(values[f.column] ?? []).map(v => <option key={v} value={v}>{v}</option>)}
          </select>
          {build && <button type="button" className="ghost" title="이 필터 칸 빼기" aria-label={`${f.label || f.column} 필터 빼기`}
            onClick={() => { save(filters.filter(x => x.column !== f.column)); setFilter(f.column, []); }}>×</button>}
        </label>
      ))}
      {pressed.map(p => (
        <span key={p.from} className="chip">{pressLabel(p, dash.cards)}
          <button type="button" className="ghost" title="이 거르기 풀기" aria-label={`${pressLabel(p, dash.cards)} 거르기 풀기`} onClick={() => { press(p.from, null); }}>×</button>
        </span>
      ))}
      {build && filters.length < max && (
        <>
          <label className="hint m-0"><input type="checkbox" checked={dash.press_chips !== false} onChange={e => dispatch({ type: "meta", patch: { press_chips: e.target.checked } })} /> 누른 값을 여기에 칩으로 보이기</label>
          <input className="[flex:0_0_11rem] w-auto" list="d-filter-cols" value={name} placeholder="필터로 쓸 컬럼 이름" aria-label="필터로 쓸 컬럼 이름"
            onChange={e => setName(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && column && !taken) add(); }} />
          <datalist id="d-filter-cols">{known.filter(k => !filters.some(f => f.column === k)).map(k => <option key={k} value={k} />)}</datalist>
          <button type="button" className="ghost" id="d-filter-add" disabled={!column || taken} title="보는 곳 위쪽에 이 컬럼의 값을 고르는 칸이 생겨요" onClick={add}>필터 추가</button>
        </>
      )}
      {!conn && filters.length > 0 && <span className="hint m-0">데이터에 연결하면 거를 수 있어요</span>}
      {error && <span className="err" role="status">{error}</span>}
    </div>
  );
}
