"""7차 /simplify G1(core) 시험: 단순화·효율 고침이 옛 동작과 같은 결과를 내는지. 데이터는 시험 안에서 지어냈고 엔진은 진짜를 쓴다."""
import datetime as dt
import decimal
import gzip
import json
import os
import pathlib
import time
import types

import polars as pl
import pytest
from lazy import ibis

from collect import collect_limits, collect_store
from core import credentials, dash_store, filelock, querying, scan_size, server, settings, use_counts


# ---------- S-R1: 대시보드 저장은 settings.write_atomic으로 ----------

def test_dash_store_file_bytes_and_no_leftovers(folder):
    dash_store.CACHE["stamp"] = None
    dash_store.save_dashboard({"name": "한글 이름", "cards": []})
    path = dash_store.path_of()
    text = path.read_text(encoding="utf-8")
    found = json.loads(text)
    assert set(found) == {"v", "blob"} and text == json.dumps(found)  # 옛 방식(ensure_ascii 기본)과 같은 글자(blob은 base64라 ASCII)
    assert credentials.reveal(found["blob"]) and "한글 이름" in credentials.reveal(found["blob"])
    assert not list(folder.glob("*.tmp")) and not list(folder.glob("*.new"))


def test_dash_store_encrypt_failure_leaves_file_and_backup_alone(folder, monkeypatch):
    dash_store.CACHE["stamp"] = None
    dash_store.save_dashboard({"name": "첫번째", "cards": []})
    dash_store.save_dashboard({"name": "두번째", "cards": []})
    path = dash_store.path_of()
    before, backup = path.read_bytes(), settings.backup_of(path).read_bytes()
    monkeypatch.setattr(credentials, "protect", lambda text: (_ for _ in ()).throw(ValueError("암호화 실패")))
    with pytest.raises(ValueError):
        dash_store.save_dashboard({"name": "세번째", "cards": []})
    assert path.read_bytes() == before and settings.backup_of(path).read_bytes() == backup
    assert not list(folder.glob("*.new")) and not list(folder.glob("*.tmp"))


# ---------- S-R4: 사용 기록 쓰기는 settings.write ----------

def test_collect_limits_write_keeps_backup_generation(folder):
    collect_limits.write({"a": {"sent": [], "day": [0, 0]}})
    collect_limits.write({"b": {"sent": [], "day": [1, 2]}})
    path = folder / collect_limits.USAGE
    assert json.loads(path.read_text(encoding="utf-8")) == {"b": {"sent": [], "day": [1, 2]}}
    assert json.loads(settings.backup_of(path).read_text(encoding="utf-8")) == {"a": {"sent": [], "day": [0, 0]}}
    assert not list(folder.glob("*.new"))


# ---------- S-A5: 오래된 잠금 지우기 ----------

def old_clear_stale(path):
    """고치기 전 collect_limits.clear_stale(복사본 알고리즘)."""
    moved = path.with_name(f"{path.name}.{os.getpid()}.old")
    try:
        os.replace(path, moved)
        if time.time() - moved.stat().st_mtime <= collect_limits.STALE_LOCK:
            try:
                os.link(moved, path)
            except OSError:
                pass
        moved.unlink(missing_ok=True)
    except OSError:
        pass


def state_of(folder):
    return sorted(p.name.replace(str(os.getpid()), "PID") for p in folder.iterdir())


@pytest.mark.parametrize("age", [0, 100])
def test_clear_stale_same_as_old_for_fresh_and_old_lock(folder, age):
    results = []
    for clear in (old_clear_stale, collect_limits.clear_stale):
        path = folder / "x.lock"
        path.write_text("")
        past = time.time() - age
        os.utime(path, (past, past))
        clear(path)
        results.append(state_of(folder))
        for p in folder.iterdir():
            p.unlink()
    assert results[0] == results[1] == (["x.lock"] if age == 0 else [])


