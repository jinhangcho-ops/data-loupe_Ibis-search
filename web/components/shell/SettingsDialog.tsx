"use client";
// N4 — 설정 창(옛 #d-modal .settings-box): 기준일·기간·주기·실시간(PeriodBar), 카드 색(팔레트), 목표 파일(문제 줄 쪽 넘김), 내 템플릿(N6 Templates), 이름 바꾸기·지우기(두 번 누르기).
// 기간 칸은 바꾸면 그 항목만 저장한다(ShellProvider). 팔레트는 dispatch({type:"meta"})(dirty → 자동 저장 N6), 이름은 바로 저장한다(목록 이름도 바뀐다).
import { useEffect, useState } from "react";
import { Templates } from "@/components/edit/Templates";
import { deleteDashboard, getTargets, saveDashboard, saveViz, setTargets, viz } from "@/lib/api";
import { type SemanticWords, setWords } from "@/lib/autoviz";
import { HEX } from "@/lib/calc";
import { useConstants } from "@/lib/constants";
import { useDash } from "@/lib/store";
import type { Constants, Dashboard, Targets } from "@/lib/types";
import { PeriodBar } from "./PeriodBar";
import { useShell } from "./ShellProvider";

// 낱말 목록 이름(viz_rules.WORD_LISTS)과 보이는 이름
const WORD_LISTS: [keyof SemanticWords, string][] = [["good", "긍정"], ["bad", "부정"], ["other", "기타"], ["null", "결측"]];

export type SettingsDialogProps = { open: boolean; onClose: () => void };

export function SettingsDialog({ open, onClose }: SettingsDialogProps) {
  const { state } = useDash();
  if (!open) return null;
  return (
    <div className="modal" onClick={e => { if (e.target === e.currentTarget) onClose(); }} onKeyDown={e => { if (e.key === "Escape") { e.preventDefault(); onClose(); } }}>
      <div className="modal-box settings-box" role="dialog" aria-modal="true" aria-labelledby="d-set-title">
        <div className="head"><h3 id="d-set-title">대시보드 설정</h3><span className="hint" style={{ margin: 0 }}>{state.dash?.name ?? ""}</span>
          <button className="ib" title="닫기" aria-label="닫기" style={{ marginLeft: "auto" }} onClick={onClose} autoFocus>×</button></div>
        {state.dash ? <Body dash={state.dash} onClose={onClose} /> : <p className="hint">대시보드를 먼저 고르거나 새로 만드세요.</p>}
      </div>
    </div>
  );
}

