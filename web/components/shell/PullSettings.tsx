"use client";
// B11 — 대시보드 설정 창의 "데이터 받기" 묶음(plans/dash-blocks.md "B11 세부 명세"): 계산 방식·갱신 주기·받는 범위(새 줄 컬럼)·받은 데이터 크기.
// 보는 곳에서도 바꿀 수 있는 설정이라 [저장]을 누르면 /api/dash/settings의 pull만 저장한다(배치·카드 설정은 건드리지 않는다).
import { useEffect, useState } from "react";
import { pullFull, pullRange, pullState, pullTables, saveSettings, setPullCrypt } from "@/lib/api";
import { useConstants } from "@/lib/constants";
import { minimumText, pullError, pullOf } from "@/lib/pull";
import { useDash } from "@/lib/store";
import type { Dashboard, Pull, PullCrypt, PullState, PullTable, PullUnit } from "@/lib/types";
import { useShell } from "./ShellProvider";

export type PullSettingsProps = { dash: Dashboard };

type Msg = { text: string; ok?: boolean };
const CRYPT_NAMES: Record<PullCrypt, string> = { none: "없음", aes128: "AES-128", aes256: "AES-256", pass: "내가 적는 암호문" };
const NO_TABLES = "연결하면 새 줄 컬럼을 고를 수 있어요.";
const ASK_CRYPT = "이미 받은 데이터를 어떻게 할까요?";
const NEED_PASS = "암호문을 적어 주세요.";

/** [SPECIFICATION] plans/dash-blocks.md#PullSettings
 *  대시보드 설정 창의 데이터 받기 묶음(계산 방식·갱신 주기·받는 범위·새 줄 컬럼·받은 데이터 크기)을 그리고 [저장]하면 pull만 저장한다. */
