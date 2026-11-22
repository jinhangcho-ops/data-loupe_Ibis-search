"""(B11) 대시보드 데이터 받기와 이 PC 계산: 연결한 DB(원격)에는 테이블마다 칸 하나(같은 연결·테이블·암호화 묶음이면 대시보드끼리 같이 쓴다)로, 쓰는 대시보드들의 컬럼 합집합을
그 테이블 컬럼에 걸린 값 조건·기간(카드마다 AND, 카드·대시보드끼리 OR)으로 거른 줄만 요청해 이 PC에 Parquet(고른 암호화)로 두고, 카드의 조인·집계·정렬·카드별 조건은
그 파일을 원래 테이블 이름의 뷰로 건 이 PC DuckDB(대시보드를 연 동안 하나)에서 지금의 카드 계산(dash_calc)으로 한다. 이미 받은 줄은 다시 받지 않는다(held 설명의 부정으로 거른다).
없는 것만 받기의 새 줄은 마지막으로 받은 줄 30개 뒤의 줄이다. 폴더·설명 파일은 dash_files, 열쇠는 dash_keys. 명세: plans/dash-blocks.md "B11 세부 명세"."""
import base64
import contextlib
import functools
import itertools
import operator
import re
import secrets
import threading
import time
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import sqlglot.expressions

from core import calc, dash_store, memory, parse,querying, result, scan_size, scratch, server, settings, source, use_counts
from dashboard import dash_calc, dash_files, dash_keys
from lazy import Later, ibis

prep_geo = Later("geo.prep_geo")  # 좌표 단계가 있는 카드만 올린다(numpy가 따라온다)
held = Later("core.held")  # 연결이 원격 DB인지 보는 곳(uses_pc)

PART = "part-{n:06d}.parquet"  # 받은 조각 파일 이름(1부터)
TEMP_PREFIX = "~"  # 쓰는 중인 조각 파일 이름 앞글자(다 쓰면 바꿔치기)
SLOT_KEYS = ("source", "schema", "table", "group", "owner")  # 칸 id를 정하는 것: 연결·테이블·암호화 묶음(사용자 답 2-7·2-7b), 따로 둔 칸이면 그 대시보드 id(같이 쓰면 "", 사용자 답 4-2)
TAIL_ROWS = 30  # 없는 것만 받기: 기억하는 마지막 줄 수(사용자 답 2-5)
PC_DB = "pc.duckdb"  # 이 PC DuckDB 파일 이름(scratch 임시 폴더 안, 뷰만 둔다)
KEY_PREFIX = "dl_"  # DuckDB add_parquet_key에 등록하는 열쇠 이름 앞글자(뒤는 열쇠의 def_hash 16자)
KEY_NAME_RE = re.compile(r"dl_[0-9a-f]{16}")  # 등록할 열쇠 이름 모양(이 꼴만 PRAGMA에 넣는다)
KEY_VALUE_RE = re.compile(r"[A-Za-z0-9+/]{22}==|[A-Za-z0-9+/]{43}=")  # 등록할 열쇠 값 모양(16·32바이트 base64만)
INCOMING = "dash_pull_incoming"  # 받는 묶음(Arrow)을 이 PC DuckDB에 잠깐 등록하는 이름
OPEN = {"dash": "", "con": None, "folder": None, "views": {}, "keys": set(), "last": 0.0, "busy": 0, "timer": None}  # 대시보드를 연 동안 쓰는 이 PC DuckDB 하나(사용자 답 2-19)와 건 뷰 {(스키마, 테이블): 조각 경로들}·등록한 열쇠 이름·마지막 사용 시각·쓰는 중인 요청 수·쉬면 닫는 타이머(사용자 답 3-4)
FRESHED = {"token": "", "slots": set()}  # 갱신 한 번(token) 안에서 이미 새로 받은 칸 id들. token이 바뀌면 비운다
NEED_MARK = "'{table}' 테이블의 새 줄을 알아볼 컬럼을 대시보드 설정에서 골라 주세요(없는 것만 받기)."
BAD_MARK = "새 줄을 알아볼 컬럼 '{column}'은 정수·날짜·시각 컬럼이어야 해요."
TAIL_LOST = "'{table}' 테이블에서 마지막으로 받은 줄을 DB에서 찾지 못했어요(고쳐지거나 지워졌어요). 알림(종)에서 기간을 정해 다시 받거나 대시보드 설정의 [전체 갱신]을 눌러 주세요."
DAY = timedelta(days=1)  # 받은 기간이 맞닿는지 볼 때의 하루
MINUTE = 60  # 설정 pc_idle(분)을 초로 바꿀 때
MIN_WAIT = 1  # 쉬는 시간 타이머를 다시 걸 때 최소 대기(초)
NOT_MINE = "이 대시보드가 쓰는 받은 데이터가 아니에요."
NOT_LOST = "다시 받을 칸이 아니에요."
MANY_DATES = "기간 컬럼이 하나가 아니라 기간을 정해 받을 수 없어요. [전체 갱신]을 눌러 주세요."
BAD_RANGE = "다시 받을 기간은 YYYY-MM-DD 꼴로, 시작이 끝보다 늦지 않게 정해 주세요."
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")  # 다시 받을 기간의 날짜 글자 모양


def card_tables(card: dict) -> list[tuple[str, str]]:
    """[SPECIFICATION] plans/dash-blocks.md#card_tables
    카드가 읽는 모든 테이블 [(스키마, 이름)](조회·이어붙이기·조인·계산 줄·전처리 구성·좌표 단계·지도 영역 표, 가상 테이블 포함). DB에는 메타데이터만 묻는다."""
    src, view = card["source"], card["view"]
    found: list[tuple[str, str]] = []
    body, recipe = src.get("body"), src.get("recipe")
    if isinstance(body, dict):
        found += [(body.get("schema") or "", name) for name in [*querying.used_tables(body), *calc.used_tables(body.get("calcs") or [])]]
    if isinstance(recipe, dict):
        schema = recipe.get("schema") or ""
        found += [(schema, recipe["base"])] + [(schema, name) for name in calc.used_tables(recipe.get("formulas") or [])]
        if recipe.get("geo"):
            exprs = prep_geo.reference_exprs(prep_geo.check(recipe["geo"]), server.get_con())
            found += [(op.namespace.database or "", op.name) for _, expr in exprs for op in expr.op().find(ibis.expr.operations.DatabaseTable)]
    regions = (view.get("geo") or {}).get("regions") or {}
    if card["kind"] == "geomap" and regions.get("table"):
        found.append((regions.get("schema") or "", regions["table"]))
    return list(dict.fromkeys(found))


