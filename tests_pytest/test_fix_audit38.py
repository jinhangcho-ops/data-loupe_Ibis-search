"""감사 38차: Parquet 가상 테이블에 SQL 식·처리 용량 확인, 가상 테이블 이름의 대소문자(따옴표)."""
import duckdb
import pytest

from lazy import ibis
from core import calc, scan_size, sql_exec, sql_funcs


@pytest.fixture
def pv(tmp_path):
    """Parquet 가상 테이블 pv와 SQL 가상 테이블 sv가 있는 DuckDB 파일 연결."""
    pq = (tmp_path / "p.parquet").as_posix()
    duckdb.sql(f"COPY (SELECT range AS id, range * 10 AS amount FROM range(5)) TO '{pq}' (FORMAT parquet)")
    db = str(tmp_path / "vt.duckdb")
    w = duckdb.connect(db)
    w.sql("CREATE TABLE orders AS SELECT 1 AS id")
    w.close()
    con = ibis.duckdb.connect(db, read_only=True)
    yield con, {"pv": {"parquet": pq}, "sv": {"sql": "SELECT id, id*2 AS d FROM orders"}}
    con.disconnect()


def test_sql_expr_on_parquet_virtual_table(pv):
    con, vts = pv
    expr = sql_exec.virtual_table(con, "pv", vts)
    out = calc.apply(["SQL식 amount + 1 이름 x"], expr).execute()
    assert out["x"].tolist() == [1, 11, 21, 31, 41]


def test_raw_sql_still_checks_user_expression(pv):
    con, vts = pv
    expr = sql_exec.virtual_table(con, "pv", vts)
    with pytest.raises(sql_exec.SecurityViolationError):  # 사용자 식의 파일 읽기는 여전히 막는다
        sql_funcs.raw_sql(expr, "x", "(SELECT count(*) FROM read_csv('a.csv'))")
    with pytest.raises(sql_exec.SecurityViolationError):  # 주석으로 FROM을 바꾸는 것도 막는다(A7)
        sql_funcs.raw_sql(expr, "x", "1) AS x FROM orders --")


def test_limit_check_counts_parquet_virtual_table(pv):
    con, vts = pv
    expr = sql_exec.virtual_table(con, "pv", vts)
    sql, dialect = scan_size.sql_of(con, expr)
    r = scan_size.limit_check(con, sql, None, dialect, {"scan_unknown": "block"})
    assert not r["unknown"] and not r["blocked"] and r["bytes"] > 0 and "Parquet" in r["how"]


class Con:
    def __init__(self, name, dialect):
        self.name, self.dialect = name, dialect


@pytest.mark.parametrize("dialect", ["postgres", "snowflake", "duckdb"])
def test_virtual_table_reference_keeps_exact_case(dialect):
    vts = {"Sales": {"sql": "SELECT 1 AS a"}, "orders_vt": {"sql": "SELECT a FROM sales"}}
    con = Con(dialect, dialect)
    tree = sql_exec.expand(con, sql_exec.check("SELECT Sales.a FROM Sales JOIN orders_vt o ON TRUE", dialect), vts)
    exp = sql_exec.glot()[1]
    refs = {t.this.this: t.this.quoted for t in tree.find_all(exp.Table)}
    assert refs == {"Sales": True, "orders_vt": True}  # 정의 안의 sales도 저장한 이름 "Sales"로
    assert {c.alias_or_name for c in tree.find_all(exp.CTE)} == {"Sales", "orders_vt"}


def test_virtual_table_case_runs_on_duckdb(pv):
    con, _ = pv
    vts = {"Sales": {"sql": "SELECT 1 AS a"}}
    assert con.raw_sql(sql_exec.expand(con, sql_exec.check("SELECT Sales.a FROM sales", "duckdb"), vts).sql(dialect="duckdb")).fetchall() == [(1,)]


