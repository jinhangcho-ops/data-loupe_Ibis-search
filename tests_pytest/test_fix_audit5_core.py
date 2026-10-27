"""5차 감사 묶음 5C(core·쿼리·지표) 시험. 값은 시험 안에서 지어냈다."""
import datetime as dt
import os

import polars as pl
import pytest
from flask import Response
from lazy import ibis

from core import prep_ops, server, settings, source, sql_exec, sql_funcs
from metrics import metric_catalog as cat
from query import flow, sentence_run as sr

D = dt.datetime


def both(**cols):
    """같은 표를 DuckDB와 Polars에 올린 {이름: 표}."""
    df = pl.DataFrame(cols)
    return {"duckdb": ibis.duckdb.connect().create_table("t", ibis.memtable(df.to_arrow())),
            "polars": ibis.polars.connect().create_table("t", df)}


# ---------- 5C1 옮기기 재시도 ----------

def test_migrate_retries_when_new_folder_only_has_locks_and_failure_mark(tmp_path):
    new = tmp_path / "DataLoupe"
    old = tmp_path / settings.OLD_NAME
    (old / "locks").mkdir(parents=True)
    (old / "settings.json").write_text("{}")
    (new / "locks").mkdir(parents=True)
    (new / "locks" / "app.lock").write_text("")
    os.utime(new / "locks" / "app.lock", (1, 1))  # 쓰다 멈춘 지 오래된 빈 잠금(방금 만든 빈 잠금은 살아 있는 것으로 본다)
    (new / "빈 폴더").mkdir()
    (new / settings.FAILED).write_text("x", encoding="utf-8")
    assert settings.migrate(new / "settings.json") is None
    assert not old.exists() and (new / "settings.json").exists()
    assert not (new / settings.FAILED).exists()


def test_migrate_leaves_a_new_folder_that_has_settings(tmp_path):
    new, old = tmp_path / "DataLoupe", tmp_path / settings.OLD_NAME
    old.mkdir()
    (old / "settings.json").write_text("{}")
    new.mkdir()
    (new / "settings.json").write_text('{"theme": "dark"}')
    assert settings.migrate(new / "settings.json") is None
    assert old.exists() and (new / "settings.json").read_text() == '{"theme": "dark"}'


def test_migrate_is_not_a_failure_when_another_process_moved_the_folder(tmp_path, monkeypatch):
    new, old = tmp_path / "DataLoupe", tmp_path / settings.OLD_NAME
    old.mkdir()

    def raced(a, b):
        os.rmdir(a)  # 다른 프로세스가 먼저 옮겼다
        raise OSError("gone")
    monkeypatch.setattr(os, "replace", raced)
    assert settings.migrate(new / "settings.json") is None
    assert not (new / settings.FAILED).exists()


def test_desktop_does_not_show_the_move_failure_window_for_scheduled_runs():
    text = open(os.path.join(os.path.dirname(settings.__file__), "..", "desktop.py"), encoding="utf-8").read()
    assert 'if moved_failed and sys.argv[1:2] not in (["--run"], ["--browser"]' in text


# ---------- 5C2·5C3·5C7 CSV 바뀜 ----------

def cp949(path, *rows):
    path.write_bytes(("이름,값\n" + "".join(f"{r}\n" for r in rows)).encode("cp949"))


@pytest.fixture
def opened():
    cons = []

    def make(*paths):
        con = source.open_flat_files.__wrapped__(list(paths))
        cons.append(con)
        server.state["tables"].clear()
        return con
    yield make
    for c in cons:
        source.close(c)
    server.state["tables"].clear()


def test_sql_path_refreshes_an_edited_csv(tmp_path, opened):
    p = tmp_path / "k.csv"
    cp949(p, "가,1", "나,2")
    con = opened(p)
    cp949(p, "가,1", "나,2", "다,3")
    assert int(sql_exec.to_expr(con, "select count(*) n from k", {}).execute().n[0]) == 3


def test_any_table_drops_the_old_schema_after_columns_change(tmp_path, opened):
    p = tmp_path / "k.csv"
    cp949(p, "가,1")
    con = opened(p)
    assert server.any_table(con, "k", None, vts={}).columns == ("이름", "값")
    p.write_bytes("이름,값,새컬럼\n가,1,x\n".encode("cp949"))
    sql_exec.to_expr(con, "select * from k", {})  # SQL 탭이 먼저 다시 등록해도
    t = server.any_table(con, "k", None, vts={})
    assert t.columns == ("이름", "값", "새컬럼")
    assert con.execute(t).values.tolist() == [["가", 1, "x"]]


def test_row_count_is_recounted_after_the_csv_changes(tmp_path, opened, monkeypatch):
    p = tmp_path / "k.csv"
    cp949(p, "가,1")
    con = opened(p)
    monkeypatch.setattr(server, "get_con", lambda: con)
    monkeypatch.setattr(server, "run", lambda f: f())
    server.state["rows"].clear()
    assert server.row_count(None, "k") == 1
    cp949(p, "가,1", "나,2", "다,3")
    assert server.row_count(None, "k") == 3  # 세어 둔 1행을 버리고 다시 센다
    server.state["rows"].clear()


def test_utf8_csv_resaved_as_cp949_is_converted(tmp_path, opened):
    p = tmp_path / "u.csv"
    p.write_text("이름,값\n가,1\n", encoding="utf-8")
    con = opened(p)
    p.write_bytes("이름,값\n가,1\n나,2\n".encode("cp949"))
    t = server.any_table(con, "u", None, vts={})
    assert con.execute(t).values.tolist() == [["가", 1], ["나", 2]]