def dash_needs(dash: dict, ctx: dict | None, extra: dict | None = None) -> list[dict]:
    """[SPECIFICATION] plans/dash-blocks.md#dash_needs
    이 대시보드가 테이블마다 필요한 것 하나: 칸 열쇠, 컬럼 합집합, 카드별 조건 묶음(terms), 새 줄 컬럼, 창 기간."""
    pull = dash_store.check_pull(dash.get("pull"))
    now = dash_store.current_source()
    w = dash_calc.fetch_window(ctx)
    window = [w["start"], w["end"]] if w else None
    group = dash_keys.group_of(dash["id"], pull["crypt"])
    cards = [c for c in dash["cards"] if c["kind"] not in dash_store.FRAME_KINDS and any((c.get("source") or {}).get(k) for k in ("body", "recipe", "analysis"))
             and use_counts.same_source(c["source"]["key"], now["key"])]
    if extra:
        cards = [extra if c["id"] == extra["id"] else c for c in cards] + ([] if any(c["id"] == extra["id"] for c in cards) else [extra])
    found: dict[tuple[str, str], dict] = {}  # (스키마, 테이블) → 카드들의 terms·컬럼(None = 전부)·쌓는 테이블인지

    def use(key: tuple[str, str], term: dict, columns: set[str] | None, stacked: bool = False) -> None:
        entry = found.setdefault(key, {"terms": [], "columns": [], "stacked": False})
        entry["terms"].append(term)
        entry["columns"].append(columns)
        entry["stacked"] = entry["stacked"] or stacked

    for card in cards:
        src = card["source"]
        body, recipe = src.get("body"), src.get("recipe")
        if card["kind"] in dash_store.GONE_KINDS or (card["kind"] == "table" and isinstance(body, dict) and not body.get("series")):
            continue
        done: set[tuple[str, str]] = set()
        if isinstance(body, dict):
            schema = body.get("schema") or ""
            names = querying.used_tables(body)
            expr, origins = querying.joined(body)
            union = body.get("union") if isinstance(body.get("union"), dict) else {}
            stacked = names[:1 + len(union.get("tables") or [])]
            joins = [parse.parse_join(line, names) for line in body.get("joins") or []]
            tables = {n: querying.table_of(n, schema or None) for n in names}
            date = dash_calc.period_column(card) or ""
            lines = [(line, dash_calc.pred_fields(parse.parse_period(line, expr))) for line in body.get("periods") or []]
            conds = [(cond, dash_calc.pred_fields([parse.predicate(cond, expr)])) for cond in body.get("conditions") or []]
            null_base = any(j[1] in ("right", "outer", "full") for j in joins)
            # asof 조인의 시각 쌍(on)도 조인 키로 본다
            join_keys = [(name, [*pairs, *([on] if on else [])]) for name, _, pairs, on in joins]
            places = [(n, body["base"], stacked, null_base, True) for n in stacked] + [(name, name, [name], how != "inner", False) for name, how, _, _ in joins]
            twice = {n for n in names if names.count(n) > 1}
            cols = dash_calc.card_columns(card)
            keys = [pair for _, pairs in join_keys for pair in pairs]
            need = None if cols is None else (set(cols) | {c for _, used in lines + conds for c in used}
                                              | {f["column"] for f in dash.get("filters") or [] if f.get("column") in expr.columns}
                                              | {left for left, _ in keys} | ({date} if date else set())
                                              | (dash_calc.linked_columns(dash, card) & set(expr.columns)))

            def sendable(item: object, used: set[str], role: str, group_names: list[str], table: str, null: bool, period: bool) -> bool:
                if parse.SOURCE in used or not all(origins.get(c) == role and all(c in tables[g].columns for g in group_names) for c in used):
                    return False
                if not null or period:
                    return True
                empty = ibis.memtable({c: [None] for c in used}, schema={c: tables[table].schema()[c] for c in used})
                return empty.filter(parse.predicate(item, empty)).count().to_pyarrow().as_py() == 0

            for name, role, group_names, null, is_stacked in places:
                tcols = tables[name].columns
                if name in twice:
                    term = {"lines": [], "conds": [], "date": ""}
                else:
                    term = {"lines": [line for line, used in lines if sendable(line, used, role, group_names, name, null, True)],
                            "conds": [cond for cond, used in conds if sendable(cond, used, role, group_names, name, null, False)],
                            "date": date if date and origins.get(date) == role and date in tcols else ""}
                if need is None:
                    columns = None
                elif is_stacked:
                    columns = need & set(tcols)
                else:
                    columns = {c for c in tcols if c in need or any(n.endswith("_" + c) for n in need)} | {r for joined_name, pairs in join_keys if joined_name == name for _, r in pairs}
                use((schema, name), term, columns, is_stacked)
                done.add((schema, name))
        if isinstance(recipe, dict):
            schema = recipe.get("schema") or ""
            base_cols = querying.table_of(recipe["base"], schema or None).columns
            use((schema, recipe["base"]), {"lines": [], "conds": list(recipe.get("filters") or []), "date": src.get("date") if src.get("date") in base_cols else ""}, None)
            done.add((schema, recipe["base"]))
        for key in card_tables(card):
            if key not in done:
                use(key, {"lines": [], "conds": [], "date": ""}, None)

    def covers(y: dict, x: dict) -> bool:
        return all(line in x["lines"] for line in y["lines"]) and all(c in x["conds"] for c in y["conds"]) and y["date"] in (x["date"], "")

    needs = []
    for (schema, table), entry in found.items():
        raw = entry["terms"]
        terms = [t for i, t in enumerate(raw) if t not in raw[:i]]
        terms = [x for i, x in enumerate(terms) if not any(j != i and covers(y, x) and (j < i or not covers(x, y)) for j, y in enumerate(terms))]
        mark = ""
        if pull["range"] == "new" and entry["stacked"]:
            mark = next((m["column"] for m in pull["marks"] if (m["schema"], m["table"]) == (schema, table)), None)
            if mark is None:
                raise ValueError(NEED_MARK.format(table=table))
            t = querying.table_of(table, schema or None)
            parse.check_name(mark, t.columns, "컬럼")
            dtype = t.schema()[mark]
            if not (dtype.is_integer() or dtype.is_date() or dtype.is_timestamp()):
                raise ValueError(BAD_MARK.format(column=mark))
        columns = None if any(c is None for c in entry["columns"]) else sorted(set().union(*entry["columns"]) | ({mark} if mark else set()))
        ident = {"source": now["key"], "schema": schema, "table": table, "group": group}
        own = dash_store.def_hash({**ident, "owner": dash["id"]})
        slot = own if dash_files.read_manifest(own) is not None else dash_store.def_hash({**ident, "owner": ""})
        needs.append({"slot": slot, "source": now["key"], "schema": schema, "table": table, "virtual": schema == "" and table in server.virtual_tables(),
                      "group": group, "columns": columns, "terms": terms, "mark": mark, "window": window})
    return needs


