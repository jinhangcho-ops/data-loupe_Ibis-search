"""조인에서 겹친 오른쪽 컬럼의 예전 이름(x_right·x_테이블)을 지금 규칙(대상_컬럼, parse.rname_for)으로 옮긴다.
저장한 검색(query/search_app.upgrade_search)과 대시보드 카드(dashboard/dash_calc.upgraded)를 불러올 때 쓴다. 명세는 plans/phase-10.md."""
import copy
import json
import re
from collections.abc import Callable

from core import parse

OLD_SUFFIX = "{name}_right"  # 예전 rname_for의 첫 겹침 이름 꼴
KEEP_KEYS = ("schema", "base", "union", "name", "label", "key")  # 이름을 바꾸지 않는 검색 요청 칸(테이블·연결 자리)


def old_rname(left: list[str], right: list[str], name: str) -> str:
    """[SPECIFICATION] plans/phase-10.md#old_rname
    예전 parse.rname_for 규칙으로 겹친 오른쪽 컬럼의 이름 꼴을 돌려준다."""
    taken = {*left, *right}

    def clash(rname: str) -> bool:
        return any(c in left and rname.format(name=c) in taken for c in right)

    rname = OLD_SUFFIX
    if clash(rname):
        rname = "{name}_" + re.sub(r"\W", "_", name)
        while clash(rname):
            rname += "_"
    return rname


def renames(joins: list[str], base_columns: list[str], tables: list[str], get_columns: Callable[[str], list[str]]) -> dict[str, str]:
    """[SPECIFICATION] plans/phase-10.md#renames
    조인 줄을 예전·지금 규칙으로 다시 놓아 {예전 이름: 새 이름}을 돌려준다."""
    old_cols, new_cols, found = list(base_columns), list(base_columns), {}
    for line in joins:
        table, how, pairs, _ = parse.parse_join(upgrade({"joins": [line]}, found)["joins"][0], tables)
        if how in ("semi", "anti"):
            continue
        merged = {r for left, r in pairs if left == r} if how == "inner" else set()
        rest = [c for c in get_columns(table) if c not in merged]
        old, new = old_rname(old_cols, rest, table), parse.rname_for(new_cols, rest, table)
        for c in rest:
            o = old.format(name=c) if c in old_cols else c
            n = new.format(name=c) if c in new_cols else c
            old_cols.append(o)
            new_cols.append(n)
            if o != n:
                found[o] = n
    return found


def upgrade(body: dict, mapping: dict[str, str]) -> dict:
    """[SPECIFICATION] plans/phase-10.md#upgrade
    검색 요청 안의 컬럼 이름을 mapping대로 한 번에 바꾼 새 사본을 돌려준다."""
    if mapping:
        pattern = re.compile("|".join(f"(?<![\\w]){re.escape(o)}(?![\\w])" for o in sorted(mapping, key=len, reverse=True)))
        sub = lambda text: pattern.sub(lambda m: mapping[m[0]], text)  # noqa: E731
    else:
        sub = str

    def walk(value):
        if isinstance(value, str):
            return sub(value)
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        return value

    def join_line(line: str) -> str:
        head = re.match(r"\s*\S*", line)[0]
        return head + sub(line[len(head):])

    return {
        k: copy.deepcopy(v) if k in KEEP_KEYS else [join_line(line) for line in v] if k == "joins" else walk(v)
        for k, v in body.items()
    }


def search_renames(body: dict) -> dict[str, str]:
    """[SPECIFICATION] plans/phase-10.md#search_renames
    지금 연결에서 검색 요청의 예전 조인 이름 표를 구한다(연결 스레드 안에서 부른다)."""
    joins = body.get("joins") or []
    text = json.dumps({k: v for k, v in body.items() if k not in KEEP_KEYS}, ensure_ascii=False)
    marks = [OLD_SUFFIX.format(name="")] + ["_" + re.sub(r"\W", "_", line.split()[0]) for line in joins if line.split()]
    if not joins or not any(m in text for m in marks):
        return {}
    from core import handoff, querying, server  # 늦은 import(순환 import)

    schema = body.get("schema") or None
    vts = {} if schema else server.virtual_tables()
    tables = server.table_names(querying.get_con(), schema) + list(vts) + handoff.names()
    base = querying.joined({**body, "joins": []})[0].columns
    return renames(joins, list(base), tables, lambda t: list(querying.table_of(t, schema, vts).columns))