def test_failed_piece_lets_running_pieces_finish(tmp_path, monkeypatch, folder):
    """파일 받기: 한 조각이 실패해도 받던 조각은 끝까지 받아 기록한다(다음에 그 범위는 다시 받지 않는다)."""
    import threading
    import time
    from collect import collect_config, collect_http, collect_job
    config = collect_config.normalize({"mode": "file", "file": {"url": "https://files.example.com/a.bin", "chunks": 2}, "balance": {"interval": 0, "retries": 0},
                                       "output": {"format": "csv", "path": str(tmp_path / "a.bin")}})
    path = collect_job.output_path(config)
    found = collect_job.parts(path)
    found["part"].parent.mkdir(parents=True, exist_ok=True)
    record = {"hash": collect_job.digest(config), "mode": "file", "done": [], "rows": 0, "bytes": 0}
    failed = threading.Event()
    H = type("H", (dict,), {"get": dict.get, "items": dict.items})

    class Body:
        def __init__(self, status, headers, data, slow=False):
            self.status, self.headers, self.data, self.slow = status, H(headers), data, slow

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=None):
            if self.slow:  # 다른 조각이 실패한 뒤에야 몸통이 온다
                failed.wait(5)
                time.sleep(0.1)
                self.slow = False
            out, self.data = (self.data[:n], self.data[n:]) if n else (self.data, b"")
            return out

    def open_url(method, url, headers, body, timeout, hide=()):
        start, end = (int(v) for v in dict(headers)["Range"].removeprefix("bytes=").split("-"))
        if start >= 10:
            failed.set()
            return Body(500, {}, b"down"), url
        return Body(206, {"Content-Range": f"bytes {start}-{end}/20", "ETag": '"e1"'}, b"y" * (end - start + 1), slow=end > 0), url
    monkeypatch.setattr(collect_http, "open_url", open_url)
    with pytest.raises(Exception):
        collect_job.run_file(config, {}, path, found, record, threading.Event())
    assert record["done"] == [[0, 10]]


def test_failed_piece_stops_waiting_pieces(tmp_path, monkeypatch, folder):
    """파일 받기: 한 조각이 실패하면 차례를 기다리던 조각은 요청을 보내지 않고, 실패 이유가 그대로 보인다."""
    import threading
    from collect import collect_config, collect_http, collect_job
    config = collect_config.normalize({"mode": "file", "file": {"url": "https://files.example.com/a.bin", "chunks": 8}, "balance": {"interval": 0.2, "retries": 0},
                                       "output": {"format": "csv", "path": str(tmp_path / "a.bin")}})
    path = collect_job.output_path(config)
    found = collect_job.parts(path)
    found["part"].parent.mkdir(parents=True, exist_ok=True)
    record = {"hash": collect_job.digest(config), "mode": "file", "done": [], "rows": 0, "bytes": 0}
    H = type("H", (dict,), {"get": dict.get, "items": dict.items})
    sent = []

    class Body:
        def __init__(self, status, headers, data):
            self.status, self.headers, self.data = status, H(headers), data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=None):
            out, self.data = (self.data[:n], self.data[n:]) if n else (self.data, b"")
            return out

    def open_url(method, url, headers, body, timeout, hide=()):
        start, end = (int(v) for v in dict(headers)["Range"].removeprefix("bytes=").split("-"))
        sent.append(start)
        if start == 0 and end > 0:
            return Body(404, {}, b"gone"), url
        return Body(206, {"Content-Range": f"bytes {start}-{end}/80", "ETag": '"e1"'}, b"y" * (end - start + 1)), url
    monkeypatch.setattr(collect_http, "open_url", open_url)
    takes, made = [], collect_job.balancer_for

    def counted(*a):  # 사용 한도에 세는 곳(take)도 실제로 보낸 요청만 센다
        balancer = made(*a)
        balancer.take = lambda: takes.append(1)
        return balancer
    monkeypatch.setattr(collect_job, "balancer_for", counted)
    with pytest.raises(Exception) as caught:
        collect_job.run_file(config, {}, path, found, record, threading.Event())
    assert sent.count(0) == 2 and sent[-1] == 0  # 실패한 조각(0-9) 뒤로는 요청이 없다(조각이 시작하는 차례는 그때그때 다르다)
    assert getattr(caught.value, "failed", True)  # 멈춤이 아니라 실패로 끝난다
    assert len(takes) == len(sent)  # 확인 요청을 포함해 보낸 요청 수만큼만 한도에 셌다