def test_clear_stale_when_stat_fails_leaves_same_state_and_locked_still_enters(folder, monkeypatch):
    real = pathlib.Path.stat

    def broken(self, *a, **k):
        if self.suffix == ".old":
            raise OSError("stat 실패")
        return real(self, *a, **k)

    results = []
    for clear in (old_clear_stale, collect_limits.clear_stale):
        path = folder / "y.lock"
        path.write_text("")
        monkeypatch.setattr(pathlib.Path, "stat", broken)
        try:
            clear(path)
        except OSError:
            pass  # 새 방식은 오류를 밖으로 내고, 부르는 쪽(Locked.__enter__)이 OSError를 삼킨다
        monkeypatch.setattr(pathlib.Path, "stat", real)
        results.append(state_of(folder))
        for p in folder.iterdir():
            p.unlink()
    assert results[0] == results[1]
    lock = folder / collect_limits.LOCK
    lock.write_text("")
    past = time.time() - 100
    os.utime(lock, (past, past))
    monkeypatch.setattr(pathlib.Path, "stat", broken)  # .old만 실패: 잠금 파일 자체의 stat은 된다
    with collect_limits.Locked():
        assert lock.exists()
    monkeypatch.setattr(pathlib.Path, "stat", real)


def test_filelock_clear_stale_default_still_checks_owner(folder):
    path = folder / "z.lock"
    assert filelock.hold(path)
    filelock.clear_stale(path)  # 살아 있는(이 프로세스) 잠금은 되돌려 놓는다
    assert path.exists() and not list(folder.glob("*.old"))
    filelock.let_go(path)


# ---------- S-A7·S-A10·S-A2a: scan_size 도우미 ----------

def test_scan_size_helpers():
    assert scan_size.blocks_unknown({"scan_unknown": "block"}) and not scan_size.blocks_unknown({"scan_unknown": "run"}) and not scan_size.blocks_unknown({})
    assert scan_size.reason(ValueError("  첫 줄\n둘째 줄")) == "첫 줄"
    assert scan_size.reason(ValueError("")) == "ValueError"
    assert scan_size.reason(ValueError("x" * 500)) == "x" * 120
    polars_con = ibis.polars.connect()
    duck = ibis.duckdb.connect()
    assert scan_size.files_dialect(polars_con) == "duckdb" and scan_size.files_dialect(duck) is None and scan_size.files_dialect(object()) is None
    for con, dialect in ((polars_con, "duckdb"), (duck, None)):
        t = ibis.memtable({"a": [1, 2]})
        sql, got = scan_size.sql_of(con, t.filter(t.a > 1))
        assert got == dialect and sql == str(ibis.to_sql(t.filter(t.a > 1), dialect=dialect) if dialect else ibis.to_sql(t.filter(t.a > 1)))
        assert server.expr_sql(con, t) == scan_size.sql_of(con, t)


# ---------- S-S5: 연결 정리 ----------

def test_drop_connection_clears_everything_and_calls_hooks(monkeypatch):
    closed, hooks = [], []
    monkeypatch.setattr(source_module(), "close", lambda con: closed.append(con))
    monkeypatch.setattr(server, "ON_SWITCH", [lambda: hooks.append(1), lambda: hooks.append(2)])
    server.state.update(con="C", passwords=["p"], opened={"kind": "folder", "path": "x", "file": "f.db"}, rows={("a", "b"): 1}, names={"s": 1}, tables={"t": 1})
    assert server.opened_info()["file"] == "f.db"
    server.drop_connection()
    assert closed == ["C"] and hooks == [1, 2]
    assert server.state["con"] is None and server.state["passwords"] == [] and server.state["opened"] is None
    assert server.state["rows"] == {} and server.state["names"] == {} and server.state["tables"] == {}
    server.drop_connection()  # 연결이 없어도 안전하고 훅은 다시 불린다
    assert closed == ["C"] and hooks == [1, 2, 1, 2]


def source_module():
    from core import source
    return source


# ---------- S-E9: use_many ----------

@pytest.fixture
def fake_clock(monkeypatch):
    class Clock:
        n = 0

        @classmethod
        def now(cls):
            cls.n += 1
            return dt.datetime(2026, 1, 1, 0, 0, cls.n)
    monkeypatch.setattr(use_counts, "dt", types.SimpleNamespace(datetime=Clock))
    return Clock