def uncovered(window: list[str | None] | None, covered: list[list[str | None]]) -> list[list[str | None]]:
    """[SPECIFICATION] plans/dash-blocks.md#uncovered
    받을 기간(window, None = 전체)에서 이미 받은 기간들(covered)에 없는 구간들 [[시작|None, 끝|None]](날짜 글자, 양끝 포함)."""
    lo, hi = (date.fromisoformat(v) if v else edge for v, edge in zip(window or [None, None], (date.min, date.max)))
    spans = sorted((date.fromisoformat(s) if s else date.min, date.fromisoformat(e) if e else date.max) for s, e in covered)
    gaps: list[list[str | None]] = []
    cur = lo

    def show(day: date, edge: date) -> str | None:
        return None if day == edge else day.isoformat()

    for start, end in spans:
        if end < cur:
            continue
        if start > hi:
            break
        if start > cur:
            gaps.append([show(cur, date.min), show(start - DAY, date.max)])
        if end >= hi:
            return gaps
        cur = end + DAY
    gaps.append([show(cur, date.min), show(hi, date.max)])
    return gaps


def new_rows(fetched: "ibis.Table", tail: "ibis.Table", keys: list[str]) -> "ibis.Table | None":
    """[SPECIFICATION] plans/dash-blocks.md#new_rows
    keys 차례로 정렬한 fetched에서 tail(마지막으로 받은 줄들, 같은 차례)이 빠짐없이 이어 붙어 나오는 마지막 자리 다음 줄들. 그런 자리가 없으면 None."""
    f = fetched.mutate(rn=ibis.row_number().over(order_by=keys))
    k = tail.mutate(i=ibis.row_number().over(order_by=keys))
    n = tail.count().to_pyarrow().as_py()  # 값 하나: 다른 표의 스칼라 서브쿼리를 피한다(Polars 백엔드에 없음)
    joined = f.join(k, [f[c].identical_to(k[c]) for c in tail.columns])
    starts = joined.mutate(start=joined.rn - joined.i).group_by("start").aggregate(hits=ibis._.count())
    start = starts.filter(starts.hits == n)["start"].max().to_pyarrow().as_py()
    if start is None:
        return None
    return f.filter(f.rn >= start + n).order_by("rn").drop("rn")


