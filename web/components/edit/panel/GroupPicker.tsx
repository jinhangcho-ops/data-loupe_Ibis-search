"use client";
// (Phase 4) 카드 설정 패널의 [표시할 그룹] 접기 칸: 보일 그룹·계열 체크(끌어 고르기), 범위, 범위 묶기, 지도 영역 묶음, 이름 바꾸기를 view.groups로 고친다.
import { useRef, useState } from "react";
import { values as fetchValues } from "@/lib/api";
import { useConstants } from "@/lib/constants";
import { dragPick, dragRange, groupNames, rangeName } from "@/lib/groups";
import type { GroupView, RangeEnd } from "@/lib/groups";
import type { Card, CardData } from "@/lib/types";
import { Check } from "../menu/Fields";

// 목록 이름이 이보다 많으면 위에 찾기 칸(쿼리 화면 common.js의 MENU_FIND와 같은 값)
const GROUP_FIND = 8;
const FIND_WAIT = 300;  // 그룹 찾아 더하기: 입력이 멈춘 뒤 기다리는 ms(쿼리 화면 common.js의 TYPE_WAIT와 같은 값)

type ListKind = "names" | "series";
type Drag = { list: ListKind; from: number; to: number };

export type GroupPickerProps = {
  card: Card;
  data: CardData;
  onChange: (patch: Partial<Card>) => void;
  recompute: (next: Card) => Promise<void>;
};

