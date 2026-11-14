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
CSS_FILES = (ROOT / "static/core/common.css", ROOT / "static/core/dash_edit.css", ROOT / "static/query/sentences.css", WEB / "styles/dashboard.css")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

# 가짜 classList(Set): init에 든 클래스로 시작한다
CLS = "const cls = (...init) => { const s = new Set(init); return { add: c => s.add(c), remove: c => s.delete(c), toggle: (c, on) => (on ?? !s.has(c)) ? s.add(c) : s.delete(c), contains: c => s.has(c) }; };\n"


def web(path):
    return (WEB / path).read_text(encoding="utf-8")


def css_text(path):
    return path.read_text(encoding="utf-8")


# ---------- 16A 디자인 토큰 ----------

TOKENS = {"--fs-xs": "12px", "--fs-sm": "13px", "--fs-md": "14px", "--fs-lg": "16px", "--fs-xl": "20px", "--fs-2xl": "28px",
          "--sp-1": "4px", "--sp-2": "8px", "--sp-3": "12px", "--sp-4": "16px", "--sp-5": "24px", "--sp-6": "32px",
          "--r-ctl": "6px", "--r-card": "10px", "--r-pill": "999px", "--h-sm": "28px", "--h-md": "32px", "--h-lg": "40px"}


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
    assert "border:1px solid var(--line-strong)" in rule(css, "input, select, textarea")
    assert "height:var(--h-md)" in rule(css, ".btn")
    ib = rule(css, ".ib")
    assert "width:var(--h-md)" in ib and "height:var(--h-md)" in ib


def test_no_px_font_sizes_or_radius_literals():
    for path in CSS_FILES:
        css = css_text(path)
        assert not re.findall(r"font-size:\s*[\d.]+px", css), path.name
        assert not re.findall(r"\bfont:\s*[\d.]+px", css), path.name
        for v in re.findall(r"border-radius:\s*([^;}]+)", css):
            rest = re.sub(r"var\(--r-(?:ctl|card|pill)\)|\b0\b|50%", "", v).strip()
            assert not rest, (path.name, v)


FAINT_OK = {"common.css": {".crumb i", ".jobx.idle i", ".cn", ".drop .pop .cal-day:disabled"},
            "dash_edit.css": {".bresize"}, "sentences.css": set(), "dashboard.css": {".drop .pop .dcal-day:disabled"}}


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
        ("/collect", "collect", "수집"), ("/integrations", "integrations", "연동")]


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
    at = [top.index(f'id="{i}"') for i in ("crumb", "usage", "job", "rf-pill", "upd", "resize")]
    assert at == sorted(at)
    for gone in ('id="feat-open"', 'id="dest-open"', 'id="theme"', "<nav"):
        assert gone not in top, gone


def test_rail_css():
    css = read("common.css")
    assert "position:fixed" in rule(css, ".rail")
    assert "padding-left:var(--rail-w)" in rule(css, "body:has(.rail)")
    assert root_vars(css, False)["--rail-w"] == "200px"
    media = re.search(r"@media \(max-width:1099px\) \{(.*?)\n\}", css, re.S).group(1)
    assert ":root { --rail-w:56px; }" in media and ".rail :is(a, .rail-btn, .rail-name) span { display:none; }" in media
    assert ".pages" not in css and ".rail a" in re.search(r"^(\.btn, \.ib[^{]*)\{", css, re.M).group(1)
    assert "min(var(--panel, clamp(300px, 28vw, 520px)), calc((100vw - var(--rail-w)) * .6))" in rule(css, ".work")


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
    tabs = re.findall(r'\["(/\w*)", "(\w*)", "([^"]+)", "[^"]*", \w+\]', bar)
    assert tabs == RAIL
    assert bar.index('<nav className="rail"') < bar.index('<header className="topbar">')


def test_dash_only_hides_rail():
    css = css_text(WEB / "styles/dashboard.css")
    line = next(x for x in css.splitlines() if x.startswith("body.dash-only :is("))
    assert ".rail" in line and ".topbar" in line
    assert "body.dash-only:has(.rail) { padding-left:0; }" in css


# ---------- 16C 설정 합치기 ----------

TABS = ["feat", "update", "calc", "memory", "sample", "date", "viz", "dest"]
DEST_IDS = ("dest-list", "dest-name", "dest-kind", "dest-keep", "dest-preset", "dest-fields", "dest-done", "notify-after", "dest-save", "dest-check",
            "dest-test", "dest-del", "msg-dest", "bot-state", "bot-keep", "bot-token", "bot-app", "bot-users", "bot-channels", "bot-save", "bot-check",
            "bot-start", "bot-stop", "msg-bot", "bot-log")