def pull_slot(need: dict, dash: dict, renew: bool, full: bool = False) -> dict:
    """[SPECIFICATION] plans/dash-blocks.md#pull_slot
    필요한 것 하나(need)의 칸을 필요하면 DB에서 받아(전부·아직 없는 줄·새 줄) 조각으로 쓰고 pull.json을 고쳐 그 내용을 돌려준다."""
    with dash_files.LOCK:
        slot = need["slot"]
        man = dash_files.read_manifest(slot)
        folder = dash_files.root() / slot
        if man is not None:
            salt = man["crypt"]["salt"]
        elif need["group"].startswith("pass:"):
            salt = base64.b64encode(secrets.token_bytes(dash_keys.SALT_BYTES)).decode()
        else:
            salt = ""
        k = dash_keys.slot_key(need["group"], dash["id"], salt)
        if OPEN["con"] is not None and OPEN["dash"] != dash["id"]:
            close_pc()
        if OPEN["con"] is None:
            pc_folder = scratch.make()
            opened = source.open_duckdb(Path(pc_folder, PC_DB), False, settings.get("memory"), settings.get("threads"))
            if need["group"] != "none":
                opened.raw_sql("SET temp_directory = ''")
            OPEN.update(dash=dash["id"], con=opened, folder=pc_folder, views={}, keys=set())
        local = OPEN["con"]
        OPEN["last"] = time.monotonic()
        if OPEN["timer"] is not None:
            OPEN["timer"].cancel()
        OPEN["timer"] = threading.Timer(settings.get("pc_idle") * MINUTE, close_idle)
        OPEN["timer"].daemon = True
        OPEN["timer"].start()
        write_enc: dict = {}
        read_enc: dict = {}
        if k is not None:
            name = KEY_PREFIX + dash_store.def_hash(k)
            if name not in OPEN["keys"]:
                if not (KEY_NAME_RE.fullmatch(name) and KEY_VALUE_RE.fullmatch(k)):
                    raise ValueError("받은 데이터 열쇠 모양이 맞지 않아요.")
                local.raw_sql(sqlglot.expressions.Pragma(this=sqlglot.expressions.Anonymous(
                    this="add_parquet_key", expressions=[sqlglot.expressions.Literal.string(name), sqlglot.expressions.Literal.string(k)])).sql("duckdb"))
                OPEN["keys"].add(name)
            write_enc = {"encryption_config": {"footer_key": name}}
            read_enc = {"encryption_config": sqlglot.expressions.Struct(expressions=[sqlglot.expressions.PropertyEQ(
                this=sqlglot.expressions.to_identifier("footer_key"), expression=sqlglot.expressions.Literal.string(name))])}
        t = querying.table_of(need["table"], need["schema"] or None)

        def term_pred(term: dict, span: list[str | None] | None) -> "ibis.Value":
            preds = [p for line in term["lines"] for p in parse.parse_period(line, t)] + [parse.predicate(cond, t) for cond in term["conds"]]
            if term["date"] and span:
                col = t[term["date"]]
                kind = col.type()
                start, end = span
                if kind.is_timestamp():
                    preds += ([col >= ibis.timestamp(start)] if start else []) + ([col < ibis.timestamp((date.fromisoformat(end) + DAY).isoformat())] if end else [])
                else:
                    preds += ([col >= ibis.literal(start, type=kind)] if start else []) + ([col <= ibis.literal(end, type=kind)] if end else [])
            return functools.reduce(operator.and_, preds) if preds else ibis.literal(True)

        def any_of(preds: list) -> "ibis.Value":
            return functools.reduce(operator.or_, preds) if preds else ibis.literal(False)

        def want(users: dict) -> "ibis.Value":
            return any_of([term_pred(term, u["window"]) for u in users.values() for term in u["terms"]])

        def held_pred(held: list) -> "ibis.Value":
            return any_of([term_pred(h["term"], h["range"]) for h in held])

        def next_number(parts: list[str]) -> int:
            return max((int(Path(p).stem.split("-")[1]) for p in parts), default=0) + 1

        def fetch(pred: "ibis.Value", columns: list[str] | None, tmp: Path) -> int:
            expr = t.filter(pred)
            if columns is not None:
                expr = expr.select(*columns)
            con = server.get_con()
            sql, dialect = server.expr_sql(con, expr)
            found = server.run(lambda: scan_size.limit_check(con, sql, server.state["opened"], dialect))
            if found["over"] or found["blocked"]:
                raise ValueError(found["text"])
            expr = expr.mutate(**{c: expr[c].cast("string") for c, kind in expr.schema().items() if kind.is_interval()})

            def land() -> None:
                import pyarrow
                got = result.batches(expr)
                first = next(got, None)
                schema = expr.schema().to_pyarrow() if first is None else first.schema
                local.con.register(INCOMING, pyarrow.RecordBatchReader.from_batches(schema, itertools.chain([] if first is None else [first], got)))
                try:
                    local.table(INCOMING).to_parquet(tmp, **write_enc)
                finally:
                    local.con.unregister(INCOMING)

            folder.mkdir(parents=True, exist_ok=True)
            try:
                server.run(land)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            return local.read_parquet([str(tmp)], **read_enc).count().to_pyarrow().as_py()

        entry = {"columns": need["columns"], "terms": need["terms"], "window": need["window"], "mark": need["mark"]}
        users = {**(man["users"] if man else {}), dash["id"]: entry}
        wanted = [u["columns"] for u in users.values()]
        union = None if any(c is None for c in wanted) else sorted(set().union(*wanted) | {u["mark"] for u in users.values() if u["mark"]})
        whole = (man is None or full or (man["columns"] is not None and (union is None or not set(union) <= set(man["columns"])))
                 or (renew and (need["mark"] == "" or man["rows"] == 0)))
        old_parts = man["parts"] if man else []
        parts = list(old_parts)
        held = list(man["held"]) if man else []
        columns = union if whole else man["columns"]
        rows = 0 if whole else man["rows"]
        pulled_at = man["pulled_at"] if man else None
        lost = man["lost"] if man else None

        def save() -> dict:
            out = {"v": dash_files.VERSION, "source": need["source"], "schema": need["schema"], "table": need["table"], "virtual": need["virtual"],
                   "crypt": {"group": need["group"], "salt": salt}, "columns": columns, "held": held, "users": users,
                   "parts": parts, "rows": rows, "pulled_at": pulled_at, "lost": lost}
            dash_files.write_manifest(slot, out)
            return out

        if whole:
            raw = [{"term": term, "range": u["window"]} for u in users.values() for term in u["terms"]]
            held = [h for i, h in enumerate(raw) if h not in raw[:i]]
            part = PART.format(n=next_number(old_parts))
            rows = fetch(want(users), columns, folder / (TEMP_PREFIX + part))
            (folder / (TEMP_PREFIX + part)).replace(folder / part)
            parts, pulled_at, lost = [part], dash_store.now_text(), None
            out = save()
            for p in old_parts:
                (folder / p).unlink(missing_ok=True)
            OPEN["views"].pop((need["schema"], need["table"]), None)
            return out
        if any(uncovered(need["window"], [h["range"] or [None, None] for h in held if h["term"] == term]) for term in need["terms"]):
            part = PART.format(n=next_number(parts))
            got = fetch(want({dash["id"]: entry}) & ~held_pred(held).fill_null(False), columns, folder / (TEMP_PREFIX + part))
            if got:
                (folder / (TEMP_PREFIX + part)).replace(folder / part)
                parts.append(part)
                rows += got
            else:
                (folder / (TEMP_PREFIX + part)).unlink(missing_ok=True)
            added = [{"term": term, "range": need["window"]} for term in need["terms"]]
            held += [h for i, h in enumerate(added) if h not in held and h not in added[:i]]
            pulled_at = dash_store.now_text()
        if renew and need["mark"]:
            mark = need["mark"]
            have = local.read_parquet([str(folder / p) for p in parts], **read_enc)
            keys = [mark, *sorted(c for c in have.columns if c != mark)]
            tail = have.order_by([ibis.desc(c) for c in keys]).limit(TAIL_ROWS)
            boundary = tail[mark].min().to_pyarrow().as_py()
            part = PART.format(n=next_number(parts))
            tmp = folder / (TEMP_PREFIX + part)
            fetch(held_pred(held) & (t[mark] >= ibis.literal(boundary, type=t[mark].type())), columns, tmp)
            fresh = new_rows(local.read_parquet([str(tmp)], **read_enc), tail, keys)
            if fresh is None:
                tmp.unlink(missing_ok=True)
                dates = {h["term"]["date"] for h in held}
                only = next(iter(dates)) if len(dates) == 1 else ""
                lost = only or "-"
                save()
                raise ValueError(TAIL_LOST.format(table=need["table"]))
            got = fresh.count().to_pyarrow().as_py()
            if got:
                try:
                    fresh.to_parquet(folder / part, **write_enc)
                except BaseException:
                    (folder / part).unlink(missing_ok=True)
                    raise
                finally:
                    tmp.unlink(missing_ok=True)
                parts.append(part)
                rows += got
            else:
                tmp.unlink(missing_ok=True)
            pulled_at, lost = dash_store.now_text(), None
        return save()