def test_use_many_same_file_as_repeated_use(folder, fake_clock):
    keys = ["a", "b", "a", "c", "a"]
    file = folder / use_counts.FILE
    for key in keys:
        use_counts.use("metrics", key)
    one_by_one = file.read_text(encoding="utf-8")
    file.unlink()
    settings.backup_of(file).unlink(missing_ok=True)
    fake_clock.n = 0
    use_counts.use_many("metrics", keys)
    assert file.read_text(encoding="utf-8") == one_by_one
    assert json.loads(one_by_one)["metrics"]["a"]["count"] == 3
    use_counts.use_many("metrics", [])  # 빈 목록은 아무것도 쓰지 않는다
    assert file.read_text(encoding="utf-8") == one_by_one


def test_use_many_connections_trim_and_info(folder, fake_clock):
    keys = [f"c{i}" for i in range(use_counts.MAX_CONNECTIONS + 3)]
    file = folder / use_counts.FILE
    for key in keys:
        use_counts.use("connections", key, {"path": key})
    one_by_one = file.read_text(encoding="utf-8")
    file.unlink()
    fake_clock.n = 0
    use_counts.use_many("connections", keys, None)
    many = json.loads(file.read_text(encoding="utf-8"))["connections"]
    assert len(many) == use_counts.MAX_CONNECTIONS
    assert len(json.loads(one_by_one)["connections"]) == use_counts.MAX_CONNECTIONS


def test_metric_values_counts_each_name_once_with_single_write(folder, monkeypatch):
    calls = []
    monkeypatch.setattr(use_counts, "use_many", lambda kind, keys, info=None: calls.append((kind, list(keys))))
    t = ibis.memtable({"x": [1, 2, 3]})
    from core import metric_file
    monkeypatch.setattr(metric_file, "registry", lambda: {})
    monkeypatch.setattr(querying, "runnable", lambda *a, **k: t)
    monkeypatch.setattr(querying, "run", lambda work: (None, {}, False), raising=False)
    assert querying.metric_values({}, ["m1", "m2"]) == ({}, None, {}, False)
    assert calls == [("metrics", ["m1", "m2"])]


# ---------- S-E3a: json_reply ----------

def sample_frame():
    return pl.DataFrame({
        "big": [2**60, -(2**61), 5, None],
        "small": [1, 2, 3, 4],
        "f": [1.5, float("nan"), float("inf"), -float("inf")],
        "d": [dt.date(2024, 1, 2), None, dt.date(1999, 12, 31), dt.date(2000, 2, 29)],
        "ts": [dt.datetime(2024, 1, 2, 3, 4, 5, 678901), None, dt.datetime(2000, 1, 1), dt.datetime(1970, 1, 1)],
        "dec": pl.Series([decimal.Decimal("1.50"), None, decimal.Decimal("-0.01"), decimal.Decimal("123456789.12")], dtype=pl.Decimal(12, 2)),
        "st": [{"a": 1, "b": "x"}, None, {"a": 2, "b": "한글"}, {"a": None, "b": "\"q\""}],
        "ls": [[1, 2], [], None, [3]],
        "s": ["한글", "a\nb\t\"c\"\\", "</script>", None],
        "b": [True, False, None, True],
    })


def both(df, **kw):
    fields = {"more": True, "total": 12, "when": dt.datetime(2026, 1, 2, 3, 4, 5), "note": "한글", "none": None}
    with server.app.test_request_context():
        old = server.app.json.response(rows=server.to_json(df, **kw), **fields)
        new = server.json_reply(df, **kw, **fields)
    return old, new


@pytest.mark.parametrize("text_big", [True, False])
def test_json_reply_parses_like_to_json_with_jsonify(text_big):
    old, new = both(sample_frame(), text_big=text_big)
    assert new.mimetype == old.mimetype == "application/json" and new.status_code == 200
    assert json.loads(new.get_data(as_text=True)) == json.loads(old.get_data(as_text=True))
    if text_big:
        assert json.loads(new.get_data(as_text=True))["rows"]["rows"][0]["big"] == str(2**60)


