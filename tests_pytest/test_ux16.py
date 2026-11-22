"""Phase 16: 디자인 토큰·왼쪽 메뉴·설정에 보낼 곳 합치기·홈 최근 연결·카드 추가 서랍(plans/phase-16.md).
화면 글자·배치는 정적 검사, 화면 함수(openSettings·showRecentConnections·showHome)는 node로 돌려 본다.
기존 코드를 고쳐야 통과하는 시험은 xfail(strict)이다. 그 항목을 끝낸 쪽이 표시를 지운다."""
import re
import shutil

import pytest

from conftest import ROOT
from test_fix_audit_js import between, read, run_node
from test_ux15 import SETTING_IDS

WEB = ROOT / "web"
CSS_FILES = (WEB / "styles/common.css", WEB / "styles/dash_edit.css", WEB / "styles/sentences.css", WEB / "styles/dashboard.css")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

# 가짜 classList(Set): init에 든 클래스로 시작한다
CLS = "const cls = (...init) => { const s = new Set(init); return { add: c => s.add(c), remove: c => s.delete(c), toggle: (c, on) => (on ?? !s.has(c)) ? s.add(c) : s.delete(c), contains: c => s.has(c) }; };\n"


def web(path):
    return (WEB / path).read_text(encoding="utf-8")


def css_text(path):
    return path.read_text(encoding="utf-8")


# ---------- 16A 디자인 토큰 ----------

# 1rem = 16px 기준: 12·13·14·16·20·28px, 4·8·12·16·24·32px, 10·16·999px, 28·32·40px
TOKENS = {"--fs-xs": ".75rem", "--fs-sm": ".8125rem", "--fs-md": ".875rem", "--fs-lg": "1rem", "--fs-xl": "1.25rem", "--fs-2xl": "1.75rem",
          "--sp-1": ".25rem", "--sp-2": ".5rem", "--sp-3": ".75rem", "--sp-4": "1rem", "--sp-5": "1.5rem", "--sp-6": "2rem",
          "--r-ctl": ".625rem", "--r-card": "1rem", "--r-pill": "62.4375rem", "--h-sm": "1.75rem", "--h-md": "2rem", "--h-lg": "2.5rem"}


def root_vars(css, dark):
    pattern = r"^:root\[data-theme=dark\] \{([^}]*)\}" if dark else r"^:root \{([^}]*)\}"
    out = {}
    for block in re.findall(pattern, css, re.M):
        out.update((k, v.strip()) for k, v in re.findall(r"(--[\w-]+):\s*([^;]+);", block))
    return out