def ensure(dash: dict, ctx: dict | None, token: str, tables: list[tuple[str, str]], extra: dict | None = None, full: bool = False) -> dict[tuple[str, str], list[Path]]:
    """[SPECIFICATION] plans/dash-blocks.md#ensure
    tables마다 받은 조각 파일 목록 {(스키마, 테이블): [경로]}(token 갱신 안에서 칸마다 한 번 새로 받는다, full이면 전부 다시)."""
    needs = {(n["schema"], n["table"]): n for n in dash_needs(dash, ctx, extra)}
    if token != FRESHED["token"]:
        FRESHED.update(token=token, slots=set())
    files: dict[tuple[str, str], list[Path]] = {}
    for schema, table in tables:
        need = needs.get((schema, table))
        if need is None:
            raise ValueError(f"'{table}' 테이블의 받기 조건을 만들지 못했어요.")
        renew = token != "" and need["slot"] not in FRESHED["slots"]
        man = pull_slot(need, dash, renew, full)
        if renew:
            FRESHED["slots"].add(need["slot"])
        files[(schema, table)] = [dash_files.root() / need["slot"] / p for p in man["parts"]]
    return files


def held_files(dash: dict, ctx: dict | None) -> tuple[dict[tuple[str, str], list[Path]], bool]:
    """[SPECIFICATION] plans/dash-blocks.md#held_files
    DB에 묻지 않고 그 대시보드가 쓰는 칸들의 조각 파일 목록과, 보는 기간에 받아 두지 않은 구간이 있는지(연결 없이 계산·필터 값 목록)."""
    group = dash_keys.group_of(dash["id"], dash_store.check_pull(dash.get("pull"))["crypt"])
    w = dash_calc.fetch_window(ctx)
    window = [w["start"], w["end"]] if w else None
    files: dict[tuple[str, str], list[Path]] = {}
    missing = False
    for slot, man in dash_files.slots(dash["id"]).items():
        if man["crypt"]["group"] != group:
            raise ValueError("암호화를 바꿔서 받아 둔 파일을 읽을 수 없어요. 연결하면 다시 받아요.")
        files[(man["schema"], man["table"])] = [dash_files.root() / slot / p for p in man["parts"]]
        if ctx is not None:
            missing = missing or any(uncovered(window, [h["range"] or [None, None] for h in man["held"] if h["term"] == term])
                                     for term in man["users"][dash["id"]]["terms"])
    return files, missing


def files_for(card: dict, ctx: dict | None, dash_id: str, token: str) -> tuple[dict[tuple[str, str], list[Path]], bool]:
    """[SPECIFICATION] plans/dash-blocks.md#files_for
    카드 하나를 이 PC에서 계산할 받은 파일과 빠진 구간이 있는지(연결이 있으면 ensure, 없으면 held_files)."""
    d = dash_store.get_dashboard(dash_id)
    if server.state["con"] is None:
        return held_files(d, ctx)
    return ensure(d, ctx, token, server.run(lambda: card_tables(card)), extra=card), False


@contextlib.contextmanager
def on_pc(files: dict[tuple[str, str], list[Path]], dash_id: str) -> Iterator["ibis.BaseBackend"]:
    """[SPECIFICATION] plans/dash-blocks.md#on_pc
    그 대시보드의 이 PC DuckDB(OPEN, 없으면 열고 다른 대시보드 것이면 바꿈)에 받은 파일을 원래 테이블 이름의 뷰로 걸고 지금 연결 자리에 잠깐 끼운 뒤 되돌린다."""
    with dash_files.LOCK:
        mans = {paths[0].parent.name: dash_files.read_manifest(paths[0].parent.name) for paths in files.values()}
        if OPEN["con"] is not None and OPEN["dash"] != dash_id:
            close_pc()
        if OPEN["con"] is None:
            folder = scratch.make()
            opened = source.open_duckdb(Path(folder, PC_DB), False, settings.get("memory"), settings.get("threads"))
            # 대시보드의 암호화 묶음은 그 칸들의 group이다(none이 아니면 디스크 넘침을 끈다)
            if any(man["crypt"]["group"] != "none" for man in mans.values()):
                opened.raw_sql("SET temp_directory = ''")
            OPEN.update(dash=dash_id, con=opened, folder=folder, views={}, keys=set())
        local = OPEN["con"]
        for (schema, table), paths in files.items():
            if OPEN["views"].get((schema, table)) == tuple(paths):
                continue
            man = mans[paths[0].parent.name]
            k = dash_keys.slot_key(man["crypt"]["group"], dash_id, man["crypt"]["salt"])
            enc = {}
            if k is not None:
                name = KEY_PREFIX + dash_store.def_hash(k)
                if name not in OPEN["keys"]:
                    if not (KEY_NAME_RE.fullmatch(name) and KEY_VALUE_RE.fullmatch(k)):
                        raise ValueError("받은 데이터 열쇠 모양이 맞지 않아요.")
                    local.raw_sql(sqlglot.expressions.Pragma(this=sqlglot.expressions.Anonymous(
                        this="add_parquet_key", expressions=[sqlglot.expressions.Literal.string(name), sqlglot.expressions.Literal.string(k)])).sql("duckdb"))
                    OPEN["keys"].add(name)
                enc = {"encryption_config": sqlglot.expressions.Struct(expressions=[sqlglot.expressions.PropertyEQ(
                    this=sqlglot.expressions.to_identifier("footer_key"), expression=sqlglot.expressions.Literal.string(name))])}
            if schema:
                local.create_database(schema, force=True)
            local.create_view(table, local.read_parquet([str(p) for p in paths], **enc), database=schema or None, overwrite=True)
            OPEN["views"][(schema, table)] = tuple(paths)
        pckey = "pc:" + (next(iter(mans.values()))["source"] if mans else dash_id)
        OPEN["last"] = time.monotonic()
        if OPEN["timer"] is not None:
            OPEN["timer"].cancel()
        OPEN["timer"] = threading.Timer(settings.get("pc_idle") * MINUTE, close_idle)
        OPEN["timer"].daemon = True
        OPEN["timer"].start()
        OPEN["busy"] += 1
        saved = {k: server.state[k] for k in ("con", "names", "tables", "rows")}
        server.state.update(con=local, names={}, tables={}, rows={}, pc=pckey)
    try:
        yield local
    finally:
        with dash_files.LOCK:
            OPEN["busy"] -= 1
        server.state.update(saved)
        server.state.pop("pc", None)