PAGES_HTML = ("core/home.html", "query/index.html", "prep/prep.html", "collect/collect.html", "integrations/integrations.html")


def test_settings_categories_and_dest_inside():
    status = read("status.html")
    panel = status[status.index('id="feat-panel"'):]
    nav = between("status.html", '<nav class="set-nav"', "</nav>")
    assert re.findall(r'data-set="(\w+)"', nav) == TABS
    assert re.findall(r">([^<]+)</button>", nav) == ["쓸 기능", "업데이트", "계산", "메모리", "표본", "날짜·달력", "차트 낱말", "보낼 곳"]
    assert re.search(r'data-set="viz" id="set-tab-viz" class="hidden"', nav)
    assert re.findall(r'<section class="set-page[^"]*" data-set="(\w+)" role="tabpanel"', panel) == TABS
    assert "set-group" not in status and "<details" not in panel and 'class="app-set"' in panel
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
    assert "grid-template-columns:160px minmax(0, 1fr)" in rule(css, ".set-body")
    assert "border-right:1px solid var(--line)" in rule(css, ".set-nav")
    assert ".set-nav button[aria-selected=true]" in css and ".modal-box.set-box { width:min(920px, 94vw)" in css  # 뒤에 오는 .modal-box의 좁은 너비(440px)에 지지 않게 선택자를 겹쳐 쓴다
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
try { openSettings("nope"); out.bad = ""; } catch (e) { out.bad = e.message; }
console.log(JSON.stringify(out));
"""
    got = run_node(tmp_path, body, parts=[code])
    assert got["open"] is True and got["selected"] == ["dest"] and got["falses"] == 7 and got["shown"] == ["dest"]
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
const out = { filled: [], opened: 0, focus: "" };
const els = { "recent-conn": { classList: cls(), replaceChildren(...k) { this.kids = k; } }, "recent-list": { replaceChildren(...k) { this.kids = k; } },
  "recent-none": { classList: cls() }, password: { focus() { out.focus = "password"; } }, find: { focus() { out.focus = "find"; } } };
globalThis.$ = id => els[id];
globalThis.Option = function (t, v) { this.text = t; this.value = v; };
const now = new Date(), two = n => String(n).padStart(2, "0"), today = `${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())}`;
globalThis.api = async () => ({ connections: [{ info: { kind: "folder", path: "C:/d" }, count: 3 }, { info: { kind: "url", url: "postgres://h/db", user: "u" }, count: 1, last: today + "T09:05:00.000000" },
  { info: { kind: "folder", path: "C:/d", files: ["C:/d/a.csv", "C:\\d\\b.parquet", "C:/d/c.csv"] }, count: 2, last: "2026-01-02T23:40:00.000000" }, { info: { kind: "folder", path: "C:/d", file: "C:/d/x.duckdb" }, count: 5, last: today + "T08:00:00.000000" }] });
globalThis.fillConnection = i => out.filled.push(i);
globalThis.openConnect = () => { out.opened++; };
(async () => {
  await showRecentConnections();
  const btns = els["recent-list"].kids.filter(k => k.tag === "button");
  out.rows = els["recent-list"].kids.map(k => k.tag === "button" ? k.children.map(c => c.textContent) : k.textContent); out.types = btns.map(b => b.type);
  out.options = els["recent-conn"].kids.map(o => o.text);
  out.noneHidden = els["recent-none"].classList.contains("hidden");
  btns[0].onclick();
  try { pickRecent(9); out.bad = ""; } catch (e) { out.bad = e.message; }
  console.log(JSON.stringify(out));
})();
"""
    got = run_node(tmp_path, body, parts=[code])
    # 최근에 쓴 순으로, 날짜마다 머리글. 줄은 [이름, "시각 · 폴더 · 횟수"]: 고른 파일이 있으면 이름은 파일 이름(둘까지)이고 폴더는 아래 줄에
    assert got["rows"] == ["오늘", ["postgres://h/db, u", "09:05 · 1번 사용"], ["x.duckdb", "08:00 · C:/d · 5번 사용"], "2026-01-02", ["a.csv, b.parquet 외 1개", "23:40 · C:/d · 2번 사용"],
                           "날짜 모름", ["C:/d", "3번 사용"]]
    assert got["options"] == ["최근 연결에서 고르기", "C:/d (3번 사용)", "postgres://h/db, u (1번 사용)", "a.csv, b.parquet 외 1개 (2번 사용)", "x.duckdb (5번 사용)"]  # 고르는 칸은 서버가 준 차례(많이 쓴 순) 그대로
    assert got["types"] == ["button"] * 4 and got["noneHidden"] is True
    assert got["opened"] == 1 and got["filled"] == [{"kind": "url", "url": "postgres://h/db", "user": "u"}] and got["focus"] == "password"
    assert got["bad"] == "없는 최근 연결: 9"


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
    assert ".modal-box.upd-box { width:min(640px, 94vw); }" in read("common.css")


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
    assert main.index('id="home-now"') < main.index("<!--include core/connect.html-->") and 'id="home-name"' in main
    go = between("home.html", '<nav class="home-go"', "</nav>")
    assert re.findall(r'href="(/\w+)" data-feature="(\w+)"', go) == [(h, f) for h, f, _ in RAIL[1:]]
    aside = between("home.html", '<aside class="home-recent"', "</aside>")
    assert 'id="recent-list"' in aside and 'id="recent-none"' in aside
    assert "function afterConnect() { showHome(); }" in html and "resume().then(showHome);" in html
    assert ".home:not(.on) #conn-close { display:none !important; }" in html