export function PullSettings({ dash }: PullSettingsProps): React.ReactElement | null {
  const c = useConstants()?.dash.pull ?? null;
  const { state, dispatch } = useDash();
  const { conn, flush, dbMin, build, recompute } = useShell();
  const [pull, setPull] = useState<Pull | null>(null);
  const [tables, setTables] = useState<PullTable[] | null>(null);
  const [remote, setRemote] = useState<boolean | null>(null);
  const [held, setHeld] = useState<PullState | null>(null);
  const [pass, setPass] = useState("");
  const [askCrypt, setAskCrypt] = useState(false);
  const [spans, setSpans] = useState<Record<string, { start: string; end: string }>>({});
  const [msg, setMsg] = useState<Msg>({ text: "" });
  const [busy, setBusy] = useState(false);
  const connected = !!conn;
  const fail = (e: unknown) => setMsg({ text: (e as Error).message });
  // 대시보드를 열 때 한 번 저장값으로 채운다(자동 저장으로 dash가 바뀌어도 쓰던 칸을 덮지 않는다)
  const [filled, setFilled] = useState("");
  if (c && filled !== dash.id) { setFilled(dash.id); setPull(pullOf(dash, c)); }
  useEffect(() => { pullState(dash.id).then(setHeld).catch(fail); }, [dash.id]);
  useEffect(() => {
    const got = connected ? pullTables(dash.id).then(r => { setRemote(r.remote); return r.tables; }) : Promise.resolve<PullTable[]>([]);
    got.then(setTables).catch(fail);
  }, [dash.id, connected]);
  if (!c || !pull) return null;

  const saved = pullOf(dash, c);
  const every = pull.every;
  const marks = (tables ?? []).filter(t => !t.join);
  const setMark = (t: PullTable, column: string) => setPull({ ...pull, marks: [...pull.marks.filter(m => !(m.schema === t.schema && m.table === t.table)), ...(column ? [{ schema: t.schema, table: t.table, column }] : [])] });
  const run = async (job: () => Promise<string>) => {
    setBusy(true);
    try { setMsg({ text: await job(), ok: true }); } catch (e) { fail(e); } finally { setBusy(false); }
  };
  const reload = async () => setHeld(await pullState(dash.id));
  const commit = (how: "pull" | "rewrite" | null) => run(async () => {
    setAskCrypt(false);
    await flush();
    if (how) await setPullCrypt(dash.id, pull.crypt, pass, how);
    const s = await saveSettings(dash.id, { pull });
    dispatch({ type: "saved", dash: s.dashboard });
    setPass("");
    await reload();
    return "받기 설정을 저장했어요.";
  });
  const save = () => {
    const err = pullError(pull, tables ?? [], c, dbMin);
    if (err) return setMsg({ text: err });
    if (pull.crypt === "pass" && pass === "" && saved.crypt !== "pass") return setMsg({ text: NEED_PASS });
    if (pull.crypt !== saved.crypt || (pull.crypt === "pass" && pass !== "")) return setAskCrypt(true);
    return commit(null);
  };
  const full = () => run(async () => {
    await flush();
    await pullFull(dash.id, state.ctx ?? null);
    await recompute(false);
    await reload();
    return "전부 다시 받았어요.";
  });
  const refetch = (slot: string) => run(async () => {
    const span = spans[slot] ?? { start: "", end: "" };
    await pullRange(dash.id, slot, span.start, span.end);
    await recompute(false);
    await reload();
    return "이 기간을 다시 받았어요.";
  });

  return (
    <>
      <h4>데이터 받기</h4>
      <div className="row" role="radiogroup" aria-label="계산 방식">
        {c.calcs.map(k => (
          <label key={k} className="hint"><input type="radio" name="pull-calc" checked={pull.calc === k} disabled={!build} onChange={() => setPull({ ...pull, calc: k })} />
            {k === "pc" ? "DB는 거르기만, 계산은 이 PC에서" : "DB에서 계산"}</label>
        ))}
      </div>
      {!build && <div className="hint m-0">계산 방식은 대시보드를 만들고 고치는 곳에서 바꿔요.</div>}
      {remote === false && <div className="hint m-0">이 PC 파일 연결은 늘 이 PC에서 계산해요.</div>}
      <div className="row" role="radiogroup" aria-label="갱신 주기">
        <label className="hint"><input type="radio" name="pull-every" checked={every === null} onChange={() => setPull({ ...pull, every: null })} />단추를 누를 때만</label>
        <label className="hint"><input type="radio" name="pull-every" checked={every !== null} onChange={() => setPull({ ...pull, every: { n: c.min_every, unit: "s" } })} />주기마다</label>
        {every !== null && <>
          <input type="number" min={1} step={1} value={every.n} aria-label="갱신 주기 숫자" className="w-[5rem]" onChange={e => setPull({ ...pull, every: { ...every, n: e.target.valueAsNumber } })} />
          <select value={every.unit} aria-label="갱신 주기 단위" onChange={e => setPull({ ...pull, every: { ...every, unit: e.target.value as PullUnit } })}>
            {(Object.entries(c.unit_names) as [PullUnit, string][]).map(([k, name]) => <option key={k} value={k}>{name}</option>)}
          </select>
        </>}
      </div>
      {pull.calc === "db" && dbMin > 0 && <div className="hint m-0">DB에서 계산할 때는 이 연결에서 {minimumText(dbMin)} 이상이에요.</div>}
      <div className="row" role="radiogroup" aria-label="받는 범위">
        {c.ranges.map(k => (
          <label key={k} className="hint"><input type="radio" name="pull-range" checked={pull.range === k} onChange={() => setPull({ ...pull, range: k })} />
            {k === "all" ? "전부 다시 받기" : "없는 것만 받기"}</label>
        ))}
      </div>
      {pull.range === "new" && <>
        <div className="hint m-0">새로 생긴 줄과 아직 받지 않은 기간만 받아요. 고쳐지거나 지워진 줄은 [전체 갱신]으로 받아요.</div>
        {!connected && <div className="hint m-0">{NO_TABLES}</div>}
        {marks.map(t => (
          <div className="row" key={`${t.schema}.${t.table}`}>
            <label className="hint">{t.table} 새 줄 컬럼
              <select value={pull.marks.find(m => m.schema === t.schema && m.table === t.table)?.column ?? ""} aria-label={`${t.table} 새 줄 컬럼`} onChange={e => setMark(t, e.target.value)}>
                <option value="">고르세요</option>
                {t.columns.map(col => <option key={col} value={col}>{col}</option>)}
              </select></label>
          </div>
        ))}
      </>}
      <div className="row"><button className="btn" onClick={full} disabled={busy || !connected}>전체 갱신</button></div>
      <div className="row" role="radiogroup" aria-label="암호화">
        {c.crypts.map(k => (
          <label key={k} className="hint"><input type="radio" name="pull-crypt" checked={pull.crypt === k} onChange={() => setPull({ ...pull, crypt: k })} />{CRYPT_NAMES[k]}</label>
        ))}
      </div>
      {pull.crypt === "pass" && <>
        <div className="row"><input type="password" autoComplete="off" value={pass} aria-label="암호문" onChange={e => setPass(e.target.value)} /></div>
        <div className="hint m-0">암호문은 길게 적어 주세요. 이 PC·이 계정만 풀 수 있게 보관해요. 다른 PC에서는 같은 암호문을 다시 적으면 받은 데이터를 읽어요.</div>
      </>}
      {(held?.slots ?? []).filter(s => s.lost).map(s => (
        <div className="row" key={s.slot}>
          <span className="hint m-0">{`'${s.table}' 마지막으로 받은 줄을 찾지 못했어요.`}</span>
          {s.lost === "-" ? <span className="hint m-0">기간 컬럼이 하나가 아니라 [전체 갱신]만 돼요.</span> : <>
            <input type="date" aria-label="다시 받을 시작일" value={spans[s.slot]?.start ?? ""} onChange={e => setSpans({ ...spans, [s.slot]: { start: e.target.value, end: spans[s.slot]?.end ?? "" } })} />
            <input type="date" aria-label="다시 받을 마지막 날" value={spans[s.slot]?.end ?? ""} onChange={e => setSpans({ ...spans, [s.slot]: { start: spans[s.slot]?.start ?? "", end: e.target.value } })} />
            <button className="btn" onClick={() => refetch(s.slot)} disabled={busy || !connected}>이 기간 다시 받기</button>
          </>}
        </div>
      ))}
      <div className="hint m-0">받은 데이터 {held?.text ?? ""} 이 PC의 설정 폴더(dash_data)에 두어요. 다른 대시보드와 같이 쓰는 파일도 들어가요.</div>
      <div className="row"><button className="btn p" onClick={save} disabled={busy}>저장</button></div>
      <div className={msg.ok ? "hint" : "err"} role="status">{msg.text}</div>
      {askCrypt && (
        <div className="modal" onClick={e => { if (e.target === e.currentTarget) setAskCrypt(false); }} onKeyDown={e => { if (e.key === "Escape") { e.preventDefault(); setAskCrypt(false); } }}>
          <div className="modal-box" role="dialog" aria-modal="true" aria-label={ASK_CRYPT}>
            <h3>{ASK_CRYPT}</h3>
            <div className="row">
              <button className="btn" onClick={() => commit("pull")} autoFocus>다시 받기</button>
              <button className="btn" onClick={() => commit("rewrite")}>풀어서 다시 쓰기</button>
              <button className="ghost" onClick={() => setAskCrypt(false)}>취소</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