def close_pc() -> None:
    """[SPECIFICATION] plans/dash-blocks.md#close_pc
    열어 둔 이 PC DuckDB를 닫고 그 임시 폴더를 지운다(다른 대시보드로 바꿀 때·대시보드를 지울 때·앱을 끌 때·연결을 바꿀 때)."""
    with dash_files.LOCK:
        if OPEN["timer"] is not None:
            OPEN["timer"].cancel()
        if OPEN["con"] is not None:
            source.close(OPEN["con"])
            scratch.remove(OPEN["folder"])
        OPEN.update(dash="", con=None, folder=None, views={}, keys=set(), last=0.0, busy=0, timer=None)


server.ON_SWITCH.append(close_pc)  # 연결을 바꾸거나 끊으면 닫는다
memory.hold("dash_pc", "대시보드 이 PC 계산(받은 데이터의 뷰)", lambda: "대시보드 1개" if OPEN["con"] is not None else "", close_pc)


def close_idle() -> None:
    """[SPECIFICATION] plans/dash-blocks.md#close_idle
    쉬는 시간 타이머가 부른다: 쓰는 요청이 없고 마지막 사용에서 설정 pc_idle분이 지났으면 close_pc, 아니면 남은 시간으로 다시 건다."""
    with dash_files.LOCK:
        if OPEN["con"] is None:
            return
        left = settings.get("pc_idle") * MINUTE - (time.monotonic() - OPEN["last"])
        if OPEN["busy"] == 0 and left <= 0:
            close_pc()
            return
        OPEN["timer"] = threading.Timer(max(left, MIN_WAIT), close_idle)
        OPEN["timer"].daemon = True
        OPEN["timer"].start()


def uses_pc(dash_id: str) -> bool:
    """[SPECIFICATION] plans/dash-blocks.md#uses_pc
    그 대시보드의 카드를 이 PC에서 계산하는지(계산 방식 pc이고, 지금 연결이 원격 DB이거나 연결 없이 받아 둔 칸이 있다)."""
    if dash_id == "":
        return False
    pull = dash_store.check_pull(dash_store.get_dashboard(dash_id).get("pull"))
    if pull["calc"] != "pc":
        return False
    con = server.state["con"]
    if con is not None:
        return held.remote(con)
    return bool(dash_files.slots(dash_id))


def compute(card: dict, now: str, ctx: dict | None, dash_id: str, token: str) -> dict:
    """[SPECIFICATION] plans/dash-blocks.md#compute
    카드 하나의 값: PC 방식이면 받은 파일에서(빠진 구간이 있으면 note에 GAP_NOTE), 아니면 지금처럼 DB에서 dash_calc.compute_filtered로 구한다."""
    if not uses_pc(dash_id):
        return dash_calc.compute_filtered(card, now, ctx)
    files, missing = files_for(card, ctx, dash_id, token)
    with on_pc(files, dash_id):
        data = dash_calc.compute_filtered(card, now, ctx)
    if missing:
        data["note"] = " ".join(x for x in (data.get("note"), dash_store.GAP_NOTE) if x)
    return data


def full(dash: dict, ctx: dict | None) -> list[str]:
    """[SPECIFICATION] plans/dash-blocks.md#full
    [전체 갱신]: 그 대시보드가 쓰는 칸을 모두 전부 다시 받는다. 다른 대시보드와 같이 쓰던 칸은 이 대시보드만 따로 칸을 만들어 받는다(사용자 답 4-2). 받은 칸 id들을 돌려준다."""
    slots: list[str] = []
    for need in dash_needs(dash, ctx):
        man = dash_files.read_manifest(need["slot"])
        if man is None or set(man["users"]) <= {dash["id"]}:
            pull_slot(need, dash, True, full=True)
            slots.append(need["slot"])
            continue
        own = dash_store.def_hash({k: need[k] for k in SLOT_KEYS if k != "owner"} | {"owner": dash["id"]})
        pull_slot({**need, "slot": own}, dash, True, full=True)
        dash_files.drop_user(need["slot"], dash["id"])
        slots.append(own)
    return slots


