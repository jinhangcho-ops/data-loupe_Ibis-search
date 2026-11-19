"""쿼리 화면의 집계·차트·SQL 탭을 일 단위 카드로(6번 묶음 A1~A3, B1~B2, S1): 카드 제목과 그 제목 줄의 실행 단추,
4칸 격자의 칸마다 이름표, 결과 카드 아래의 내보내기 줄, 한 줄에 나란한 접는 설정, 옛 id가 그대로인지. 화면 파일을 읽어 구조만 본다."""
from __future__ import annotations

from html.parser import HTMLParser

from test_fix_audit_js import read

VOID = frozenset({"input", "br", "hr", "img", "meta", "link"})
FIELDS = frozenset({"input", "select", "textarea"})
# 카드 제목 → 제목 줄(.head)에 있어야 하는 실행 단추 id
RUN_BUTTONS = {"컬럼 요약": ["summarize"], "피벗 표": ["pv-run"], "기간별 표": ["pt-run"], "차트": ["draw", "dash-add"],
               "질문으로 SQL 만들기": ["nl-ask"], "SQL": ["sql-run2"], "결과": []}
GRID_CARDS = ("컬럼 요약", "피벗 표", "기간별 표", "차트")
# 카드 제목 → (결과가 그려지는 곳 id, 그 뒤에 와야 하는 내보내기 단추 id)
EXPORTS = {"컬럼 요약": ("summary", ["simage", "ssend", "ssend-go"]), "기간별 표": ("pt-out", ["pt-csv"]),
           "차트": ("chart", ["ctable-csv", "cimage", "csend", "csend-go"]), "결과": ("sql-result", ["sql-save-open", "sql-save-pop"])}
EXPORT_MARKS = {"컬럼 요약": "data-table", "차트": "data-image", "결과": "data-sqlexport"}
FOLDS = ["세로축 최소·최대", "그룹 고르기", "라벨 바꾸기", "자동 규칙", "컬럼을 끌어다 놓아 차트 만들기"]
# 고치기 전(카드로 바꾸기 전) 집계·차트 탭과 SQL 화면에 있던 id. 하나도 사라지면 안 된다.
OLD_IDS = """cagg call cauto caxis cconnect cconnect-box cdash cexport-all cgroup-box cgroups chart cimage clabels corder cratio cscale csend csend-go
csplit cswap ctable ctable-csv ctable-show ctype cunit cunit-box cx cy cz cz-box dash-add draw err-chart err-pivot err-pt err-summary hashed-note-c
hashed-note-s pane-chart pane-summary pivot pt-add pt-box pt-cal pt-chart pt-csv pt-out pt-period pt-run pt-show pt-tables pt-total pt-unit pv-above
pv-add pv-col pv-convert pv-extra pv-having pv-how pv-key pv-parent pv-pareto pv-preset pv-ps pv-rows pv-rows2 pv-run pv-share pv-show pv-sub
pv-target pv-total pv-val pv-where s-above s-dup s-gap s-ps s-quant s-zero scol-add scols series series-add shelf-box shelf-host simage ssend
ssend-go summarize summary y1max y1min ymax ymin err-sql llm-box llm-env llm-name llm-pick llm-read nl-ask nl-prompt nl-q nl-view sql-check
sql-conn sql-count sql-deep sql-go sql-next sql-page sql-paint sql-plan sql-plan-show sql-prev sql-result sql-rows sql-run2 sql-save-open
sql-save-pop sql-sub sql-text sqlv""".split()


class Node:
    def __init__(self, tag: str, attrs: dict[str, str | None], parent: Node | None) -> None:
        self.tag, self.attrs, self.parent, self.children, self.own = tag, attrs, parent, [], ""

    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def walk(self) -> list[Node]:
        return [self, *(n for c in self.children for n in c.walk())]

    def find(self, tag: str = "", cls: str = "") -> list[Node]:
        return [n for n in self.walk()[1:] if (not tag or n.tag == tag) and (not cls or cls in n.classes())]

    def text(self) -> str:
        return "".join(n.own for n in self.walk()).strip()

    def up(self, cls: str) -> Node | None:
        node = self.parent
        while node is not None and cls not in node.classes():
            node = node.parent
        return node


class Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.root = Node("root", {}, None)
        self.at = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, dict(attrs), self.at)
        self.at.children.append(node)
        if tag not in VOID:
            self.at = node

    def handle_endtag(self, tag: str) -> None:
        node = self.at
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.at = node.parent

    def handle_data(self, data: str) -> None:
        self.at.own += data


def page() -> Node:
    tree = Tree()
    tree.feed(read("index.html"))
    return tree.root