def test_piece_waiting_on_usage_limit_sends_nothing_after_failure(tmp_path, monkeypatch, folder):
    """사용 한도를 기다리던 조각은, 그사이 다른 조각이 실패하면 요청을 보내지 않는다."""
    import threading
    from collect import collect_config, collect_http, collect_job
    config = collect_config.normalize({"mode": "file", "file": {"url": "https://files.example.com/a.bin", "chunks": 2}, "balance": {"interval": 0, "retries": 0},
                                       "output": {"format": "csv", "path": str(tmp_path / "a.bin")}})
    path = collect_job.output_path(config)
    found = collect_job.parts(path)
    found["part"].parent.mkdir(parents=True, exist_ok=True)
    record = {"hash": collect_job.digest(config), "mode": "file", "done": [], "rows": 0, "bytes": 0}
    H = type("H", (dict,), {"get": dict.get, "items": dict.items})
    sent, failed, both = [], threading.Event(), threading.Barrier(2, timeout=5)

    class Body:
        def __init__(self, status, headers, data):
            self.status, self.headers, self.data = status, H(headers), data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=None):
            out, self.data = (self.data[:n], self.data[n:]) if n else (self.data, b"")
            return out

    def open_url(method, url, headers, body, timeout, hide=()):
        start, end = (int(v) for v in dict(headers)["Range"].removeprefix("bytes=").split("-"))
        sent.append(start)
        if start == 0 and end > 0:
            failed.set()
            return Body(404, {}, b"gone"), url
        return Body(206, {"Content-Range": f"bytes {start}-{end}/20", "ETag": '"e1"'}, b"y" * (end - start + 1)), url

    def take():  # 두 조각이 다 자리를 잡은 뒤, 뒤에 만든 조각(10-19)은 앞 조각이 실패할 때까지 한도를 기다린다
        me = threading.current_thread()
        if "worker" not in me.name:
            return
        both.wait()
        workers = sorted((t for t in threading.enumerate() if "worker" in t.name), key=lambda t: int(t.name.split("-")[1].split()[0]))
        if me is not workers[0]:
            failed.wait(5)
    made = collect_job.balancer_for

    def limited(*a):
        balancer = made(*a)
        balancer.take = take
        return balancer
    monkeypatch.setattr(collect_http, "open_url", open_url)
    monkeypatch.setattr(collect_job, "balancer_for", limited)
    with pytest.raises(Exception):
        collect_job.run_file(config, {}, path, found, record, threading.Event())
    assert sent == [0, 0]  # 확인 요청과 실패한 앞 조각뿐


def test_target_met_exactly_despite_float_tail():
    from core import targets
    from dashboard import summary_text
    assert targets.parse_value("1.1억") == 110_000_000 and targets.parse_value("1.15억") == 115_000_000
    assert summary_text.target_sentence(110_000_000, targets.parse_value("1.1억"), "currency").endswith("목표를 채웠어요.")
    assert summary_text.target_sentence(1.1 + 2.2, 3.3, "number").endswith("목표를 채웠어요.")  # 합계의 꼬리 3.3000000000000003
    assert summary_text.target_sentence(3.31, 3.3, "number").endswith("목표를 넘었어요.")


def test_tiny_targets_keep_their_value():
    from core import targets
    assert targets.parse_value("0.0000025") == 2.5e-06 and targets.parse_value("0.0000001") == 1e-07
    assert targets.parse_value("1.23456789%") == targets.parse_value("0.0123456789") == 0.0123456789


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node가 없다")
def test_cards_judge_target_like_the_server(tmp_path):
    from test_fix_audit_js import between, run_node
    helper = between("dash_cards.js", "const overTarget", "\n") + "\n"
    out = run_node(tmp_path, "console.log(JSON.stringify([overTarget(1.1 + 2.2, 3.3), underTarget(0.7 + 0.1, 0.8), overTarget(3.31, 3.3), underTarget(3.29, 3.3)]));", parts=[helper])
    assert out == [False, False, True, True]  # 꼬리는 같은 값, 진짜 차이는 넘음·모자람


# ---------- 22차 감사 ----------

def test_formula_concat_skips_empty_values_like_the_function_dictionary():
    from lazy import ibis
    from core import prep_derive
    t = ibis.memtable({"a": [None, "김"], "b": ["철수", "영희"]})
    assert t.select(r=prep_derive.Formula(t, "concat(a, b)").build()).to_polars()["r"].to_list() == ["철수", "김영희"]