def refetch_range(dash: dict, slot: str, start: str, end: str) -> dict:
    """[SPECIFICATION] plans/dash-blocks.md#refetch_range
    마지막 줄을 못 찾은 칸에서 사용자가 정한 기간의 줄을 지우고 그 기간을 다시 받는다(사용자 답 2-3)."""
    man = dash_files.read_manifest(slot)
    if man is None or dash["id"] not in man["users"]:
        raise ValueError(NOT_MINE)
    column = man["lost"]
    if column is None:
        raise ValueError(NOT_LOST)
    if column == "-":
        raise ValueError(MANY_DATES)
    if not (DAY_RE.fullmatch(start) and DAY_RE.fullmatch(end)) or date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError(BAD_RANGE)
    with dash_files.LOCK:
        group, salt = man["crypt"]["group"], man["crypt"]["salt"]
        k = dash_keys.slot_key(group, dash["id"], salt)
        if OPEN["con"] is not None and OPEN["dash"] != dash["id"]:
            close_pc()
        if OPEN["con"] is None:
            pc_folder = scratch.make()
            opened = source.open_duckdb(Path(pc_folder, PC_DB), False, settings.get("memory"), settings.get("threads"))
            if group != "none":
                opened.raw_sql("SET temp_directory = ''")
            OPEN.update(dash=dash["id"], con=opened, folder=pc_folder, views={}, keys=set())
        local = OPEN["con"]
        OPEN["last"] = time.monotonic()
        if OPEN["timer"] is not None:
            OPEN["timer"].cancel()
        OPEN["timer"] = threading.Timer(settings.get("pc_idle") * MINUTE, close_idle)
        OPEN["timer"].daemon = True
        OPEN["timer"].start()
        write_enc: dict = {}
        read_enc: dict = {}
        if k is not None:
            name = KEY_PREFIX + dash_store.def_hash(k)
            if name not in OPEN["keys"]:
                if not (KEY_NAME_RE.fullmatch(name) and KEY_VALUE_RE.fullmatch(k)):
                    raise ValueError("받은 데이터 열쇠 모양이 맞지 않아요.")
                local.raw_sql(sqlglot.expressions.Pragma(this=sqlglot.expressions.Anonymous(
                    this="add_parquet_key", expressions=[sqlglot.expressions.Literal.string(name), sqlglot.expressions.Literal.string(k)])).sql("duckdb"))
                OPEN["keys"].add(name)
            write_enc = {"encryption_config": {"footer_key": name}}
            read_enc = {"encryption_config": sqlglot.expressions.Struct(expressions=[sqlglot.expressions.PropertyEQ(
                this=sqlglot.expressions.to_identifier("footer_key"), expression=sqlglot.expressions.Literal.string(name))])}
        folder = dash_files.root() / slot
        t = querying.table_of(man["table"], man["schema"] or None)

        def term_pred(table: "ibis.Table", term: dict, span: list[str | None] | None) -> "ibis.Value":
            preds = [p for line in term["lines"] for p in parse.parse_period(line, table)] + [parse.predicate(cond, table) for cond in term["conds"]]
            if term["date"] and span:
                col = table[term["date"]]
                kind = col.type()
                lo, hi = span
                if kind.is_timestamp():
                    preds += ([col >= ibis.timestamp(lo)] if lo else []) + ([col < ibis.timestamp((date.fromisoformat(hi) + DAY).isoformat())] if hi else [])
                else:
                    preds += ([col >= ibis.literal(lo, type=kind)] if lo else []) + ([col <= ibis.literal(hi, type=kind)] if hi else [])
            return functools.reduce(operator.and_, preds) if preds else ibis.literal(True)

        span = {"lines": [], "conds": [], "date": column}
        heldp = functools.reduce(operator.or_, [term_pred(t, h["term"], h["range"]) for h in man["held"]], ibis.literal(False))
        expr = t.filter(heldp & term_pred(t, span, [start, end]))
        if man["columns"] is not None:
            expr = expr.select(*man["columns"])
        con = server.get_con()
        sql, dialect = server.expr_sql(con, expr)
        found = server.run(lambda: scan_size.limit_check(con, sql, server.state["opened"], dialect))
        if found["over"] or found["blocked"]:
            raise ValueError(found["text"])
        expr = expr.mutate(**{c: expr[c].cast("string") for c, kind in expr.schema().items() if kind.is_interval()})
        number = max((int(Path(p).stem.split("-")[1]) for p in man["parts"]), default=0) + 1
        kept, fresh = PART.format(n=number), PART.format(n=number + 1)
        kept_tmp, fresh_tmp = folder / (TEMP_PREFIX + kept), folder / (TEMP_PREFIX + fresh)

        def land() -> None:
            import pyarrow
            got = result.batches(expr)
            first = next(got, None)
            schema = expr.schema().to_pyarrow() if first is None else first.schema
            local.con.register(INCOMING, pyarrow.RecordBatchReader.from_batches(schema, itertools.chain([] if first is None else [first], got)))
            try:
                local.table(INCOMING).to_parquet(fresh_tmp, **write_enc)
            finally:
                local.con.unregister(INCOMING)

        have = local.read_parquet([str(folder / p) for p in man["parts"]], **read_enc)
        try:
            have.filter(~term_pred(have, span, [start, end]).fill_null(False)).to_parquet(kept_tmp, **write_enc)
            server.run(land)
        except BaseException:
            kept_tmp.unlink(missing_ok=True)
            fresh_tmp.unlink(missing_ok=True)
            raise
        kept_rows = local.read_parquet([str(kept_tmp)], **read_enc).count().to_pyarrow().as_py()
        got = local.read_parquet([str(fresh_tmp)], **read_enc).count().to_pyarrow().as_py()
        kept_tmp.replace(folder / kept)
        parts = [kept]
        if got:
            fresh_tmp.replace(folder / fresh)
            parts.append(fresh)
        else:
            fresh_tmp.unlink(missing_ok=True)
        dash_files.write_manifest(slot, {**man, "parts": parts, "rows": kept_rows + got, "pulled_at": dash_store.now_text(), "lost": None})
        for p in man["parts"]:
            (folder / p).unlink(missing_ok=True)
        OPEN["views"].pop((man["schema"], man["table"]), None)
    return {"rows": got}