def by_id(root: Node) -> dict[str, Node]:
    return {n.attrs["id"]: n for n in root.walk() if n.attrs.get("id")}


def cards(root: Node) -> dict[str, Node]:
    """세 탭(집계·차트·SQL 화면) 안의 카드를 제목으로 찾는다."""
    ids = by_id(root)
    found = {}
    for host in ("pane-summary", "pane-chart", "sqlv"):
        for card in ids[host].find(cls="card"):
            heads = [h for h in card.children if "head" in h.classes()]
            if heads and heads[0].find("h3"):
                found[heads[0].find("h3")[0].text()] = card
    return found


def test_six_jobs_are_cards_with_the_run_button_in_the_title_row() -> None:
    root = page()
    found, ids = cards(root), by_id(root)
    assert list(found) == list(RUN_BUTTONS)  # 집계 셋 → 차트 → SQL 셋, 이 차례로
    for title, buttons in RUN_BUTTONS.items():
        head = found[title].children[0]
        assert "head" in head.classes() and len(head.find(cls="hint")) == 1, title  # 제목 + 한 줄 설명
        assert [b for b in buttons if ids[b].up("head") is not head] == [], title
    for host in ("pane-summary", "pane-chart", "sqlv"):
        assert ids[host].find(cls="lead") == [], host  # 안내 글은 카드의 한 줄 설명 하나뿐


def test_every_field_in_a_grid_has_a_label() -> None:
    found = cards(page())
    for title in GRID_CARDS:
        grids = found[title].find(cls="q-grid")
        assert grids, title
        fields = [f for g in grids for f in g.walk() if f.tag in FIELDS or f.attrs.get("id") in ("cagg", "pv-how", "scols")]
        assert fields, title
        bare = [f.attrs.get("id") for f in fields if not (f.up("cf") and [c for c in f.up("cf").children if "cl" in c.classes() and c.text()])]
        assert bare == [], title
        loose = [c.attrs.get("id") or c.tag for g in grids for c in g.children if "cf" not in c.classes()]
        assert loose == [], title  # 격자 바로 아래는 이름표 달린 칸(.cf)뿐
        assert [b.attrs.get("id") for g in grids for b in g.find("input") if b.attrs.get("type") == "checkbox"] == [], title  # 체크 칸은 격자 밖 한 줄에
    css = read("common.css")
    assert ".q-grid { display:grid; grid-template-columns:repeat(4, minmax(0, 1fr));" in css
    assert "@media (max-width:800px) { .q-grid { grid-template-columns:repeat(2, minmax(0, 1fr)); } }" in css


def test_checkboxes_sit_together_in_one_row() -> None:
    ids = by_id(page())
    for boxes in (["s-dup", "s-gap", "s-quant", "s-zero"], ["pv-share", "pv-parent", "pv-total", "pv-pareto"]):
        rows = {id(ids[b].up("q-row")) for b in boxes}
        assert len(rows) == 1 and None not in [ids[b].up("q-row") for b in boxes], boxes
        assert all("check" in ids[b].parent.classes() for b in boxes), boxes


def test_export_row_is_the_bottom_row_of_its_result_card() -> None:
    root = page()
    found, order = cards(root), {id(n): i for i, n in enumerate(root.walk())}
    for title, (result, buttons) in EXPORTS.items():
        inside = by_id(found[title])
        marked = [n for n in found[title].walk() if EXPORT_MARKS.get(title) in n.attrs]
        assert (title not in EXPORT_MARKS) or marked, title
        for node in [*(inside[b] for b in buttons), *marked]:  # KeyError면 카드 밖에 있다
            assert order[id(node)] > order[id(inside[result])], title
            assert node.up("q-foot") is found[title].children[-1], title


def test_chart_settings_fold_side_by_side() -> None:
    ids = by_id(page())
    folds = ids["pane-chart"].find(cls="q-folds")
    assert len(folds) == 1 and [d.tag for d in folds[0].children] == ["details"] * len(FOLDS)
    assert [d.children[0].text() for d in folds[0].children] == FOLDS
    for inner, at in (("ymin", 0), ("y1max", 0), ("cgroups", 1), ("clabels", 2), ("cauto", 3), ("call", 3), ("shelf-host", 4)):
        node = ids[inner]
        while node.tag != "details":
            node = node.parent
        assert node is folds[0].children[at], inner
    css = read("common.css")
    assert ".q-folds > details[open] { flex-basis:100%; order:1; }" in css  # 펼친 항목은 접힌 줄 아래에 한 줄 가득


def test_no_old_id_is_lost() -> None:
    ids = by_id(page())
    assert [i for i in OLD_IDS if i not in ids] == []
    html = read("index.html")
    assert [i for i in OLD_IDS if html.count(f'id="{i}"') != 1] == []
