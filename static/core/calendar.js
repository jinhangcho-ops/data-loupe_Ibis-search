// 기간 달력(P1): 두 달을 나란히 보이고, 첫 클릭은 시작 날·둘째 클릭은 끝 날(앞 날을 누르면 둘을 바꾼다). min~max 밖 날은 못 고른다.
// Calendar.open({ anchor, min, max, start, end, onPick(start, end) }) → { el, close }. 날짜는 "YYYY-MM-DD"(시각이 붙어 와도 앞 10자만 본다).
// 처음 보이는 달은 max의 달(오른쪽, 없으면 오늘). 키보드: 화살표로 날 이동, Enter 고르기, Esc 닫기. 모양은 우클릭 메뉴처럼 .drop .pop을 빌려 쓴다.
// common.js의 el이 먼저 불려 있어야 한다. 대시보드(web/components/shell/Calendar.tsx)도 같은 규칙·같은 클래스를 쓴다.
const Calendar = (() => {
  const DAY = 864e5, WEEK = ["일", "월", "화", "수", "목", "금", "토"];
  const ymd = v => v ? String(v).slice(0, 10) : "";
  const at = s => new Date(s + "T00:00:00Z");  // UTC로 다뤄 PC 시간대·서머타임에 날이 밀리지 않게
  const iso = d => d.toISOString().slice(0, 10);
  const addDays = (s, n) => iso(new Date(at(s).getTime() + n * DAY));
  const monthOf = s => s.slice(0, 7);
  const addMonths = (m, n) => { const k = +m.slice(0, 4) * 12 + +m.slice(5, 7) - 1 + n; return `${String(Math.floor(k / 12)).padStart(4, "0")}-${String(k % 12 + 1).padStart(2, "0")}`; };
  const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
  const korean = s => `${+s.slice(0, 4)}년 ${+s.slice(5, 7)}월${s.length > 7 ? ` ${+s.slice(8, 10)}일` : ""}`;
  let shown = null;  // 지금 열린 달력(하나만)

  function open({ anchor, min, max, start, end, onPick }) {
    shown?.close();
    min = ymd(min); max = ymd(max);
    const clamp = s => min && s < min ? min : max && s > max ? max : s;
    const st = { start: ymd(start), end: ymd(end), month: addMonths(monthOf(max || today()), -1), focus: clamp(ymd(start) || max || today()) };
    const visible = d => monthOf(d) >= st.month && monthOf(d) <= addMonths(st.month, 1);
    if (!visible(st.focus)) st.focus = clamp(max || today());  // 키보드로 들어갈 날(tabindex=0)은 보이는 두 달 안에 있어야 한다
    const prev = el("button", { type: "button", className: "ghost cal-nav", textContent: "‹", title: "이전 달" });
    const next = el("button", { type: "button", className: "ghost cal-nav", textContent: "›", title: "다음 달" });
    const months = el("div", { className: "cal-months" }), note = el("div", { className: "hint cal-note" });
    const pop = el("div", { className: "pop cal" }, el("div", { className: "cal-head" }, prev, next), months, note), box = el("div", { className: "drop" }, pop);
    pop.setAttribute("role", "dialog"); pop.setAttribute("aria-label", "기간 고르기"); note.setAttribute("aria-live", "polite");
    prev.setAttribute("aria-label", "이전 달"); next.setAttribute("aria-label", "다음 달");
    const off = d => (min && d < min) || (max && d > max);
    function grid(m) {
      const first = at(m + "-01"), lead = first.getUTCDay(), days = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate(), now = today();
      let cells = "<tr>" + "<td></td>".repeat(lead);
      for (let n = 1; n <= days; n++) {
        const d = `${m}-${String(n).padStart(2, "0")}`, on = d === st.start || d === st.end, inside = st.start && st.end && d > st.start && d < st.end;
        const cls = ["cal-day", on && "on", inside && "in", d === now && "today"].filter(Boolean).join(" ");
        cells += `<td aria-selected="${on || !!inside}"><button type="button" class="${cls}" data-d="${d}" tabindex="${d === st.focus ? 0 : -1}" aria-label="${korean(d)}"${d === now ? ' aria-current="date"' : ""}${off(d) ? " disabled" : ""}>${n}</button></td>`;
        if ((lead + n) % 7 === 0 && n < days) cells += "</tr><tr>";
      }
      return `<table class="cal-grid" role="grid" aria-label="${korean(m)}"><caption>${korean(m)}</caption><thead><tr>${WEEK.map(w => `<th scope="col">${w}</th>`).join("")}</tr></thead><tbody>${cells}</tr></tbody></table>`;
    }
    function draw(focus) {
      months.innerHTML = grid(st.month) + grid(addMonths(st.month, 1));
      prev.disabled = !!min && st.month <= monthOf(min);
      next.disabled = !!max && addMonths(st.month, 1) >= monthOf(max);
      note.textContent = st.start && !st.end ? `끝 날을 고르세요(시작 ${st.start})` : "시작 날을 고르세요";
      if (focus) months.querySelector(`[data-d="${st.focus}"]`)?.focus();
    }
    function pick(d) {
      if (off(d)) return;
      st.focus = d;
      if (!st.start || st.end) { st.start = d; st.end = ""; draw(true); return; }
      [st.start, st.end] = d < st.start ? [d, st.start] : [st.start, d];
      draw();
      close();
      onPick?.(st.start, st.end);
    }
    function move(d) {  // 키보드로 날을 옮긴다. 보이는 두 달 밖이면 달을 넘긴다
      st.focus = clamp(d);
      const m = monthOf(st.focus);
      if (m < st.month) st.month = m; else if (m > addMonths(st.month, 1)) st.month = addMonths(m, -1);
      draw(true);
    }
    const turn = n => { st.month = addMonths(st.month, n); if (!visible(st.focus)) st.focus = clamp(st.month + "-01"); draw(); };  // 달을 넘겨도 키보드로 들어갈 날을 보이는 달에 둔다
    prev.onclick = () => turn(-1);
    next.onclick = () => turn(1);
    months.onclick = e => { const b = e.target.closest("button[data-d]"); if (b) pick(b.dataset.d); };
    pop.onkeydown = e => {
      const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[e.key];
      if (step && e.target.dataset?.d) { e.preventDefault(); move(addDays(e.target.dataset.d, step)); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); anchor?.focus?.(); }
    };
    const outside = e => { if (!box.contains(e.target) && e.target !== anchor) close(); };
    function close() {
      box.remove();
      document.removeEventListener("mousedown", outside, true);
      if (shown?.el === pop) shown = null;
    }
    box.style.cssText = "position:fixed;z-index:300;left:0;top:0";  // 붙이기 전에 fixed로: 블록(.drop)인 채로 재면 너비가 화면 너비가 되어 왼쪽 끝에 붙는다(B1)
    document.body.append(box);
    draw();
    const r = anchor?.getBoundingClientRect?.() ?? { left: 0, bottom: 0 }, w = box.getBoundingClientRect();  // 칸 아래, 화면 밖으로 나가지 않게
    box.style.cssText = `position:fixed;z-index:300;left:${Math.max(0, Math.min(r.left, innerWidth - w.width))}px;top:${Math.max(0, Math.min(r.bottom + 4, innerHeight - w.height))}px`;
    months.querySelector(`[data-d="${st.focus}"]`)?.focus();
    document.addEventListener("mousedown", outside, true);
    return shown = { el: pop, close };
  }
  return { open };
})();
