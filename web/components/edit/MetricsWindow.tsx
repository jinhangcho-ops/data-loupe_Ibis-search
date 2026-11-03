"use client";
// 지표 창(옛 dashboard.html openMetrics: 테이블을 고르면 그 테이블로 쿼리 화면 [지표] 탭과 같은 내용을 보였다). 옛 sentences.js·metric_catalog.js를 다시 쓰지 않고
// 목록·표만 둔다: 테이블 고르기(연결이 있을 때), 지표 파일의 지표(myMetrics.legacy, [카드로 추가] = 그 테이블의 KPI 카드 body.names), 내 지표(myMetrics.metrics),
// 지표 사전 표(/api/catalog, 고른 테이블에서 쓸 수 있는지 /api/catalog/available, [내 지표로 저장] /api/catalog/save). 계산·빈칸 고치기는 쿼리 화면 [지표] 탭(/search#metrics).
// 대시보드 카드는 지표 파일의 이름만 구할 수 있다(서버 dash_calc.metric_scalar가 metric_file에서 찾는다): 내 지표·사전 항목은 카드로 넣지 않는다.
import { useEffect, useState } from "react";
import { catalog, catalogAvailable, catalogSave, columns as fetchColumns, myMetrics, tables as fetchTables, type Availability, type CatalogEntry } from "@/lib/api";
import { useConstants } from "@/lib/constants";
import type { Card } from "@/lib/types";
import { buildCard, emptyForm } from "./menu/build";

export type MetricsWindowProps = { open: boolean; onClose: () => void; onAdd: (card: Partial<Card>) => void };

// 서버 타입 이름 → 지표 사전의 종류(옛 funcs.js KIND_OF)
const KIND_OF: Record<string, string> = { 정수: "num", 실수: "num", 문자: "text", 날짜: "date", 날짜시간: "date", "참/거짓": "bool", json: "json" };
const TABLE = { width: "100%", whiteSpace: "normal", fontSize: 12.5 } as const;
const kindOf = (t: string) => KIND_OF[t] ?? (["num", "text", "date", "bool", "json", "array", "struct"].includes(t) ? t : t.startsWith("목록") ? "array" : t.startsWith("구조") ? "struct" : "other");

export function MetricsWindow(props: MetricsWindowProps) {
  return props.open ? <Window {...props} /> : null;  // 열 때마다 새로 받는다
}

type Mine = Awaited<ReturnType<typeof myMetrics>>;