// [SPECIFICATION] plans/phase-4.md#GroupPicker
// view.groups를 고치는 [표시할 그룹] 접기 칸을 그린다(서버 묶기만 recompute를 부른다).
export function GroupPicker({ card, data, onChange, recompute }: GroupPickerProps) {
  const consts = useConstants();
  const [open, setOpen] = useState(false);
  const [find, setFind] = useState<Record<ListKind, string>>({ names: "", series: "" });
  const [drag, setDrag] = useState<Drag | null>(null);
  const [pick, setPick] = useState<string[]>([]);
  const [setName, setSetName] = useState("");
  const [error, setError] = useState("");
  const [hunt, setHunt] = useState<{ q: string; values: string[]; more: boolean; error: string }>({ q: "", values: [], more: false, error: "" });
  const huntSeq = useRef(0);
  const huntTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const g: GroupView = (card.view as unknown as { groups?: GroupView }).groups ?? {};
  const { names: got, series, ordered } = groupNames(card, data);
  const served = card.kind === "chart" && data.x_kind !== "date";  // (Phase 18) 서버가 X를 고른 그룹·숫자 범위로 먼저 거르는 카드(dash_calc.server_picks)
  const names = served ? [...new Set([...got, ...(g.keep ?? [])])] : got;  // 받은 값에 없는 고른 그룹도 보여 끌 수 있게
  if (names.length === 0 && series.length === 0 && ordered === null) return null;

  const ranges = g.ranges ?? [];

  // 빈 배열·빈 객체·merge false를 지우고, 남은 것이 없으면 view에서 groups 키를 지운다
  const update = async (next: GroupView, again: boolean) => {
    const groups = Object.fromEntries(
      Object.entries(next).filter(([, v]) =>
        v !== undefined && v !== false && !(Array.isArray(v) ? v.length === 0 : typeof v === "object" && v !== null && Object.keys(v).length === 0),
      ),
    ) as GroupView;
    const rest: Record<string, unknown> = { ...card.view };
    delete rest.groups;
    const view = (Object.keys(groups).length ? { ...rest, groups } : rest) as Card["view"];
    onChange({ view });
    if (!again) return;
    setError("");
    try {
      await recompute({ ...card, view });
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const look = (q: string) => {
    clearTimeout(huntTimer.current);
    const mine = ++huntSeq.current;  // 늦게 온 앞 응답은 버린다
    fetchValues({ ...(card.source.body ?? {}), q }, String(card.source.body?.x ?? "")).then(
      (r) => { if (mine === huntSeq.current) setHunt({ q, values: r.values, more: r.more, error: "" }); },
      (e) => { if (mine === huntSeq.current) setHunt({ q, values: [], more: false, error: (e as Error).message }); },
    );
  };
  const flip = (cur: string[] | undefined, picked: string[]) => {
    const now = cur ?? [];
    return picked.every((p) => now.includes(p)) ? now.filter((x) => !picked.includes(x)) : [...now, ...picked.filter((p) => !now.includes(p))];
  };
  const toggle = (list: ListKind, picked: string[]) =>
    update(list === "names" ? { ...g, keep: flip(g.keep, picked) } : { ...g, series: flip(g.series, picked) }, list === "names" && served);
  const setRanges = (next: NonNullable<GroupView["ranges"]>) => update({ ...g, ranges: next }, !!g.merge || (served && ordered === "number"));
  const end = (v: string): RangeEnd => (v === "" ? null : ordered === "number" ? +v : v);
  const editRange = (k: number, side: number, v: RangeEnd) =>
    setRanges(ranges.map((x, m): [RangeEnd, RangeEnd] => (m !== k ? [x[0], x[1]] : side ? [x[0], v] : [v, x[1]])));
  const finish = () => {
    if (!drag) return;
    const all = drag.list === "names" ? names : series;
    setDrag(null);
    if (drag.from === drag.to) toggle(drag.list, [all[drag.from]]);
    else if (drag.list === "names" && ordered) setRanges([...ranges, dragRange(names, drag.from, drag.to, ordered)]);
    else toggle(drag.list, dragPick(all, drag.from, drag.to));
  };
  const rename = (name: string, v: string) => {
    const r = { ...(g.rename ?? {}) };
    if (v) r[name] = v;
    else delete r[name];
    update({ ...g, rename: r }, false);
  };

  // 체크 목록: data-i는 거르기 전 번호(dragPick·dragRange가 받는 차례)
  const rows = (list: ListKind, all: string[], picked: string[] | undefined) => {
    const q = find[list];
    const span = drag?.list === list ? [Math.min(drag.from, drag.to), Math.max(drag.from, drag.to)] : null;
    return (
      <div>
        {all.length > GROUP_FIND && (
          <input
            aria-label={list === "names" ? "그룹 찾기" : "계열 찾기"}
            placeholder="찾기"
            value={q}
            onChange={(e) => setFind({ ...find, [list]: e.target.value })}
          />
        )}
        {all.map(
          (name, i) =>
            name.includes(q) && (
              <div
                key={i}
                className={span && i >= span[0] && i <= span[1] ? "brow on" : "brow"}
                data-list={list}
                data-i={i}
                onPointerDown={(e) => {
                  if (e.button !== 0) return;
                  e.currentTarget.setPointerCapture(e.pointerId);
                  setDrag({ list, from: i, to: i });
                }}
                onPointerMove={(e) => {
                  if (drag?.list !== list) return;
                  // 잡은 칸이 이벤트를 받으므로 지나는 칸은 좌표로 찾는다
                  const el = document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>(`[data-list="${list}"]`);
                  if (el) setDrag({ ...drag, to: Number(el.dataset.i) });
                }}
                onPointerUp={finish}
                onClickCapture={(e) => {
                  // 마우스 누름은 pointerup이 처리한다(키보드 누름은 detail 0이라 Check가 처리)
                  if (e.detail) e.preventDefault();
                }}
              >
                <Check label={name} checked={!!picked?.includes(name)} onChange={() => toggle(list, [name])} />
              </div>
            ),
        )}
      </div>
    );
  };

  const renamable = [
    ...new Set([...names, ...series, ...(g.merge ? ranges.map(rangeName) : []), ...Object.keys(g.sets ?? {})]),
  ];

  return (
    <>
      <div className="btools">
        <button type="button" className={`btype${open ? " on" : ""}`} aria-expanded={open} onClick={() => setOpen(!open)}>
          표시할 그룹
        </button>
      </div>
      {open && (
        <div className="bcolors">
          {served && (
            <div>
              <input aria-label="그룹 찾아 더하기" placeholder="그룹 찾아 더하기" value={hunt.q}
                onChange={(e) => { const q = e.target.value; setHunt({ ...hunt, q }); clearTimeout(huntTimer.current); huntTimer.current = setTimeout(() => look(q.trim()), FIND_WAIT); }}
                onKeyDown={(e) => { if (e.key !== "Enter" || e.nativeEvent.isComposing) return; e.preventDefault(); look(hunt.q.trim()); }} />
              {hunt.values.map((v) => (
                <button key={v} type="button" onClick={() => update({ ...g, keep: [...new Set([...(g.keep ?? []), v])] }, true)}>{v}</button>
              ))}
              {hunt.more && <div className="hint">더 있어요. 더 좁혀 찾으세요.</div>}
              {hunt.error && <div className="hint" role="status">{hunt.error}</div>}
            </div>
          )}
          {names.length > 0 && rows("names", names, g.keep)}
          {series.length > 0 && rows("series", series, g.series)}
          {ordered && (
            <div>
              {ranges.map((r, k) => (
                <div key={k} className="brow">
                  {[0, 1].map((side) => (
                    <input
                      key={side}
                      type={ordered === "number" ? "number" : "date"}
                      aria-label={side ? "끝" : "시작"}
                      value={r[side] ?? ""}
                      onChange={(e) => editRange(k, side, end(e.target.value))}
                    />
                  ))}
                  <button type="button" onClick={() => setRanges(ranges.filter((_, m) => m !== k))}>
                    빼기
                  </button>
                </div>
              ))}
              <button type="button" onClick={() => setRanges([...ranges, [null, null]])}>
                범위 더하기
              </button>
            </div>
          )}
          {consts && card.kind === "chart" && data.x_kind === "num" && (
            <div>
              <Check label="범위를 한 그룹으로 묶기" checked={!!g.merge} onChange={() => update({ ...g, merge: !g.merge }, true)} />
              <div className="hint">{consts.dash.merge_note}</div>
            </div>
          )}
          {card.kind === "geomap" && (
            <div>
              {names.map((name) => (
                <Check key={name} label={name} checked={pick.includes(name)} onChange={() => setPick(flip(pick, [name]))} />
              ))}
              <input aria-label="묶음 이름" value={setName} onChange={(e) => setSetName(e.target.value)} />
              <button
                type="button"
                onClick={() => {
                  update({ ...g, sets: { ...(g.sets ?? {}), [setName]: pick } }, false);
                  setPick([]);
                  setSetName("");
                }}
              >
                묶음 만들기
              </button>
              {Object.entries(g.sets ?? {}).map(([k, v]) => (
                <div key={k} className="brow">
                  {k}: {v.join(", ")}
                  <button
                    type="button"
                    onClick={() => {
                      const s = { ...(g.sets ?? {}) };
                      delete s[k];
                      update({ ...g, sets: s }, false);
                    }}
                  >
                    풀기
                  </button>
                </div>
              ))}
            </div>
          )}
          <div>
            {renamable.map((name) => (
              <label key={name} className="brow">
                {name}
                <input aria-label={`${name} 이름 바꾸기`} value={g.rename?.[name] ?? ""} onChange={(e) => rename(name, e.target.value)} />
              </label>
            ))}
          </div>
          {error && (
            <div className="hint" role="status">
              {error}
            </div>
          )}
        </div>
      )}
    </>
  );
}