def test_refresh_csv_returns_changed_names(tmp_path, opened):
    p = tmp_path / "k.csv"
    cp949(p, "가,1")
    con = opened(p)
    assert source.refresh_csv(con) == []
    cp949(p, "가,1", "나,2")
    assert source.refresh_csv(con) == ["k"] and source.refresh_csv(con) == []


# ---------- 5C5 글자 컬럼 vs 숫자 조건 ----------

@pytest.mark.parametrize("cond,want", [("code = 5", ["high", "low", "low"]), ("code in 5, 6", ["high", "high", "low"]), ("code > 5", ["low", "high", "high"])])
def test_e41_numeric_looking_value_on_text_column(cond, want):
    d = sr.compile("e41", {"조건1": cond, "값1": "high", "값3": "low", "새 이름": "level"}, {"base": "t"}, {"code": "text", "n": "num"})
    assert d["ok"], d
    from core import calc
    for name, t in both(code=["5", "6", "x"], n=[5.0, 6.0, 7.0]).items():
        assert list(calc.apply([d["body"]["calcs"][0]], t).execute()["level"]) == want, name


def test_head_name_in_condition_needs_an_operator():
    cols = {"total": "num", "total in": "num"}
    assert flow.head_name("total in 1, 2", cols, condition=True) is None
    assert flow.head_name("total in = 3", cols, condition=True) == "total in"
    assert flow.head_name("total in 1, 2", cols) == "total in"  # 정렬은 그대로


# ---------- 5C6 mde·power ----------

S = {"실험군": "grp", "대조군 값": "c", "실험군 값": "e", "지표 값": "m", "유의수준": 0.05, "검정력": 0.8}


def compute(eid, t, slots=S):
    return cat.compute(eid, {**slots}, {}, cat.Ctx(table=t), None)


def test_mde_with_zero_pooled_sd_is_none_with_a_note():
    for name, t in both(grp=["c", "c", "e", "e"], m=[1.0, 1.0, 1.0, 1.0]).items():
        r = compute("mde", t)
        assert r["value"] is None and any("표준편차" in n for n in r["notes"]), name


def test_power_with_a_single_row_group_is_none_with_a_note():
    for name, t in both(grp=["c", "e", "e", "e"], m=[1.0, 2.0, 3.0, 4.0]).items():
        r = compute("power", t, {k: v for k, v in S.items() if k != "검정력"})
        assert r["value"] is None and any("표준편차" in n for n in r["notes"]), name


# ---------- 5C7 작은 것 ----------

def test_half_up_rejects_out_of_range_digits():
    t = both(x=[1.5, 2.5])["duckdb"]
    for bad in (16, -16, 400):
        with pytest.raises(ValueError, match="15"):
            sql_funcs.half_up(t.x, bad)
    assert t.mutate(r=sql_funcs.half_up(t.x, 0)).execute()["r"].tolist() == [2.0, 3.0]
    ok = [op for op in prep_ops.OPS if op[0] == "round"][0][2]
    assert ok(15) and not ok(16) and not ok(-1)


def test_find_empty_text_matches_between_engines():
    got = {}
    for name, t in both(s=["가나", None, "", "b"]).items():
        got[name] = t.mutate(r=t.s.find("")).execute()["r"].tolist()
    assert str(got["duckdb"]) == str(got["polars"])


def test_squeeze_only_compresses_status_200():
    with server.app.test_request_context(headers={"Accept-Encoding": "gzip"}):
        assert server.squeeze(Response("y" * 40000, status=200, mimetype="text/plain")).headers.get("Content-Encoding") == "gzip"
        partial = Response("y" * 40000, status=206, mimetype="text/plain")
        partial.headers["Content-Range"] = "bytes 0-39999/80000"
        assert server.squeeze(partial).headers.get("Content-Encoding") is None


def test_money_supply_end_and_passes():
    t = ibis.memtable(pl.DataFrame({"u": [1, 1, 2], "ts": [D(2026, 3, 1), D(2026, 3, 5), D(2026, 3, 2)], "b": [10.0, 20.0, 5.0]}).to_arrow())
    r = cat.compute("money_supply", {"사용자": "u", "시각": "ts", "잔액": "b", "활성 기준(일)": 3}, {}, cat.Ctx(table=t), None)
    assert r["value"] == 20.0  # 마지막 날(3/5)에서 3일 안: 사용자 1만
    assert cat.PASSES["money_supply"] == 4 and cat.PASSES["velocity"] == 5


def test_roas_notes_the_common_key_rule_and_payback_keeps_the_last_day():
    users = ibis.memtable(pl.DataFrame({"uid": [1, 2], "ts": [D(2026, 3, 1), D(2026, 3, 1)], "ch": ["A", "A"], "amt": [10.0, 10.0]}).to_arrow())
    ads = {"ads": ibis.memtable(pl.DataFrame({"cost": [5.0], "ch": ["A"]}).to_arrow())}
    align = {"kind": "key", "keys": [["ch", "ch"]]}
    base = {"사용자": "uid", "시각": "ts", "금액": "amt", "광고 테이블": "ads", "광고비": "cost"}
    r = cat.compute("roas", {**base, "N일": 7}, {}, cat.Ctx(table=users, other=lambda n: ads[n]), align)
    assert any("광고비가 있는" in n for n in r["notes"])
    p = cat.compute("payback", {**base, "최대 일수": 1001}, {}, cat.Ctx(table=users, other=lambda n: ads[n]), align)
    assert p["rows"][-1]["day"] == 1001