function Window({ onClose, onAdd }: MetricsWindowProps) {
  const consts = useConstants();
  const [names, setNames] = useState<string[] | null>(null);  // 테이블 목록(null = 연결 없음·못 읽음)
  const [table, setTable] = useState("");
  const [kinds, setKinds] = useState<{ table: string; cols: Record<string, string> }>({ table: "", cols: {} });
  const [mine, setMine] = useState<Mine | null>(null);
  const [cat, setCat] = useState<{ areas: { id: string; name: string }[]; entries: CatalogEntry[] } | null>(null);
  const [av, setAv] = useState<{ table: string; found: Availability }>({ table: "", found: {} });
  const [area, setArea] = useState("");
  const [note, setNote] = useState({ text: "", ok: false });

  useEffect(() => {
    fetchTables().then(r => setNames(r.tables)).catch(() => setNames(null));
    myMetrics().then(setMine).catch(e => setNote({ text: (e as Error).message, ok: false }));
    catalog().then(c => { setCat(c); setArea(c.areas[0]?.id ?? ""); }).catch(e => setNote({ text: (e as Error).message, ok: false }));
  }, []);
  // 테이블을 고르면 그 컬럼의 종류와 사전 항목을 쓸 수 있는지
  useEffect(() => {
    if (!table) return;
    let live = true;
    fetchColumns(table).then(async r => {
      const cols = Object.fromEntries(r.columns.map(([n, t]) => [n, kindOf(t)]));
      const found = (await catalogAvailable(cols)).availability;
      if (live) { setKinds({ table, cols }); setAv({ table, found }); }
    }).catch(e => live && setNote({ text: (e as Error).message, ok: false }));
    return () => { live = false; };
  }, [table]);

  const ready = kinds.table === table && av.table === table ? av.found : {};  // 늦게 온 앞 테이블의 결과는 버린다
  const addLegacy = (name: string) => {
    if (!table) return setNote({ text: "카드로 넣을 테이블을 먼저 고르세요.", ok: false });
    try { onAdd(buildCard({ ...emptyForm("kpi"), table, metric: name, title: name }, consts?.dash.aggs ?? [], undefined, consts?.agg_names)); onClose(); }
    catch (e) { setNote({ text: (e as Error).message, ok: false }); }
  };
  const save = async (e: CatalogEntry) => {
    try {
      const { metric } = await catalogSave(e.id, ready[e.id]?.suggest ?? {}, table, kinds.cols);
      setNote({ text: `내 지표로 저장했어요: ${metric.text}`, ok: true });
      setMine(await myMetrics());
    } catch (err) { setNote({ text: (err as Error).message, ok: false }); }
  };
  const rows = (cat?.entries ?? []).filter(e => e.area === area);
  return (
    <div className="modal" onClick={e => { if (e.target === e.currentTarget) onClose(); }} onKeyDown={e => { if (e.key === "Escape") onClose(); }}>
      <div className="modal-box" role="dialog" aria-label="지표" style={{ width: "min(860px, 94vw)", display: "flex", flexDirection: "column", gap: 10 }}>
        <div className="head row" style={{ gap: 8 }}>
          <h3>지표</h3>
          {names && (
            <select id="d-met-table" aria-label="테이블" value={table} onChange={e => setTable(e.target.value)} style={{ width: "auto" }}>
              <option value="">테이블 고르기</option>
              {names.map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          )}
          <button className="ib" title="닫기" style={{ marginLeft: "auto" }} onClick={onClose}>×</button>
        </div>
        {!names && <div className="hint">데이터에 연결하면 테이블을 골라 지표를 카드로 넣거나 쓸 수 있는지 볼 수 있어요.</div>}
        <div className={note.ok ? "ok" : "err"} id="err-met" role="status">{note.text}</div>

        <h4 style={{ margin: 0 }}>지표 파일의 지표</h4>
        {mine?.legacy.length ? (
          <table style={TABLE} id="d-met-legacy"><tbody>
            {mine.legacy.map(m => (
              <tr key={m.id}>
                <td><b>{m.name}</b><div className="hint">{m.description || m.text}</div></td>
                <td style={{ width: 1 }}><button className="btn" disabled={!table} title={table ? `${table} 테이블로 KPI 카드를 넣어요` : "테이블을 먼저 고르세요"} onClick={() => addLegacy(m.name)}>카드로 추가</button></td>
              </tr>
            ))}
          </tbody></table>
        ) : <div className="hint">{mine ? "지표 파일에 지표가 없어요. 쿼리 화면 [지표] 탭에서 지표 파일을 정해요." : "읽는 중…"}</div>}

        <h4 style={{ margin: 0 }}>내 지표</h4>
        {mine?.metrics.length ? (
          <ul id="d-met-mine" style={{ margin: 0, paddingLeft: 18 }}>{mine.metrics.map(m => <li key={m.id}>{m.text}</li>)}</ul>
        ) : <div className="hint">{mine ? "저장한 내 지표가 없어요. 아래 지표 사전에서 [내 지표로 저장]을 누르거나 쿼리 화면 [지표] 탭에서 만들어요." : ""}</div>}
        <div className="hint">내 지표는 문장·쿼리 화면에서 구해요. 카드로 넣으려면 쿼리 화면에서 차트로 만든 뒤 [대시보드에 추가]를 누르세요.</div>

        <h4 style={{ margin: 0 }}>지표 사전</h4>
        {cat ? (
          <>
            <div className="btools" role="group" aria-label="영역">
              {cat.areas.map(a => <button key={a.id} type="button" className={`btype${a.id === area ? " on" : ""}`} aria-pressed={a.id === area} onClick={() => setArea(a.id)}>{a.name}</button>)}
            </div>
            <div className="table-wrap"><table style={TABLE} id="d-met-catalog"><thead><tr><th>지표</th><th>설명</th><th>{table ? `${table}에서` : "쓸 수 있나"}</th><th></th></tr></thead><tbody>
              {rows.map(e => {
                const a = ready[e.id], ok = !!a?.usable && !e.cross;
                return (
                  <tr key={e.id}>
                    <td><b>{e.name}</b>{e.abbr ? <span className="hint"> {e.abbr}</span> : null}</td>
                    <td className="hint">{e.help}</td>
                    <td className="hint">{!table ? "테이블을 고르세요" : !a ? "…" : a.usable ? a.note ?? "쓸 수 있어요" : a.reason}</td>
                    <td style={{ width: 1 }}><button className="btn" disabled={!ok} title={e.cross ? "다른 테이블과 맞춤 구절이 필요해서 쿼리 화면 [지표] 탭에서 저장해요" : "추천 컬럼으로 이 테이블에 묶은 내 지표로 저장해요"} onClick={() => save(e)}>내 지표로 저장</button></td>
                  </tr>
                );
              })}
            </tbody></table></div>
          </>
        ) : <div className="hint">읽는 중…</div>}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <a className="btn" href="/search#metrics">지표 화면 열기</a>
          <button className="ghost" onClick={onClose}>닫기</button>
        </div>
      </div>
    </div>
  );
}