def luminance(hex_):
    def part(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    if len(hex_) == 4:  # #fff 줄임꼴
        hex_ = "#" + "".join(c * 2 for c in hex_[1:])
    r, g, b = (part(int(hex_[i:i + 2], 16) / 255) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def rule(css, selector):
    m = re.search(r"^" + re.escape(selector) + r" \{([^}]*)\}", css, re.M)
    assert m, selector
    return m.group(1)


def test_tokens_defined():
    css = read("common.css")
    light, dark = root_vars(css, False), root_vars(css, True)
    for k, v in TOKENS.items():
        assert light.get(k) == v, k
    assert light["--line-strong"] == "#858c91" and dark["--line-strong"] == "#6f7880"


def test_line_strong_contrast_on_cards():
    css = read("common.css")
    for dark in (False, True):
        v = root_vars(css, dark)
        assert contrast(v["--line-strong"], v["--card"]) >= 3.0, dark  # 입력 칸 테두리(비텍스트 대비 3:1)
    assert contrast(root_vars(css, False)["--line"], "#ffffff") < 3.0  # --line은 구분선에만


def test_inputs_and_buttons_use_tokens():
    css = read("common.css")
    assert "border:1px solid transparent" in rule(css, "input, select, textarea")  # 선 대신 눌린 면(neumorphism). 높이 계산의 2px는 그대로
    assert "box-shadow:var(--press)" in rule(css, ":where(input:not([type=checkbox], [type=radio], [type=range], [type=color]), select, textarea)")
    assert "height:var(--h-md)" in rule(css, ".btn")
    ib = rule(css, ".ib")
    assert "width:var(--h-md)" in ib and "height:var(--h-md)" in ib


def test_no_px_font_sizes_or_radius_literals():
    for path in CSS_FILES:
        css = css_text(path)
        assert not re.findall(r"font-size:\s*[\d.]+px", css), path.name
        assert not re.findall(r"\bfont:\s*[\d.]+px", css), path.name
        for v in re.findall(r"border-radius:\s*([^;}]+)", css):
            rest = re.sub(r"var\(--r-(?:ctl|card|pill|board)\)|\b0\b|50%", "", v).strip()
            assert not rest, (path.name, v)


FAINT_OK = {"common.css": {".crumb i", ".jobx.idle i", ".cn", ".drop .pop .cal-day:disabled"},
            "dash_edit.css": {".bresize", ".sresize"}, "sentences.css": set(), "dashboard.css": {".drop .pop .dcal-day:disabled"}}


def test_faint_only_on_decor():
    for path in CSS_FILES:
        got = {m.group(1).strip() for m in re.finditer(r"^([^{}\n]+)\{[^}\n]*var\(--faint\)", css_text(path), re.M)}
        assert got == FAINT_OK[path.name], path.name  # 글자에는 --muted, 점·꺼진 날짜·장식에만 --faint


def test_query_screen_text_not_faint():
    html = read("index.html")
    for sel in ("#sql-text::placeholder", "#rows th:not(.sorted, .pick, .addcol):hover::after", ":is(#sql-paint, #sql) .sc"):
        line = next(x for x in html.splitlines() if sel in x)
        part = line[line.index(sel):]
        assert "var(--muted)" in part.split("}")[0] and "var(--faint)" not in part.split("}")[0], sel


# ---------- 16B 왼쪽 메뉴 ----------

RAIL = [("/", "", "홈"), ("/search", "query", "쿼리"), ("/prep", "prep", "분석"), ("/dashboard", "dashboard", "대시보드"),
        ("/dashboard/build", "dashboard", "대시보드 만들기"), ("/collect", "collect", "수집"), ("/integrations", "integrations", "연동")]


def test_rail_markup_and_topbar_order():
    status = read("status.html")
    assert status.index('<nav class="rail"') < status.index('<header class="topbar">')
    rail = between("status.html", '<nav class="rail"', "</nav>")
    items = re.findall(r'<a href="([^"]+)"(?: data-feature="(\w+)")?[^>]*>.*?<span>([^<]+)</span></a>', rail)
    assert items == RAIL
    assert rail.index("Data Loupe") < rail.index('href="/"')
    for a in re.findall(r"<a [^>]*>(.*?)</a>", rail):
        assert "<svg" in a and 'stroke="currentColor"' in a, a
    foot = rail[rail.index('class="rail-foot"'):]
    assert foot.index('id="feat-open"') < foot.index('id="theme"') and ">설정</span>" in foot
    top = between("status.html", '<header class="topbar">', "</header>")
    at = [top.index(f'id="{i}"') for i in ("crumb", "usage", "job", "upd", "resize")]
    assert at == sorted(at)
    for gone in ('id="feat-open"', 'id="dest-open"', 'id="theme"', "<nav"):
        assert gone not in top, gone


def test_rail_css():
    css = read("common.css")
    assert "position:fixed" in rule(css, ".rail")
    assert "padding-left:var(--rail-w)" in rule(css, "body:has(.rail)")
    assert root_vars(css, False)["--rail-w"] == "12.5rem"  # 200px
    media = re.search(r"@media \(max-width:1099px\) \{(.*?)\n\}", css, re.S).group(1)
    assert ":root { --rail-w:3.5rem; }" in media and ".rail :is(a, .rail-btn, .rail-name) span { display:none; }" in media
    assert ".pages" not in css and ".rail a" in re.search(r"^(\.btn, \.ib[^{]*)\{", css, re.M).group(1)
    assert "min(var(--panel, clamp(18.75rem, 28vw, 32.5rem)), calc((100vw - var(--rail-w)) * .6))" in rule(css, ".work")


def test_rail_marks_current_and_crumb_everywhere():
    js = read("common.js")
    assert 'document.querySelectorAll(".rail a").forEach(a => a.classList.toggle("on", a.pathname === location.pathname));' in js
    assert ".pages a" not in js
    crumb = between("common.js", "function showCrumb() {", "\n}\n")
    assert '"hidden"' not in crumb  # 연결 칸이 없는 화면(수집)에서도 보인다
    block = between("common.js", "if (!hasConnect) {", "\n}\n")
    assert 'api("/api/state").then(s => { if (s.connected) { connName = connLabel(s.opened); showCrumb(); } });' in block
    assert '$("crumb").onclick = () => { location.href = "/"; };' in block


def test_topbar_tsx_matches_status():
    bar = web("components/shell/TopBar.tsx")
    assert '<nav className="rail" aria-label="화면">' in bar and 'className="rail-name"' in bar and "Data Loupe" in bar and 'className="rail-foot"' in bar
    assert 'PANEL_PARAM = "panel"' in bar and "href={`/?${PANEL_PARAM}=feat`}" in bar
    assert "보낼 곳 설정" not in bar and "PANELS" not in bar and 'className="pages"' not in bar
    assert 'id="crumb"' in bar and 'id="crumb-text"' in bar and 'id="theme"' in bar
    tabs = re.findall(r'\["(/[\w/]*)", "(\w*)", "([^"]+)", "[^"]*", \w+\]', bar)
    assert tabs == RAIL
    assert bar.index('<nav className="rail"') < bar.index('<header className="topbar">')


def test_dash_only_hides_rail():
    css = css_text(WEB / "styles/dashboard.css")
    line = next(x for x in css.splitlines() if x.startswith("body.dash-only :is("))
    assert ".rail" in line and ".topbar" in line
    assert "body.dash-only:has(.rail) { padding-left:0; }" in css


# ---------- 16C 설정 합치기 ----------

TABS = ["feat", "view", "update", "calc", "memory", "sample", "date", "viz", "dest"]
DEST_IDS = ("dest-list", "dest-name", "dest-kind", "dest-keep", "dest-preset", "dest-fields", "dest-done", "notify-after", "dest-save", "dest-check",
            "dest-test", "dest-del", "msg-dest", "bot-state", "bot-keep", "bot-token", "bot-app", "bot-users", "bot-channels", "bot-save", "bot-check",
            "bot-start", "bot-stop", "msg-bot", "bot-log")
PAGES_HTML = ("core/home.html", "query/index.html", "prep/prep.html", "collect/collect.html", "integrations/integrations.html")


def test_settings_categories_and_dest_inside():
    status = read("status.html")
    panel = status[status.index('id="feat-panel"'):]
    nav = between("status.html", '<nav class="set-nav"', "</nav>")
    assert re.findall(r'data-set="(\w+)"', nav) == TABS
    assert re.findall(r">([^<]+)</button>", nav) == ["쓸 기능", "화면 크기", "업데이트", "계산", "메모리", "표본", "날짜·달력", "차트 낱말", "보낼 곳"]
    assert re.search(r'data-set="viz" id="set-tab-viz" class="hidden"', nav)
    assert re.findall(r'<section class="set-page[^"]*" data-set="(\w+)" role="tabpanel"', panel) == TABS
    assert "set-group" not in status and panel.count("<details") == 2 and 'class="app-set"' in panel  # 접는 곳: 표본 [자세히], 보낼 곳의 Slack 봇
    for i in SETTING_IDS:
        assert f'id="{i}"' in panel, i
    viz = between("status.html", 'data-set="viz" role="tabpanel"', "</section>")
    assert 'id="viz-prefs"' in viz
    dest = between("status.html", 'data-set="dest" role="tabpanel"', "</section>")
    for i in DEST_IDS:
        assert f'id="{i}"' in dest, i
    assert dest.count("인증 확인") == 2 and "연결 확인" not in dest
    for gone in ('id="dest-panel"', 'id="dest-open"', 'id="dest-close"'):
        assert gone not in status, gone
    assert not (ROOT / "static/core/notify.html").exists()
    for page in PAGES_HTML:
        assert "core/notify.html" not in (ROOT / "static" / page).read_text(encoding="utf-8"), page


def test_settings_css():
    css = read("common.css")
    assert "grid-template-columns:10rem minmax(0, 1fr)" in rule(css, ".set-body")
    assert "border-right:1px solid var(--line)" in rule(css, ".set-nav")
    assert ".set-nav button[aria-selected=true]" in css and ".modal-box.set-box { width:min(57.5rem, 94vw)" in css  # 뒤에 오는 .modal-box의 좁은 너비(27.5rem)에 지지 않게 선택자를 겹쳐 쓴다
    assert ".set-group" not in css


@needs_node
def test_open_settings_shows_one_category(tmp_path):
    code = between("common.js", "const SET_TABS", "\n}\n") + "\n}\n"
    body = CLS + r"""
const panel = { classList: cls("hidden") };
const tabs = SET_TABS.map(k => ({ dataset: { set: k }, attrs: {}, setAttribute(a, v) { this.attrs[a] = v; } }));
const pages = SET_TABS.map(k => ({ dataset: { set: k }, classList: cls("hidden") }));
globalThis.$ = id => ({ "feat-panel": panel })[id];
globalThis.document = { querySelectorAll: q => q === "#feat-panel .set-page" ? pages : q === "#feat-panel .set-nav [data-set]" ? tabs : [] };
const calls = []; globalThis.api = url => { calls.push(url); return Promise.resolve({}); }; globalThis.showBot = () => {};
const shown = () => pages.filter(p => !p.classList.contains("hidden")).map(p => p.dataset.set);
openSettings("dest");
const out = { open: !panel.classList.contains("hidden"), selected: tabs.filter(t => t.attrs["aria-selected"] === "true").map(t => t.dataset.set),
  falses: tabs.filter(t => t.attrs["aria-selected"] === "false").length, shown: shown(), calls: [...calls] };
openSettings("calc");
out.calc = shown(); out.calls2 = calls.length;
openSettings("view"); out.view = shown();
try { openSettings("nope"); out.bad = ""; } catch (e) { out.bad = e.message; }
console.log(JSON.stringify(out));
"""
    got = run_node(tmp_path, body, parts=[code])
    assert got["open"] is True and got["selected"] == ["dest"] and got["falses"] == len(TABS) - 1 and got["shown"] == ["dest"]
    assert got["view"] == ["view"]  # [화면 크기] 분류도 열린다
    assert got["calls"] == ["/api/bot"] and got["calc"] == ["calc"] and got["calls2"] == 1  # 보낼 곳을 열 때만 봇 상태를 새로
    assert got["bad"] == "없는 설정 분류: nope"


def test_settings_wiring():
    js = read("common.js")
    assert '$("feat-open").onclick = () => openSettings("feat");' in js
    assert 'document.querySelectorAll("#feat-panel .set-nav [data-set]").forEach(b => b.onclick = () => openSettings(b.dataset.set));' in js
    assert 'const PANEL_PARAM = "panel", PANELS = ["feat", "dest"]' in js and "if (PANELS.includes(askedPanel)) openSettings(askedPanel);" in js
    assert '$("set-tab-viz").classList.remove("hidden");' in js
    for gone in ("dest-open", "dest-close", "dest-panel"):
        assert gone not in js, gone
    integ = read("integrations.html")
    assert '$("send-manage").onclick = () => openSettings("dest");' in integ and "dest-open" not in integ
    assert "위쪽 [보낼 곳]" not in read("collect.html")


# ---------- 16D 홈 최근 연결 ----------

@needs_node
def test_recent_list_fills_and_opens(tmp_path):
    code = between("common.js", "let recentConnections = [];", "// 연결 카드(connect.html)")
    body = CLS + r"""
const out = { filled: [], opened: 0, focus: "", said: [], connected: [], went: 0, listed: 0 };
const els = { "recent-conn": { classList: cls(), replaceChildren(...k) { this.kids = k; } }, "recent-list": { replaceChildren(...k) { this.kids = k; } },
  "recent-none": { classList: cls() }, password: { focus() { out.focus = "password"; } }, find: { focus() { out.focus = "find"; } } };
globalThis.$ = id => els[id];
const home = { classList: cls() };
globalThis.document = { querySelector: () => home };
globalThis.say = (id, text, ok) => out.said.push([id, text, ok]);
globalThis.connectWith = async body => { if (body.path === "C:/gone") throw new Error("그런 폴더나 파일이 없어요."); out.connected.push(body); };
globalThis.goQuery = () => { out.went++; };
globalThis.listFiles = async () => { out.listed++; };
globalThis.Option = function (t, v) { this.text = t; this.value = v; };
const now = new Date(), two = n => String(n).padStart(2, "0"), today = `${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())}`;
globalThis.api = async () => ({ connections: [{ info: { kind: "folder", path: "C:/d" }, count: 3 }, { info: { kind: "url", url: "postgres://h/db", user: "u" }, count: 1, last: today + "T09:05:00.000000" },
  { info: { kind: "folder", path: "C:/d", files: ["C:/d/a.csv", "C:\\d\\b.parquet", "C:/d/c.csv"] }, count: 2, last: "2026-01-02T23:40:00.000000" }, { info: { kind: "folder", path: "C:/d", file: "C:/d/x.duckdb" }, count: 5, last: today + "T08:00:00.000000" }] });
globalThis.fillConnection = i => out.filled.push(i);
globalThis.openConnect = () => { out.opened++; };
(async () => {
  await showRecentConnections();
  const all = els["recent-list"].kids, folders = all.filter(k => k.tag === "details");  // 줄과 날짜 폴더가 한 카드(#recent-list)에 있다
  const kids = all.filter(k => k.tag !== "details"), btns = kids.filter(k => k.tag === "button");
  out.top = btns.length;  // 폴더 밖에 보이는 줄 수
  out.rows = kids.map(k => k.tag === "button" ? k.children.slice(1).map(c => c.textContent) : k.textContent); out.types = btns.map(b => b.type);
  out.icons = btns.map(b => [b.children[0].className, b.children[0].children[0].tag, b.children[0].children[0].children.map(p => p.attrs.d).join(" ") === RECENT_ICONS.url.join(" ")]);  // 줄 앞의 종류 그림 타일: 주소는 DB, 파일은 문서
  out.folders = folders.map(f => [f.tag, f.children[0].children.map(c => c.textContent), f.children.slice(1).map(b => b.children[1].textContent)]);  // 이전: 날짜 폴더(머리글, 그 날의 연결들)
  out.last = all.at(-1).tag;  // 폴더는 줄들 뒤에 온다
  out.options = els["recent-conn"].kids.map(o => o.text);
  out.noneHidden = els["recent-none"].classList.contains("hidden");
  await btns[0].onclick({ currentTarget: btns[0] });  // 아이디가 있는 주소: 칸을 채우고 비밀번호만 받는다
  out.afterUrl = { opened: out.opened, filled: out.filled.length, focus: out.focus, go: recentGo, connected: out.connected.length, said: out.said.at(-1) };
  await btns[1].onclick({ currentTarget: btns[1] });  // 고른 파일이 기억된 파일 연결: 바로 연결하고 쿼리로
  out.afterFile = { connected: out.connected.at(-1), went: out.went, disabled: btns[1].disabled };
  await folders[0].children[1].onclick({ currentTarget: {} });  // 고른 파일 기록이 없는 예전 연결: 칸을 채우고 파일 목록까지 편다
  out.afterOld = { listed: out.listed, said: out.said.at(-1)[1], went: out.went };
  recentConnections.push({ info: { kind: "folder", path: "C:/gone", file: "C:/gone/a.duckdb" }, count: 1 });
  await openRecent(recentConnections.length - 1); out.failed = out.said.at(-1);  // 실패 문구는 최근 연결 카드에
  try { pickRecent(99); out.bad = ""; } catch (e) { out.bad = e.message; }
  console.log(JSON.stringify(out));
})();
"""
    got = run_node(tmp_path, body, parts=[code])
    # 최근에 쓴 순으로, 날짜마다 머리글. 줄은 [이름, 시각(오른쪽 끝), "폴더 · 횟수"]: 고른 파일이 있으면 이름은 파일 이름(둘까지)이고 폴더는 아래 줄에. 나머지가 있으면 "이전" 머리글
    assert got["rows"] == ["오늘", ["postgres://h/db, u", "09:05", "1번 사용"], ["x.duckdb", "08:00", "C:/d · 5번 사용"], "2026-01-02", ["a.csv, b.parquet 외 1개", "23:40", "C:/d · 2번 사용"], "이전"]
    assert got["icons"] == [["icon-tile", "svg", True], ["icon-tile", "svg", False], ["icon-tile", "svg", False]]
    assert got["options"] == ["최근 연결에서 고르기", "C:/d (3번 사용)", "postgres://h/db, u (1번 사용)", "a.csv, b.parquet 외 1개 (2번 사용)", "x.duckdb (5번 사용)"]  # 고르는 칸은 서버가 준 차례(많이 쓴 순) 그대로
    assert got["top"] == 3 and got["last"] == "details"  # 오늘 것(적으면 최근 3개까지)만 줄로, 나머지는 같은 카드의 날짜 폴더에
    assert got["folders"] == [["details", ["날짜 모름", "연결 1개"], ["C:/d"]]]  # 날짜마다 폴더 하나(오늘 것은 없다), 안에 그 날의 연결
    assert got["types"] == ["button"] * 3 and got["noneHidden"] is True
    assert "showOlder" not in read("common.js") and "recent-older" not in read("home.html")  # 이전 연결 카드는 따로 없다
    assert got["afterUrl"] == {"opened": 1, "filled": 1, "focus": "password", "go": True, "connected": 0, "said": ["msg-recent", "비밀번호를 넣고 [연결]을 누르면 쿼리로 넘어가요.", "hint"]}
    assert got["afterFile"] == {"connected": {"kind": "folder", "path": "C:/d", "file": "C:/d/x.duckdb"}, "went": 1, "disabled": False}
    assert got["afterOld"] == {"listed": 1, "said": "파일을 고르고 [연결]을 누르면 쿼리로 넘어가요.", "went": 1}
    assert got["failed"] == ["msg-recent", "그런 폴더나 파일이 없어요.", None]
    assert got["bad"] == "없는 최근 연결: 99"


@needs_node
def test_update_notes_become_sections_and_lists(tmp_path):
    """업데이트 창: 릴리스 노트를 묶음 머리 + 목록으로 그린다. "없음"뿐인 묶음은 빼고, "이름: 설명"은 이름을 굵게, 글자로만 넣는다."""
    body = r"""
const el = (tag, props = {}, ...kids) => ({ tagName: tag.toUpperCase(), ...props, children: kids.filter(k => typeof k !== "string"), text: kids.filter(k => typeof k === "string").join(""),
  get lastChild() { return this.children.at(-1); }, append(k) { this.children.push(k); } });
const flat = e => [e.tagName, e.textContent ?? e.text, ...e.children.map(flat)];
console.log(JSON.stringify(updNotes("### 추가\r\n- A/B 검정: 사람마다 모아요.\n- <b>굵게</b>\n\n### 개선\n없음\n### 삭제\n없음\n받지 못했어요").map(flat)));
"""
    code = between("common.js", "function updNotes(text)", "// 새 버전 알림")
    assert run_node(tmp_path, body, parts=[code]) == [
        ["SECTION", "", ["H4", "추가"], ["UL", "", ["LI", " 사람마다 모아요.", ["B", "A/B 검정"]], ["LI", "<b>굵게</b>"]]],
        ["P", "받지 못했어요"]]
    panel = between("status.html", '<div id="upd-panel"', '<div id="upd-msg"')
    assert 'class="modal-box upd-box"' in panel and 'id="upd-close" class="btn">나중에' in panel and 'id="upd-go" class="btn p"' in panel
    assert ".modal-box.upd-box { width:min(40rem, 94vw); }" in read("common.css")


@needs_node
def test_file_list_shows_names_under_the_folder(tmp_path):
    """파일 목록에는 경로 칸의 폴더 아래 이름만 보인다(값은 전체 경로 그대로)."""
    body = r"""
const box = { value: "C:\\data\\" };
globalThis.$ = () => box;
const out = [underPath("C:\\data\\a.csv"), underPath("c:/DATA/sub/b.parquet"), underPath("C:\\database\\c.csv"), underPath("D:\\x\\d.csv")];
box.value = "C:\\data\\a.csv"; out.push(underPath("C:\\data\\a.csv"));
box.value = ""; out.push(underPath("C:\\data\\a.csv"));
console.log(JSON.stringify(out));
"""
    code = between("common.js", "function underPath(full)", "const LIST_MAX")
    assert run_node(tmp_path, body, parts=[code]) == ["a.csv", "sub/b.parquet", "c.csv", "d.csv", "a.csv", "a.csv"]
    lists = between("common.js", "async function listFiles()", "connMsg(more")
    assert "o.text = underPath(o.value)" in lists and 'b.nextSibling.textContent = " " + underPath(b.value)' in lists and "b.parentElement.title = b.value" in lists


def test_home_layout():
    html = read("home.html")
    for gone in ('class="choice"', "data-go", "connectLast", "let target", ".hero", "var(--faint)"):
        assert gone not in html, gone
    main = between("home.html", '<section class="home-main"', "</section>")
    assert 'id="home-name"' in main and 'id="home-state"' in main and 'id="home-conn"' in main  # 지금 연결 줄: 이름 · 꼬리표 · 단추
    assert "<!--include core/connect.html-->" not in main and html.index("</aside>") < html.index("<!--include core/connect.html-->")  # 연결 창은 첫 화면 칸 밖(막 위에 뜬다)
    go = between("home.html", '<nav class="home-go"', "</nav>")
    assert re.findall(r'href="(/\w+)" data-feature="(\w+)"', go) == [(h, f) for h, f, _ in RAIL[1:] if h != "/dashboard/build"]  # 첫 화면 카드는 기능마다 하나(만드는 곳은 왼쪽 메뉴에만)
    aside = between("home.html", '<aside class="home-recent"', "</aside>")
    assert 'id="recent-list"' in aside and 'id="recent-none"' in aside and 'id="msg-recent"' in aside
    # 최근 연결 카드는 왼쪽 칸과 같은 높이로 늘고, 목록(날짜 폴더 포함)은 카드 안에서 굴린다: 폴더를 펼쳐도 카드가 커지지 않는다
    assert ".home-recent { height:0; min-height:100%;" in html and "align-self:start" not in html
    assert ".recent-list { flex:1; min-height:0; overflow-y:auto;" in html and ".recent-folder { flex:none;" in html
    assert "function afterConnect() { recentGo ? goQuery() : showHome(); }" in html and "resume().then(showHome);" in html  # 최근 연결로 연결하면 쿼리로
    assert 'SERVER.features.on.includes("query") ? location.assign("/search") : showHome()' in html
    assert "#conn-close { display:none" not in html and ".home #conn-panel" not in html  # 첫 화면도 연결 창을 띄우고, 연결 전에도 닫을 수 있다
    assert '$("home-conn").onclick = () => openConnect();' in html


def test_demo_button_lives_in_settings_only():
    """[예시 데이터로 둘러보기]는 설정 [쓸 기능]에만 있다(첫 화면·연결 창에는 없다). 연결이 바뀐다는 것을 단추 설명에 적는다."""
    for page in ("home.html", "connect.html"):
        assert "demo" not in read(page) and "예시 데이터" not in read(page), page
    feat = between("status.html", 'data-set="feat" role="tabpanel"', "</section>")
    button = re.search(r'<button id="set-demo" class="btn" type="button" title="([^"]+)">예시 데이터로 둘러보기</button>', feat)
    assert button and "연결이 예시 데이터로 바뀌어요" in button[1] and 'id="set-demo-msg"' in feat
    assert '$("set-demo").onclick = () => busy($("set-demo"), async () => {' in read("common.js")


@needs_node
def test_settings_demo_connects_like_the_connect_button(tmp_path):
    """연결 칸이 있는 화면은 connectWith 뒤 afterConnect(있을 때만), 없는 화면(수집)은 연결만 하고 위쪽 줄 이름을 바꾼다.
    성공하면 설정 창을 닫고, 실패하면 문구를 보이고 열어 둔다."""
    code = between("common.js", '$("set-demo").onclick', "// 릴리스 노트")
    body = CLS + r"""
const INFO = { kind: "folder", path: "C:/demo", file: "C:/demo/demo.duckdb" };
async function run({ hasConnect, after, fail }) {
  const log = [], button = {}, panel = { classList: cls() };
  const $ = id => ({ "set-demo": button, "feat-panel": panel })[id];
  const api = async url => { log.push(url); if (fail) throw new Error("못 만들었어요"); return url === "/api/demo" ? INFO : { schemas: [] }; };
  const busy = (b, fn) => fn();
  const connectWith = async info => { log.push(["connectWith", info.file]); };
  const afterConnect = after ? () => log.push("afterConnect") : undefined;
  const connLabel = info => info.file.split("/").pop(), showCrumb = () => log.push(["crumb", connName]);
  const say = (id, text) => log.push([id, text]);
  let connName = null;
""" + code + r"""
  await button.onclick();
  return { log, closed: panel.classList.contains("hidden") };
}
(async () => console.log(JSON.stringify([await run({ hasConnect: true, after: true }), await run({ hasConnect: true }), await run({ hasConnect: false }), await run({ hasConnect: true, fail: true })])))();
"""
    home, query, collect, failed = run_node(tmp_path, body)
    assert home == {"log": ["/api/demo", ["connectWith", "C:/demo/demo.duckdb"], "afterConnect", ["set-demo-msg", ""]], "closed": True}
    assert query == {"log": ["/api/demo", ["connectWith", "C:/demo/demo.duckdb"], ["set-demo-msg", ""]], "closed": True}
    assert collect == {"log": ["/api/demo", "/api/connect", ["crumb", "demo.duckdb"], ["set-demo-msg", ""]], "closed": True}
    assert failed == {"log": ["/api/demo", ["set-demo-msg", "못 만들었어요"]], "closed": False}


@needs_node
def test_show_home_switches_views(tmp_path):
    code = between("home.html", "function showHome() {", "\n}\n") + "\n}\n"
    body = CLS + r"""
const home = { classList: cls() }, name = { textContent: "x" }, tag = { textContent: "", classList: cls() }, conn = { textContent: "" };
globalThis.$ = id => ({ "home-name": name, "home-state": tag, "home-conn": conn })[id];
globalThis.document = { querySelector: q => (q === ".home" ? home : null) };
const n = { open: 0, close: 0 };
globalThis.openConnect = () => { n.open++; }; globalThis.closeConnect = () => { n.close++; };
const look = () => ({ on: home.classList.contains("on"), name: name.textContent, tag: [tag.textContent, tag.classList.contains("on")], conn: conn.textContent, ...n });
globalThis.connected = null; globalThis.connName = null;
showHome(); const before = look();
globalThis.connected = "other"; globalThis.connName = "sales.duckdb";
showHome();
console.log(JSON.stringify({ before, after: look() }));
"""
    got = run_node(tmp_path, body, parts=[code])
    assert got["before"] == {"on": False, "name": "연결된 데이터가 없어요", "tag": ["연결 안 됨", False], "conn": "데이터 연결", "open": 1, "close": 0}
    assert got["after"] == {"on": True, "name": "sales.duckdb", "tag": ["연결됨", True], "conn": "다른 데이터 연결", "open": 1, "close": 1}


def test_changed_files_have_no_control_chars():
    for path in ("static/core/common.js", "static/core/home.html", "static/core/status.html", "web/components/shell/TopBar.tsx", "web/components/edit/AddCardMenu.tsx"):
        assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", (ROOT / path).read_text(encoding="utf-8")), path


# ---------- 16E 카드 추가 서랍 ----------

def test_add_card_drawer_markup():
    src = web("components/edit/AddCardMenu.tsx")
    assert '<Sel label="카드 종류"' not in src and "KIND_GROUPS.map(([g, kinds]) =>" in src
    assert 'className="cm-groups"' in src and 'className="cm-kind-list"' in src and "cm-kind-name" not in src  # 묶음 구역과 종류 구역이 따로
    assert "KIND_GROUPS.find(([g]) => g === group)![1].map(k =>" in src  # 종류 구역에는 고른 묶음의 종류만
    assert 'className={"cm-kind" + (f.kind === k ? " on" : "")}' in src and "aria-pressed={f.kind === k}" in src
    assert 'onClick={() => { setErr(""); setQuery(""); setTop(FIRST_GROUP); setGroup(KIND_GROUPS.find(g => g[1].includes(k))![0]); setF(p => ({ ...p, kind: k, rows: fit(p.rows, k) })); }}' in src
    assert 'id={fill ? undefined : "d-add"}' in src and 'className={fill ? "pop cardmenu" : "d-add cardmenu"}' in src
    assert '{!fill && <div className="phead"><b>카드 추가</b>' in src
    body, more, foot = src.index('className="d-add-body"'), src.index("<summary>더 많은 설정</summary>"), src.index('className="row d-add-foot"')
    assert body < more < foot
    fold = src[more:src.index("</details>", more)]
    assert 'placeholder="카드 이름 (비우면 자동)"' in fold and 'aria-label="목표 파일 경로"' in fold
    assert '<details className="cm-more">' in src
    tail = src[foot:]
    assert '{fill ? "이 카드에 채우기" : "카드 추가"}' in tail and ">닫기</button>" in tail


def test_use_add_card_exists():
    bar = web("components/shell/Toolbar.tsx")
    assert "export function useAddCard(): (card: Partial<Card>) => Promise<void>" in bar and "Sub-agent must implement" not in bar


def test_toolbar_edit_only_tools():
    bar = web("components/shell/Toolbar.tsx")
    assert re.search(r'\{build && \(\s*<div className="drop" ref=\{drop\}', bar)  # [+ 카드 ▾]는 만드는 곳에만
    assert "편집 끝내기" not in bar and 'id="d-view"' in bar and 'id="d-build"' in bar  # [편집] 단추 대신 두 곳을 오가는 단추
    # 대시보드 설정은 [더 보기] 메뉴 안에 있다(만드는 곳)
    assert re.search(r'\n\s*<button role="menuitem"[^\n]*setMenu\(false\); setSettings\(true\);[^\n]*>대시보드 설정</button>', bar) and not re.search(r'state\.editing && <button[^\n]*대시보드 설정', bar)
    assert ">설정</button>" not in bar and "<AddCardMenu" not in bar and bar.count("conn.label") == 1  # 연결 이름은 [대시보드만 보기]의 위쪽 줄에만
    assert 'setMenu(false); dispatch({ type: "select", id: null }); setAddOpen(true);' not in bar and bar.count(">카드 추가</button>") == 1  # 메뉴 안의 같은 일을 하는 [카드 추가]는 뺐다(강조 단추 하나만)
    assert "}, [menu]);" in bar and "[menu, addOpen, setAddOpen]" not in bar
    assert "const add = useAddCard();" in bar


def test_dashboard_drawer_slot():
    src = web("components/shell/DashboardScreen.tsx")
    assert "const add = useAddCard();" in src
    assert "{addOpen && !dashOnly ? <AddCardMenu open onClose={() => setAddOpen(false)} onAdd={add} /> : <CardPanel" in src
    assert "useEffect(() => { if (state.selected) setAddOpen(false); }, [state.selected, setAddOpen]);" in src
    assert '<b>{state.dash?.name || "대시보드"}</b><span className="hint ml-[12px]" id="d-state"' in src
    assert '<span className="d-conn" title="지금 연결된 데이터">' in web("components/shell/Toolbar.tsx")  # 연결 이름은 [대시보드만 보기]의 위쪽 줄에
    assert 'onClick={e => { e.stopPropagation(); dispatch({ type: "select", id: null }); setAddOpen(true); }}' in src


def test_drawer_css():
    css = read("dash_edit.css")
    d = rule(css, ".d-main > #d-add")
    for part in ("grid-column:3;", "position:sticky;", "display:flex;", "flex-direction:column;", "overflow:hidden;", "max-height:calc(100vh - 10rem);"):
        assert part in d, part
    assert "flex:1; min-height:0; overflow:auto;" in rule(css, "#d-add .d-add-body")
    assert "flex:none;" in rule(css, "#d-add .d-add-foot")
    assert ".cardmenu .cm-groups" in css and ".cardmenu .cm-kind-list" in css and ".cardmenu .cm-more" in css