@needs_node
def test_show_home_switches_views(tmp_path):
    code = between("home.html", "function showHome() {", "\n}\n") + "\n}\n"
    body = CLS + r"""
const home = { classList: cls() }, now = { classList: cls("hidden") }, name = { textContent: "x" };
globalThis.$ = id => ({ "home-now": now, "home-name": name })[id];
globalThis.document = { querySelector: q => (q === ".home" ? home : null) };
const n = { open: 0, close: 0 };
globalThis.openConnect = () => { n.open++; }; globalThis.closeConnect = () => { n.close++; };
const look = () => ({ on: home.classList.contains("on"), nowHidden: now.classList.contains("hidden"), name: name.textContent, ...n });
globalThis.connected = null; globalThis.connName = null;
showHome(); const before = look();
globalThis.connected = "other"; globalThis.connName = "sales.duckdb";
showHome();
console.log(JSON.stringify({ before, after: look() }));
"""
    got = run_node(tmp_path, body, parts=[code])
    assert got["before"] == {"on": False, "nowHidden": True, "name": "", "open": 1, "close": 0}
    assert got["after"] == {"on": True, "nowHidden": False, "name": "sales.duckdb", "open": 1, "close": 1}


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
    assert 'onClick={() => { setErr(""); setF(p => ({ ...p, kind: k, rows: fit(p.rows, k) })); }}' in src
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
    assert re.search(r'\{state\.editing && \(\s*<div className="drop" ref=\{drop\}', bar)
    assert re.search(r'\{state\.editing && <button className="btn" onClick=\{\(\) => setSettings\(true\)\}[^\n]*>대시보드 설정</button>\}', bar)
    assert ">설정</button>" not in bar and "<AddCardMenu" not in bar and "conn.label" not in bar
    assert 'setMenu(false); dispatch({ type: "select", id: null }); setAddOpen(true);' in bar
    assert "}, [menu]);" in bar and "[menu, addOpen, setAddOpen]" not in bar
    assert "if (state.editing) setAddOpen(false);" in bar
    assert "const add = useAddCard();" in bar


def test_dashboard_drawer_slot():
    src = web("app/dashboard/page.tsx")
    assert "const add = useAddCard();" in src
    assert "{addOpen && !dashOnly ? <AddCardMenu open onClose={() => setAddOpen(false)} onAdd={add} /> : <CardPanel" in src
    assert "useEffect(() => { if (state.selected) setAddOpen(false); }, [state.selected, setAddOpen]);" in src
    assert '<b>{state.dash?.name || "대시보드"}</b><span className="d-conn"' in src
    assert 'onClick={e => { e.stopPropagation(); dispatch({ type: "select", id: null }); setAddOpen(true); }}' in src


def test_drawer_css():
    css = read("dash_edit.css")
    d = rule(css, ".d-main > #d-add")
    for part in ("grid-column:3;", "position:sticky;", "display:flex;", "flex-direction:column;", "overflow:hidden;", "max-height:calc(100vh - 160px);"):
        assert part in d, part
    assert "flex:1; min-height:0; overflow:auto;" in rule(css, "#d-add .d-add-body")
    assert "flex:none;" in rule(css, "#d-add .d-add-foot")
    assert ".cardmenu .cm-groups" in css and ".cardmenu .cm-kind-list" in css and ".cardmenu .cm-more" in css