def test_json_reply_empty_table_and_no_fields_and_many_rows():
    for df in (pl.DataFrame({"a": [], "b": []}), pl.DataFrame(schema={"a": pl.Int64, "b": pl.Utf8}), pl.DataFrame()):
        old, new = both(df)
        assert json.loads(new.get_data(as_text=True)) == json.loads(old.get_data(as_text=True))
    df = pl.DataFrame({"a": list(range(1000)), "b": [f"행{i}" for i in range(1000)]})
    with server.app.test_request_context():
        old = server.app.json.response(rows=server.to_json(df))
        new = server.json_reply(df)
    assert json.loads(new.get_data(as_text=True)) == json.loads(old.get_data(as_text=True))


def test_json_reply_goes_through_gzip_like_jsonify():
    df = pl.DataFrame({"a": list(range(20000)), "b": [f"행{i}" for i in range(20000)]})
    with server.app.test_request_context(headers={"Accept-Encoding": "gzip"}):
        new = server.squeeze(server.json_reply(df, more=False))
        old = server.squeeze(server.app.json.response(rows=server.to_json(df), more=False))
    assert new.headers["Content-Encoding"] == old.headers["Content-Encoding"] == "gzip"
    assert json.loads(gzip.decompress(new.get_data())) == json.loads(gzip.decompress(old.get_data()))


# ---------- S-A3a: split_parts ----------

def test_split_parts_orders_by_count_then_value_and_flags_more():
    from core import result
    values = [f"g{i:03d}" for i in range(result.MAX_SPLITS + 5) for _ in range(1)] + ["g001"] * 3 + [None] * 4
    t = ibis.memtable({"split": values, "x": list(range(len(values)))})
    parts, more = result.split_parts(t, "split")
    assert more is True and len(parts) == result.MAX_SPLITS and parts[0] == "g001" and None not in parts
    assert parts[1:] == sorted(parts[1:])
    few = ibis.memtable({"split": ["b", "a", "b", None]})
    assert result.split_parts(few, "split") == (["b", "a"], False)
    with pytest.raises(ValueError):
        result.split_parts(few, "없는컬럼")


# ---------- S-E8: Parquet 출력 행 수 ----------

def test_write_output_parquet_rows_match_count(tmp_path):
    con = ibis.duckdb.connect()
    table = ibis.memtable(pl.DataFrame({"a": list(range(2500)), "b": [f"행{i % 7}" for i in range(2500)]}))
    out = tmp_path / "out.parquet"
    rows = collect_store.write_output(con, table, "parquet", out, tmp_path)
    assert rows == con.execute(table.count()) == 2500
    deduped = table.select("b").distinct()
    out2 = tmp_path / "out2.parquet"
    assert collect_store.write_output(con, deduped, "parquet", out2, tmp_path) == con.execute(deduped.count()) == 7
    csv_out = tmp_path / "out.csv"
    assert collect_store.write_output(con, table, "csv", csv_out, tmp_path) == 2500


# ---------- S-R9·S-A9·S-R12·S-S1 ----------

def test_saved_text_reads_string_or_empty(folder):
    assert settings.saved_text("none.json") == ""
    settings.write("p.json", {"path": "C:/x.json", "env": 3, "empty": ""})
    assert settings.saved_text("p.json") == "C:/x.json"
    assert settings.saved_text("p.json", "env") == "" and settings.saved_text("p.json", "empty") == "" and settings.saved_text("p.json", "missing") == ""


def test_shared_definitions():
    from core import prep_derive, sql_exec, sql_funcs
    assert prep_derive.NAME is sql_funcs.NAME  # uploads는 지울 명령을 적재한 DB 방언(sqlglot)으로 만들어 sql_exec.quote를 쓰지 않는다(12A3)
    assert sql_exec.quote('a"b') == '"a""b"' and sql_exec.quote(5) == '"5"'
    assert server.BAD_NAME_CHARS.sub("_", 'a/b:c*"d') == "a_b_c__d" and "CON" in server.RESERVED and "LPT9" in server.RESERVED