def test_lift_with_negative_control_points_the_right_way():
    from metrics import metric_calc as mc
    assert mc.lift(-5, -10) == 0.5 and mc.lift(12, 10) == pytest.approx(0.2)  # 순이익 -10 → -5는 좋아진 것


def test_position_slots_reject_negative():
    from core import sql_funcs
    with pytest.raises(ValueError, match="0이나 음수는 쓸 수 없습니다"):
        sql_funcs.not_zero(-1, "N번째")
    assert sql_funcs.not_zero(2, "N번째") == 2


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node가 없다")
def test_color_rules_treat_float_tails_as_equal(tmp_path):
    from test_fix_audit_js import run_node
    body = "const m = (t, v) => parseMatch(t).test(v); console.log(JSON.stringify([m('= 0.3', 0.1 + 0.2), m('<= 0.3', 0.1 + 0.2), m('< 0.3', 0.1 + 0.2), m('0.3, 0.5', 0.1 + 0.2), m('!= 0.3', 0.1 + 0.2), m('> 0.3', 0.31)]));"
    out = run_node(tmp_path, body, files=["dash_rules.js"])
    assert out == [True, True, False, True, False, True]


# ---------- 23차 감사 ----------

@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node가 없다")
def test_counter_alert_and_cut_table_total(tmp_path):
    import json, subprocess
    from pathlib import Path
    from core import dash_store
    static = Path(__file__).resolve().parent.parent / "static"
    dash = json.dumps({"types": dash_store.CHART_TYPES, "time_types": dash_store.TIME_TYPES, "units": dash_store.UNITS, "max_palette": dash_store.MAX_PALETTE}, ensure_ascii=False)
    parts = ["board.js", "dash.js", "dash_cards.js", "dash_rules.js", "dash_grid.js", "dash_edit.js"]
    body = """
const assert = require("assert");
const kpi = (id, v) => ({ id, kind: "gauge", title: "환불률", view: { format: "number" }, data: { values: { scalar: [v] } }, source: {} });
const alerts = counterAlerts([kpi("r", 1.1 + 2.2)], { card: "", counters: ["r"], limits: { r: { op: "<=", value: 3.3 } } });
assert.deepStrictEqual(alerts, []);  // 합계의 꼬리는 한계와 같은 값
assert.strictEqual(counterAlerts([kpi("r", 3.4)], { card: "", counters: ["r"], limits: { r: { op: "<=", value: 3.3 } } }).length, 1);
console.log("ok");
"""
    script = tmp_path / "t.js"
    script.write_text(f"globalThis.SERVER = {{ dash: JSON.parse({json.dumps(dash)}) }};\n" + "".join(next(static.rglob(n)).read_text(encoding="utf-8") for n in parts) + body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-2000:]
    cards = (static / "dashboard" / "dash_cards.js").read_text(encoding="utf-8")
    assert '!card.data?.values?.more && !rows.some(' in cards  # 앞 500행만 받은 표는 합계를 내지 않는다


def test_map_with_nothing_to_draw_explains():
    import json
    import polars as pl
    from geo import mapcard
    regions = pl.DataFrame({"name": ["a"], "geometry": [json.dumps({"type": "Point", "coordinates": [127, 37]})]})
    with pytest.raises(ValueError, match="그릴 영역도 점도 없어요"):
        mapcard.build(regions, pl.DataFrame({"lat": [None], "lon": [None]}, schema={"lat": pl.Float64, "lon": pl.Float64}), {"name": "name"})


def test_onehot_values_differing_only_in_case_explain():
    from lazy import ibis
    from core import prep_ops
    t = ibis.memtable({"city": ["Seoul", "seoul", "Busan"]})
    with pytest.raises(ValueError, match="대소문자만 달라요"):
        prep_ops.onehot(t, "city", True)


def test_metric_delete_asks_when_used_and_onehot_removal_keeps_column():
    from pathlib import Path
    static = Path(__file__).resolve().parent.parent / "static"
    js = (static / "query" / "sentences.js").read_text(encoding="utf-8")
    remove = js[js.index("async function removeMetric(m)"):js.index("function scopeNow()")]
    assert remove.index("/api/my_metrics/used_by") < remove.index("sentAsk(") < remove.index("/api/my_metrics/delete")
    assert "if (OPS[key]?.unkeep) e.keep = true;" in (static / "prep" / "prep.html").read_text(encoding="utf-8")