// 창을 열 때마다 새로 그린다(칸을 저장된 값으로 채운다)
function Body({ dash, onClose }: { dash: Dashboard; onClose: () => void }) {
  const { dispatch } = useDash();
  const consts = useConstants();
  const { period, open, flush } = useShell();
  const [palette, setPalette] = useState(dash.palette.join(", "));
  const [palErr, setPalErr] = useState("");
  const [path, setPath] = useState("");
  const [targets, setT] = useState<Targets | null>(null);
  const [tNote, setTNote] = useState("");
  const [showErrs, setShowErrs] = useState(false);
  const [name, setName] = useState(dash.name);
  const [msg, setMsg] = useState<{ text: string; ok?: boolean }>({ text: "" });
  const [sure, setSure] = useState(false);  // 지우기는 두 번 눌러야 한다(창에서 확인 상자가 안 뜰 수 있어 버튼 글자로 묻는다)
  const [busy, setBusy] = useState(false);
  const [prefs, setPrefs] = useState<Constants["dash"]["viz"] | null>(null);
  const [wordText, setWordText] = useState<Record<string, string>>({});
  const [wordMsg, setWordMsg] = useState<{ text: string; ok?: boolean }>({ text: "" });
  useEffect(() => { getTargets().then(t => { setT(t); setPath(t.path); }).catch(e => setTNote((e as Error).message)); }, []);
  useEffect(() => {
    viz().then(p => { setPrefs(p); setWordText(Object.fromEntries(WORD_LISTS.map(([k]) => [k, p.words[k].join("\n")]))); }).catch(e => setWordMsg({ text: (e as Error).message }));
  }, []);
  // 낱말은 한 줄에 하나. 빈 줄은 건너뛰고, 공백·겹침·길이는 서버가 검사해 그 문장을 보인다
  const saveWords = async () => {
    if (!prefs) return;
    setBusy(true);
    try {
      const words = Object.fromEntries(WORD_LISTS.map(([k]) => [k, wordText[k].split("\n").filter(w => w !== "")])) as unknown as SemanticWords;
      const saved = await saveViz({ ...prefs, words });
      setPrefs(saved);
      setWordText(Object.fromEntries(WORD_LISTS.map(([k]) => [k, saved.words[k].join("\n")])));
      setWords(saved.words);
      setWordMsg({ text: "차트 낱말을 저장했어요.", ok: true });
    } catch (e) { setWordMsg({ text: (e as Error).message }); } finally { setBusy(false); }
  };

  // 팔레트: HEX를 쉼표로 나눠 적는다. 틀린 색이 있으면 이유를 보이고 이전 팔레트를 그대로 둔다
  const applyPalette = (found: string[]) => {
    const bad = found.filter(c => !HEX.test(c)), max = consts?.dash.max_palette ?? 20;
    if (bad.length || found.length > max) return setPalErr(bad.length ? `색 코드가 아니에요: ${bad.join(", ")}. #11706f처럼 적어 주세요. 이전 색은 그대로예요.` : `색은 ${max}개까지 넣을 수 있어요. 이전 색은 그대로예요.`);
    setPalErr("");
    setPalette(found.join(", "));
    dispatch({ type: "meta", patch: { palette: found } });
  };
  const saveTargets = async () => {
    setBusy(true);
    try { const t = await setTargets(path.trim()); setT(t); setTNote(""); } catch (e) { setTNote((e as Error).message); } finally { setBusy(false); }
  };
  const rename = async () => {
    setBusy(true);
    try {
      await flush();
      const d = await saveDashboard({ ...dash, name: name.trim() });
      dispatch({ type: "list", list: d });
      dispatch({ type: "saved", dash: d.dashboard });
      setMsg({ text: "이름을 바꿨어요.", ok: true });
    } catch (e) { setMsg({ text: (e as Error).message }); } finally { setBusy(false); }
  };
  const remove = async () => {
    if (!sure) return setSure(true);
    setSure(false);
    setBusy(true);
    const id = dash.id;
    dispatch({ type: "open", dash: null });  // 지운 뒤 자동 저장이 이 대시보드를 되살리지 않게 먼저 닫는다
    try {
      const left = await deleteDashboard(id);
      dispatch({ type: "list", list: left });
      onClose();
      await open(left.dashboards.some(d => d.id === left.last) ? left.last : left.dashboards[0]?.id ?? "");
    } catch (e) { setMsg({ text: (e as Error).message }); await open(id); } finally { setBusy(false); }
  };
  const t = targets, tText = tNote || (!t ? "" : t.error || !t.path ? t.error || "목표 파일을 정하면 카드가 이름으로 목표를 찾을 수 있어요"
    : `목표 ${t.rows.length}개를 읽었어요.` + (t.error_total ? ` 읽지 못한 줄이 ${t.error_total}개 있어요(${(t.errors[0] as { message?: string })?.message ?? ""}). ` : ""));
  return (
    <>
      <PeriodBar />
      <div className="err" role="alert">{period.settingsError}</div>
      <h4>카드 색</h4>
      <div className="row">
        <input value={palette} onChange={e => setPalette(e.target.value)} placeholder="#11706f, #e8590c" aria-label="카드 색"
          title="색 코드를 쉼표로 나눠 적어요. 카드에서 따로 정한 색이 먼저 쓰이고, 비워 두면 기본 색을 써요" />
        <button className="btn" onClick={() => applyPalette(palette.split(/[\s,]+/).filter(Boolean))}>적용</button>
        <button className="ghost" onClick={() => applyPalette(consts?.dash.palette ?? [])}>기본 색 넣기</button>
        <button className="ghost" onClick={() => applyPalette([])}>비우기</button>
      </div>
      <div className="err">{palErr}</div>
      <h4>목표 파일</h4>
      <div className="row">
        <input value={path} onChange={e => setPath(e.target.value)} placeholder="목표 파일 경로" aria-label="목표 파일 경로" title="xlsx, csv, yaml, json, md, txt 파일을 읽을 수 있어요" />
        <button className="btn" onClick={saveTargets} disabled={busy}>저장</button>
      </div>
      <div className="hint" style={{ margin: 0 }}>{tText}{!tNote && t?.error_total ? <button className="linkish" onClick={() => setShowErrs(true)}>보기</button> : null}</div>
      {showErrs && <TargetErrors onClose={() => setShowErrs(false)} />}
      <h4>차트 낱말</h4>
      <div className="hint" style={{ margin: 0 }}>한 줄에 낱말 하나. 긍정·부정은 이름에 들어 있으면, 기타·결측은 이름 전체가 같으면 맞아요.</div>
      <div className="row">
        {WORD_LISTS.map(([k, label]) => (
          <label key={k}>{label}
            <textarea rows={4} value={wordText[k] ?? ""} onChange={e => setWordText({ ...wordText, [k]: e.target.value })} disabled={!prefs} aria-label={`${label} 낱말`} />
          </label>
        ))}
        <button className="btn" onClick={saveWords} disabled={busy || !prefs}>저장</button>
      </div>
      <div className={wordMsg.ok ? "hint" : "err"} role="status">{wordMsg.text}</div>
      <h4>내 템플릿</h4>
      <Templates />
      <h4>이름 바꾸기와 지우기</h4>
      <div className="row">
        <input value={name} onChange={e => setName(e.target.value)} aria-label="대시보드 이름" />
        <button className="btn" onClick={rename} disabled={busy}>이름 바꾸기</button>
        <button className="ghost" style={{ color: "var(--err)" }} onClick={remove} onBlur={() => setSure(false)} disabled={busy}>{sure ? "한 번 더 누르면 지워져요" : "대시보드 지우기"}</button>
      </div>
      <div className={msg.ok ? "hint" : "err"} role="status">{msg.text}</div>
      <div className="hint" style={{ margin: 0 }} title="연결 계정과 비밀번호는 저장하지 않아요">카드 값은 이 PC의 이 계정에서만 열 수 있게 암호화해 저장해요.</div>
    </>
  );
}