def rewrite_crypt(dash: dict, old_keys: dict[str, str | None]) -> list[str]:
    """[SPECIFICATION] plans/dash-blocks.md#rewrite_crypt
    암호화를 바꾼 대시보드가 쓰던 옛 칸들({칸 id: 옛 열쇠})을 옛 열쇠로 풀어 새 묶음의 칸으로 다시 쓰고(사용자 답 2-8) 새 칸 id들을 돌려준다."""
    new = dash_keys.group_of(dash["id"], dash_store.check_pull(dash.get("pull"))["crypt"])
    with dash_files.LOCK:
        mans = {slot: dash_files.read_manifest(slot) for slot in old_keys}
        if OPEN["con"] is not None and OPEN["dash"] != dash["id"]:
            close_pc()
        if OPEN["con"] is None:
            pc_folder = scratch.make()
            opened = source.open_duckdb(Path(pc_folder, PC_DB), False, settings.get("memory"), settings.get("threads"))
            if new != "none" or any(man["crypt"]["group"] != "none" for man in mans.values()):
                opened.raw_sql("SET temp_directory = ''")
            OPEN.update(dash=dash["id"], con=opened, folder=pc_folder, views={}, keys=set())
        local = OPEN["con"]
        OPEN["last"] = time.monotonic()
        if OPEN["timer"] is not None:
            OPEN["timer"].cancel()
        OPEN["timer"] = threading.Timer(settings.get("pc_idle") * MINUTE, close_idle)
        OPEN["timer"].daemon = True
        OPEN["timer"].start()

        def encs(k: str | None) -> tuple[dict, dict]:
            if k is None:
                return {}, {}
            name = KEY_PREFIX + dash_store.def_hash(k)
            if name not in OPEN["keys"]:
                if not (KEY_NAME_RE.fullmatch(name) and KEY_VALUE_RE.fullmatch(k)):
                    raise ValueError("받은 데이터 열쇠 모양이 맞지 않아요.")
                local.raw_sql(sqlglot.expressions.Pragma(this=sqlglot.expressions.Anonymous(
                    this="add_parquet_key", expressions=[sqlglot.expressions.Literal.string(name), sqlglot.expressions.Literal.string(k)])).sql("duckdb"))
                OPEN["keys"].add(name)
            return {"encryption_config": {"footer_key": name}}, {"encryption_config": sqlglot.expressions.Struct(expressions=[sqlglot.expressions.PropertyEQ(
                this=sqlglot.expressions.to_identifier("footer_key"), expression=sqlglot.expressions.Literal.string(name))])}

        made: list[str] = []
        for old, man in mans.items():
            if man["crypt"]["group"] == new:
                continue
            entry = man["users"][dash["id"]]
            s2 = dash_store.def_hash({"source": man["source"], "schema": man["schema"], "table": man["table"], "group": new, "owner": ""})
            found = dash_files.read_manifest(s2)
            if found is not None:
                dash_files.write_manifest(s2, {**found, "users": {**found["users"], dash["id"]: entry}})
            else:
                salt = base64.b64encode(secrets.token_bytes(dash_keys.SALT_BYTES)).decode() if new.startswith("pass:") else ""
                _, read_old = encs(old_keys[old])
                write_new, read_new = encs(dash_keys.slot_key(new, dash["id"], salt))
                folder = dash_files.root() / s2
                folder.mkdir(parents=True, exist_ok=True)
                part = PART.format(n=1)
                tmp = folder / (TEMP_PREFIX + part)
                try:
                    local.read_parquet([str(dash_files.root() / old / p) for p in man["parts"]], **read_old).to_parquet(tmp, **write_new)
                except BaseException:
                    tmp.unlink(missing_ok=True)
                    raise
                rows = local.read_parquet([str(tmp)], **read_new).count().to_pyarrow().as_py()
                tmp.replace(folder / part)
                dash_files.write_manifest(s2, {**man, "crypt": {"group": new, "salt": salt}, "users": {dash["id"]: entry}, "parts": [part], "rows": rows})
            dash_files.drop_user(old, dash["id"])
            made.append(s2)
    return made


def unused(dash: dict) -> list[dict]:
    """[SPECIFICATION] plans/dash-blocks.md#unused
    그 대시보드가 쓰는 칸 가운데 지금 카드들이 더는 읽지 않는 테이블의 것 [{slot, schema, table, bytes, shared}](연결 스레드에서)."""
    now = dash_store.current_source()
    cards = [c for c in dash["cards"] if c["kind"] not in dash_store.FRAME_KINDS and any((c.get("source") or {}).get(k) for k in ("body", "recipe", "analysis"))
             and use_counts.same_source(c["source"]["key"], now["key"])]
    used = {t for c in cards for t in card_tables(c)}
    return [{"slot": slot, "schema": man["schema"], "table": man["table"],
             "bytes": sum((dash_files.root() / slot / p).stat().st_size for p in man["parts"] if (dash_files.root() / slot / p).is_file()),
             "shared": bool(set(man["users"]) - {dash["id"]})}
            for slot, man in dash_files.slots(dash["id"]).items() if (man["schema"], man["table"]) not in used]


def mark_tables(dash: dict) -> list[dict]:
    """[SPECIFICATION] plans/dash-blocks.md#mark_tables
    대시보드 카드들이 읽는 테이블과 새 줄 컬럼 후보(정수·날짜·시각 컬럼) 목록(설정 창용, 연결 스레드에서)."""
    now = dash_store.current_source()
    cards = [c for c in dash["cards"] if c["kind"] not in dash_store.FRAME_KINDS and any((c.get("source") or {}).get(k) for k in ("body", "recipe", "analysis"))
             and use_counts.same_source(c["source"]["key"], now["key"])]
    only_joined: dict[tuple[str, str], bool] = {}  # (스키마, 테이블) → 어느 카드에서도 쌓는 테이블이 아니면 True
    for card in cards:
        body, recipe = card["source"].get("body"), card["source"].get("recipe")
        if isinstance(body, dict):
            names = querying.used_tables(body)
            union = body.get("union") if isinstance(body.get("union"), dict) else {}
            stacked = names[:1 + len(union.get("tables") or [])]
            for name in names:
                key = (body.get("schema") or "", name)
                only_joined[key] = only_joined.get(key, True) and name not in stacked
        if isinstance(recipe, dict):
            only_joined[(recipe.get("schema") or "", recipe["base"])] = False
    return [{"schema": schema, "table": table,
             "columns": [c for c, kind in querying.table_of(table, schema or None).schema().items() if kind.is_integer() or kind.is_date() or kind.is_timestamp()],
             "join": joined}
            for (schema, table), joined in only_joined.items()]
