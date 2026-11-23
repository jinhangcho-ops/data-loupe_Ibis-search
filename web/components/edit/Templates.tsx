"use client";
// N6·B7 — 템플릿: 직접 구성 + 기본 템플릿(서버 templates/ 폴더) + 내 템플릿(서버 dash_templates.json). 저장·파일로 내보내기·파일에서 가져오기·[더 받기](누를 때만 GitHub).
import { type ChangeEvent, useEffect, useRef, useState } from "react";
import { deleteTemplate, exportTemplate, fetchTemplates, importTemplate, listTemplates, remoteTemplates, saveTemplate } from "@/lib/api";
import { useDash } from "@/lib/store";
import { BLANK, FIRST_PICK, allTemplates } from "@/lib/templates";
import type { RemoteTemplate, Template } from "@/lib/types";

const SAVE = "내 템플릿으로 저장";
const SAVE_TIP = "종류, 자리, 이름, 색과 모양 설정만 저장해요. 컬럼, 값, 연결, 조건은 담지 않아요";
const SAVED = "내 템플릿으로 저장했어요. 종류, 자리, 이름, 색과 모양 설정만 담겼어요.";
const NO_DASH = "대시보드를 연 뒤 저장할 수 있어요.";
const NAME_HINT = "템플릿 이름";
const PICK_TIP = "시작 모양. 카드 자리만 놓이고, 데이터는 카드마다 정해요";
const IMPORT = "파일에서 가져오기";
const IMPORTED = "파일의 템플릿을 내 템플릿에 넣었어요.";
const EXPORT = "파일로 내보내기";
const EXPORTED = "템플릿 파일을 저장했어요:";
const MORE = "더 받기";
const MORE_TIP = "누를 때만 GitHub 저장소의 템플릿 목록을 받아요. 보내는 것은 주소 요청뿐이에요";
const REMOTE_PICK = "받을 템플릿을 고르세요.";
const REMOTE_EMPTY = "받을 수 있는 템플릿이 없어요.";
const FETCH = "고른 것 받기";
const FETCHED = "받은 템플릿을 내 템플릿에 넣었어요.";
const DROP = "지우기";
export const UNOPENED = "이 버전에서 못 여는 요소";
const SUFFIX = ".json"; // 서버 dash_store.TEMPLATE_SUFFIX
const PIECES = (n: number) => `조각 ${n}개`;

export type TemplatesProps = { onPick?: (t: Template) => void };

/** 카드 id(옛 randomId: 16자 16진수) */
export const newCardId = () => [...crypto.getRandomValues(new Uint8Array(8))].map(b => b.toString(16).padStart(2, "0")).join("");

export function Templates({ onPick }: TemplatesProps) {
  const { state } = useDash();
  const [mine, setMine] = useState<Template[]>([]);
  const [builtin, setBuiltin] = useState<Template[]>([]);
  const [picked, setPicked] = useState(FIRST_PICK);
  const [name, setName] = useState("");
  const [note, setNote] = useState({ text: "", ok: false });
  const [busy, setBusy] = useState(false);
  const [remote, setRemote] = useState<RemoteTemplate[] | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const file = useRef<HTMLInputElement>(null);
  const all = allTemplates(builtin, mine);
  useEffect(() => { listTemplates().then(d => { setMine(d.templates); setBuiltin(d.builtin); }).catch(e => setNote({ text: (e as Error).message, ok: false })); }, []);
  // 기본 템플릿이 오면 그때 고른 것을 알린다
  useEffect(() => { onPick?.(all.find(t => t.id === picked) ?? BLANK); }, [builtin]);  // eslint-disable-line react-hooks/exhaustive-deps
  const run = async (work: () => Promise<string>) => { setBusy(true); try { setNote({ text: await work(), ok: true }); } catch (e) { setNote({ text: (e as Error).message, ok: false }); } finally { setBusy(false); } };
  const save = () => run(async () => {
    if (!state.dash) throw new Error(NO_DASH);
    setMine((await saveTemplate(name, { pages: state.dash.pages ?? [], sections: state.dash.sections ?? [], cards: state.dash.cards.map(c => ({ ...c, source: {}, data: {} })) })).templates);
    return SAVED;
  });
  const pickFile = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]; e.target.value = "";
    if (f) run(async () => { setMine((await importTemplate(await f.text())).templates); return IMPORTED; });
  };
  const more = () => run(async () => { const { files } = await remoteTemplates(); setRemote(files); setChosen([]); return files.length ? REMOTE_PICK : REMOTE_EMPTY; });
  const take = () => run(async () => { setMine((await fetchTemplates(chosen)).templates); setRemote(null); setChosen([]); return FETCHED; });
  const send = (id: string) => run(async () => { const { path } = await exportTemplate(id); return path ? `${EXPORTED} ${path}` : ""; });
  const drop = (id: string) => run(async () => { setMine((await deleteTemplate(id)).templates); return ""; });
  return (
    <div className="templates">
      {onPick && (
        <select value={picked} title={PICK_TIP} className="w-auto"
          onChange={e => { setPicked(e.target.value); const t = all.find(x => x.id === e.target.value); if (t) onPick(t); }}>
          {all.map(t => <option key={t.id} value={t.id} title={t.help}>{t.name}</option>)}
        </select>
      )}
      <div className="row">
        <input placeholder={NAME_HINT} value={name} onChange={e => setName(e.target.value)} />
        <button className="btn" title={SAVE_TIP} onClick={save} disabled={busy}>{SAVE}</button>
      </div>
      <div className="row">
        <button className="btn" onClick={() => file.current?.click()} disabled={busy}>{IMPORT}</button>
        <input ref={file} type="file" accept={SUFFIX} hidden onChange={pickFile} />
        <button className="btn" title={MORE_TIP} onClick={more} disabled={busy}>{MORE}</button>
      </div>
      {remote && remote.length > 0 && (
        <div>
          {remote.map(f => (
            <label key={f.file} className="row">
              <input type="checkbox" checked={chosen.includes(f.file)} onChange={e => setChosen(e.target.checked ? [...chosen, f.file] : chosen.filter(x => x !== f.file))} />
              <span className="hint flex-1">{f.file.slice(0, -SUFFIX.length)}</span>
            </label>
          ))}
          <button className="btn p" onClick={take} disabled={busy || chosen.length === 0}>{FETCH}</button>
        </div>
      )}
      <div className={`${note.ok ? "hint" : "err"} m-0`}>{note.text}</div>
      {mine.map(t => (
        <div key={t.id} className="row">
          <span className="hint flex-1">{t.name} ({PIECES(t.pieces.length)})</span>
          {t.unknown.length > 0 && <span className="err m-0">{UNOPENED}: {t.unknown.join(", ")}</span>}
          <button className="ghost" onClick={() => send(t.id)} disabled={busy}>{EXPORT}</button>
          <button className="ghost" onClick={() => drop(t.id)} disabled={busy}>{DROP}</button>
        </div>
      ))}
    </div>
  );
}