// 목표 파일의 읽지 못한 줄: 줄 번호·이유를 쪽마다 넘겨 본다(옛 targetErrors)
function TargetErrors({ onClose }: { onClose: () => void }) {
  const [offset, setOffset] = useState(0);
  const [t, setT] = useState<Targets | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { getTargets(offset).then(d => { setT(d); setErr(""); }).catch(e => setErr((e as Error).message)); }, [offset]);
  const errs = (t?.errors ?? []) as { message?: string }[];
  return (
    <div className="modal" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal-box" role="dialog" aria-modal="true" aria-label="읽지 못한 줄">
        <h3>읽지 못한 줄</h3>
        <div style={{ whiteSpace: "pre-wrap", margin: "10px 0 14px" }}>{err || errs.map((x, i) => <div key={i}>{x.message}</div>)}</div>
        <div className="row">
          <button className="btn" disabled={!t || offset <= 0} onClick={() => setOffset(Math.max(0, offset - (t?.limit ?? 0)))}>이전</button>
          <button className="btn" disabled={!t || offset + t.limit >= t.error_total} onClick={() => setOffset(offset + (t?.limit ?? 0))}>다음</button>
          <span className="hint" style={{ margin: 0 }}>{t ? (errs.length ? `${offset + 1}~${offset + errs.length} / ${t.error_total}` : `0 / ${t.error_total}`) : ""}</span>
          <button className="btn" style={{ marginLeft: "auto" }} onClick={onClose}>닫기</button>
        </div>
      </div>
    </div>
  );
}
