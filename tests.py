"""창을 띄우지 않고 서버 동작을 확인한다. 데이터는 실행할 때마다 임시 폴더에 지어내 만든다. 프로젝트 폴더에서 실행한다.

    python tests.py

확인마다 OK/FAIL 한 줄을 찍고, 하나라도 실패하면 종료 코드 1로 끝난다.
"""
import base64
import datetime as dt
import io
import json
import os
import random
import re
import math
import shutil
import subprocess
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
import urllib.parse
import zipfile
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # 콘솔 인코딩이 달라도 한글을 찍을 수 있게 한다.
# 실제 설정 파일과 임시 폴더를 건드리지 않게, settings·app을 불러오기 전에 임시 폴더 하나로 돌린다.
ROOT = Path(tempfile.mkdtemp(prefix="data_loupe_tests_"))
tempfile.tempdir = str(ROOT)  # 프로그램이 만드는 임시 폴더(내보내기)도 이 안에 생긴다.
os.environ["APPDATA"] = str(ROOT / "appdata")
sys.dont_write_bytecode = True  # 프로젝트 폴더에 __pycache__를 남기지 않는다.

import duckdb
from flask import Flask
import polars as pl
import psutil

from collect import collect_app  # noqa: F401  수집 라우트가 앱에 달린다.
from dashboard import dash_app  # noqa: F401  대시보드 라우트가 앱에 달린다.
from core import draft_app  # noqa: F401  화면 임시 저장(초안) 라우트가 앱에 달린다.
from query import flow_app  # noqa: F401  흐름·문장·내 지표 라우트가 앱에 달린다.
from metrics import catalog_app  # noqa: F401  지표 사전 라우트가 앱에 달린다.
from integrations import schedule_app  # noqa: F401  예약 라우트가 앱에 달린다.
from integrations import notify
from integrations.adapters import transport
from prep import prep_app
from core import credentials
from core import prep_ops
from prep import prep_profile
from core import profiler
from core import settings
from core import source
from query.search_app import app
from core.server import KEY
from core import server
from core.server import state

# 지어낸 데이터: 고객 40명과 주문 300건. 고객 번호 41~45는 고객 표에 없다(조인에서 빈 값이 된다).
random.seed(1)
STATUSES, NOTES = ["new", "paid", "shipped", "refunded"], [None, "=1+1", "red or blue", "a, b", "'vip'", "vip"]
customers = [{"id": i, "name": f"customer{i:02d}", "city": random.choice(["Seoul", "Busan", "Daegu", "Jeju"])} for i in range(1, 41)]
orders = []
for i in range(1, 301):
    day = dt.date(2024, 1, 1) + dt.timedelta(days=random.randrange(180))
    orders.append({"order_id": i, "customer_id": random.randint(1, 45), "status": random.choice(STATUSES),
                   "amount": random.randint(1, 500) * 100, "ordered_on": day,
                   "paid_at": dt.datetime.combine(day, dt.time(random.randrange(24), random.randrange(60))),
                   "note": random.choice(NOTES)})
# 전처리용 지어낸 데이터: 20행. 컬럼마다 지저분한 값 다섯 개(점수·날짜는 스무 개)를 되풀이한다.
MESSY = {
    "id": list(range(1, 21)),
    "name": ["  Alice  ", "bob", "CAROL  ", "  dave", "Eve  Smith"] * 4,
    "code": ["-", "", "N/A", "A1", None] * 4,
    "price": ["1,200원", "3000", "abc", " 45 ", None] * 4,
    "born": ["20260101", "20251231", "20260315", "20240229", None] * 4,
    "seen": ["2026-01-05", "2026-13-01", "bad", "2026-03-01", None] * 4,
    "grade": ["A", "B", None, "B", "C"] * 4,
    "note": ["=1+1", "x", "@cmd", "ok", None] * 4,
    "score": [10, 20, None, 40, 50, None, 30, 20, 10, None, 60, 20, None, 80, 90, 20, None, 10, 30, 20],
    "rate": [1.5, 2.5, None, 4.0] * 5,
    "day": [None if i % 5 == 4 else dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(20)],
    "at": [dt.time(9, 30), dt.time(18, 5), None, dt.time(12, 0)] * 5,
    "flag": [True, False] * 10,
    "amt": [5, 100, 1, 17, 9, 12, 3, 15, 7, 18, 2, None, 14, 6, 10, 16, 4, 13, 8, 11],
    "ratio": [3.14159, 2.71828, None, 1.41421] * 5,
    "neg": list(range(-9, 11)),
    "flat": [7] * 20,
    "city": ["Seoul", "Busan", "Seoul", "Daegu", "Seoul", "Busan", "Jeju", "Seoul", "Busan", None] * 2,
    "mixed": ["Seoul", "SEOUL", "seoul", "Busan", "busan"] * 4,
    "stamp": ["2026-03-05 14:07:09", "2026-12-31 23:59:59", "2026-03-05 25:00:00", "x", None] * 4,
    "ap": ["2026-03-05 02:07 PM", "2026-03-05 12:07 am", "2026-03-05 13:07 PM", "x", None] * 4,
    "mon": ["05 Mar 2026", "31 december 2026", "5 Mar 2026", "bad", None] * 4,
    "short": ["26/03/05", "26/12/31", "26/13/01", "x", None] * 4,
    "iso_dt": ["2026-03-05 14:07:09", "2026-12-31 23:59:59", "2025-01-01 00:00:00", "2024-02-29 12:00:00", None] * 4,
    "isod": ["2026-01-05", "2026-02-10", "2025-12-31", "2024-02-29", "2026-07-07"] * 4,
    "numstr": ["10", "20", "-", "40", "50"] * 4,
    "decs": ["1.5", "2", "3.25", "-", "?"] * 4,
    "money": ["1,200원", "3,000원", "4,500원", None, "7원"] * 4,
    "const": ["x"] * 20,
    "alln": pl.Series([None] * 20, dtype=pl.String),
    "d8": list(range(20)),
    "d16": [i * 100 for i in range(20)],
    "d32": [i * 100_000 for i in range(20)],
    "d64": [i * 10**9 for i in range(20)],
    "dnull": pl.Series([None] * 20, dtype=pl.Int64),
    "b8": [127] + [-128] * 19,
    "b16": [128] + [0] * 19,
    "txt": ["서울 강남  역", "  부산 해운대 ", "", None, "tel 010-1234, 02-555"] * 4,
    "sentence": ["배송 서울  서울 주문", "  a,b , a ,, c ", "", None, 'say "hi" "hi" a\\b a\\b'] * 4,
}
# 앞 1,000행과 전체가 다른 표(모양 검사를 앞부분으로 거르는 것을 확인한다) 3,000행.
WIDE = {
    "n": list(range(3000)),
    "num_all": [str(i) for i in range(3000)],  # 전체가 숫자 모양
    "num_late": ["x" if i == 2500 else str(i) for i in range(3000)],  # 앞은 숫자 모양, 뒤에 글자
    "bad_head": ["x" if i == 0 else str(i) for i in range(3000)],  # 앞에 글자
    "date_late": [("" if i % 2 else None) if i < 1500 else "20260101" for i in range(3000)],  # 앞 1,500행은 모두 빈 값, 뒤는 날짜 모양
    "kor": ["가나다" if i % 2 == 0 else "한글입니다" for i in range(3000)],  # 글자 수 3, 5(바이트 수가 아니다)
}
BAD = {"s": ["x" if i % 2 else "y" for i in range(1100)]}  # 모양 후보가 하나도 없는 표
# 숫자로 바꾸기의 경계 값 6행. DuckDB·Parquet·SQLite 세 연결에서 같은 결과가 나와야 한다.
TOP = 2**63 - 1
NUMS = {
    "id": list(range(1, 7)),
    "price": ["1,200원", "3000", "abc", " 45 ", None, "7원"],
    "numstr": ["10", "20", "-", "40", "50", "+60"],
    "decs": ["1.5", "2", "-", ".5", "1e3", "inf"],
    "edge": [str(TOP), str(TOP + 1), str(-TOP), "0012", "1.5", "0x10"],  # int64에 들어가는 가장 큰 값과 그 다음 값
    "money": ["1,200원", "3,000원", "4,500원", None, "7원", "12원"],
    "day": ["2026-01-05", "2026-02-10", "2025-12-31", "2024-02-29", "2026-07-07", "2026-03-01"],
    "rate": [1.0, 2.0, None, -3.0, 1e30, 4.0],
}
NUMBERS, NUMBERS_FLAT = ROOT / "numbers", ROOT / "numbers_flat"
DATA, PREP, PREP_FLAT = ROOT / "data", ROOT / "prep", ROOT / "prep_flat"
SAMPLING, SAMPLING_FLAT = ROOT / "sampling", ROOT / "sampling_flat"
fails, done = [], []


def make_data():
    (DATA / "flat").mkdir(parents=True)
    con = duckdb.connect(str(DATA / "shop.duckdb"))
    for name, rows in (("customers", customers), ("orders", orders)):
        frame = pl.DataFrame(rows)
        con.execute(f"create table {name} as select * from frame")
    con.close()
    pl.DataFrame(customers[:5]).write_parquet(DATA / "flat" / "buyers.parquet")
    pl.DataFrame(orders[:5]).write_csv(DATA / "flat" / "sales.csv")
    con = sqlite3.connect(DATA / "shop.db")  # SQLite에는 날짜 타입이 없어 날짜는 글자로 들어간다.
    for name, rows in (("customers", customers), ("orders", orders)):
        con.execute(f"create table {name} ({', '.join(k + (' integer' if isinstance(v, int) else ' text') for k, v in rows[0].items())})")
        con.executemany(f"insert into {name} values ({', '.join('?' * len(rows[0]))})", [[v if v is None or isinstance(v, int) else str(v) for v in row.values()] for row in rows])
    con.commit()
    con.close()
    PREP.mkdir()
    PREP_FLAT.mkdir()
    frame = pl.DataFrame(MESSY)
    con = duckdb.connect(str(PREP / "prep.duckdb"))
    con.execute('create table messy as select * replace (cast("at" as time) as "at") from frame')  # Polars의 시간은 나노초라 DuckDB의 time으로 바꾼다.
    con.close()
    frame.write_parquet(PREP_FLAT / "messy.parquet")
    SAMPLING.mkdir()
    SAMPLING_FLAT.mkdir()
    wide, bad = pl.DataFrame(WIDE), pl.DataFrame(BAD)
    con = duckdb.connect(str(SAMPLING / "sampling.duckdb"))
    con.execute("create table wide as select * from wide")
    con.execute("create table bad as select * from bad")
    con.close()
    wide.write_parquet(SAMPLING_FLAT / "wide.parquet")
    bad.write_parquet(SAMPLING_FLAT / "bad.parquet")
    NUMBERS.mkdir()
    NUMBERS_FLAT.mkdir()
    nums = pl.DataFrame(NUMS)
    con = duckdb.connect(str(NUMBERS / "numbers.duckdb"))
    con.execute("create table nums as select * from nums")
    con.close()
    nums.write_parquet(NUMBERS_FLAT / "nums.parquet")
    con = sqlite3.connect(NUMBERS / "numbers.db")
    con.execute(f"create table nums ({', '.join(k + {'id': ' integer', 'rate': ' real'}.get(k, ' text') for k in NUMS)})")
    con.executemany(f"insert into nums values ({', '.join('?' * len(NUMS))})", list(zip(*NUMS.values())))
    con.commit()
    con.close()


def check(name, got, want):
    ok = got == want
    print("OK  " if ok else "FAIL", name, "" if ok else f"-> {got!r} (기대 {want!r})")
    done.append(name)
    if not ok:
        fails.append(name)


def raised(work):
    try:
        work()
    except Exception as e:
        return type(e).__name__


def prep_text(tag, pc, error, only, preview, values):
    """글자에서 뽑아내는 ops(extract, has, words)."""
    spec = lambda **o: only("txt", txt={"ops": o})
    pick = lambda r, name: values(r, name)[:5]
    r = preview(spec(extract={"pattern": "[0-9]+"}))
    check(tag + " extract: 처음 맞은 부분, 안 맞거나 빈 값이면 빈 값", (r["rows"]["columns"], pick(r, "txt_match"), r["types"], r["sources"], r["derived"]),
          (["txt", "txt_match"], [None, None, None, None, "010"], ["string", "string"], ["txt", "txt"], [False, True]))
    check(tag + " extract: 묶음 번호(0은 전체, 2는 두 번째 묶음)", [pick(preview(spec(extract={"pattern": "([0-9]+)-([0-9]+)", **g})), "txt_match") for g in ({}, {"group": 0}, {"group": 2})],
          [[None, None, None, None, "010-1234"], [None, None, None, None, "010-1234"], [None, None, None, None, "1234"]])
    check(tag + " extract: 한글 정규식, 여러 줄 전체", values(preview(spec(extract={"pattern": "[가-힣]+"})), "txt_match"), ["서울", "부산", None, None, None] * 4)
    check(tag + " extract: 이어서 오는 |는 전체를 묶어 처음 맞은 것", pick(preview(spec(extract={"pattern": "tel|서울"})), "txt_match"), ["서울", None, None, None, "tel"])
    r = preview(spec(has={"words": ["서울", "부산", "tel"]}))
    check(tag + " has: 글자 그대로, 단어마다 int8 컬럼(빈 값 행은 0)", (r["rows"]["columns"], [pick(r, f"txt_has_{w}") for w in ("서울", "부산", "tel")], r["types"], r["derived"]),
          (["txt", "txt_has_서울", "txt_has_부산", "txt_has_tel"], [[1, 0, 0, 0, 0], [0, 1, 0, 0, 0], [0, 0, 0, 0, 1]], ["string", "int8", "int8", "int8"], [False, True, True, True]))
    r = preview(spec(has={"words": ["서울|부산", "[0-9]{3}-"], "regex": True}))
    check(tag + " has: 정규식", [pick(r, "txt_has_서울|부산"), pick(r, "txt_has_[0-9]{3}-")], [[1, 1, 0, 0, 0], [0, 0, 0, 0, 1]])
    check(tag + " has: .은 글자 그대로면 점이 든 값만, 정규식이면 빈 값 빼고 모두",
          [pick(preview(spec(has={"words": ["."], "regex": r})), "txt_has_.") for r in (False, True)], [[0, 0, 0, 0, 0], [1, 1, 0, 0, 1]])
    r = preview(spec(trim=True, extract={"pattern": "^[가-힣]+"}, has={"words": ["부산"]}, case="upper"))
    check(tag + " 파생 컬럼은 다른 ops를 적용한 뒤의 값으로(trim → extract, 앞 공백이 지워져야 ^에 맞음)", pick(r, "txt_match"), ["서울", "부산", None, None, None])
    r = preview(spec(extract={"pattern": "[0-9]+"}, has={"words": ["서울"]}), drop=["txt_match", "nothing"])
    check(tag + " drop으로 파생 컬럼을 뺄 수 있음", r["rows"]["columns"], ["txt", "txt_has_서울"])
    r = preview(only("id", "txt", txt={"keep": False, "ops": {"extract": {"pattern": "[0-9]+"}}}))
    check(tag + " 원래 컬럼을 빼도 파생 컬럼은 남음", r["rows"]["columns"], ["id", "txt_match"])

    ids = list(range(1, 21))
    sentence = lambda **o: only("id", "sentence", sentence={"ops": o})
    loads = lambda r, name="sentence_counts": [None if v is None else json.loads(v) for v in values(r, name)[:5]]
    used, original = [], prep_ops.counts_by_table
    prep_ops.counts_by_table = lambda *a: (used.append(1), original(*a))[1]  # 번호표 방식을 탔는지 센다.
    try:
        r = preview(sentence(counts={}))
    finally:
        prep_ops.counts_by_table = original
    check(tag + " counts: 행 안에서 세는 식을 못 쓰는 연결만 번호표 방식을 탐(탔으면 합치느라 행 순서가 바뀔 수 있음)", (bool(used), values(r, "id") == ids if not used else None), (tag == "Parquet", True if tag == "DuckDB" else None))
    r = preview(sentence(counts={}), sorts=["id"])
    check(tag + " counts: 공백(연속·앞뒤 공백)으로 나눠 조각별 개수, 키는 정렬 순서, 빈 값·빈 문자열 행은 빈 값",
          (r["rows"]["columns"], values(r, "sentence_counts")[:3], r["types"], r["sources"], r["derived"], r["before"], r["after"]),
          (["id", "sentence", "sentence_counts"], ['{"배송": 1, "서울": 2, "주문": 1}', '{",": 1, ",,": 1, "a": 1, "a,b": 1, "c": 1}', None], ["int64", "string", "string"],
           ["id", "sentence", "sentence"], [False, False, True], 20, 20))
    check(tag + " counts: 따옴표·역슬래시가 든 값도 json.loads로 읽히고 키 순서가 정렬 순서", ([loads(r)[4], list(loads(r)[4])], values(r, "sentence_counts")[3:5]),
          ([{'"hi"': 2, "a\\b": 2, "say": 1}, ['"hi"', "a\\b", "say"]], [None, '{"\\"hi\\"": 2, "a\\\\b": 2, "say": 1}']))
    check(tag + " counts: 같은 문장인 행은 같은 결과", (values(r, "sentence_counts")[0] == values(r, "sentence_counts")[5], values(r, "sentence_counts")[4] == values(r, "sentence_counts")[19]), (True, True))
    r = preview(sentence(counts={"sep": ","}), sorts=["id"])
    check(tag + " counts: 쉼표로 나누면 조각의 앞뒤 공백을 지우고 빈 조각은 세지 않음", loads(r)[:2], [{"배송 서울  서울 주문": 1}, {"a": 2, "b": 1, "c": 1}])
    r = preview(sentence(counts={"sep": ""}), sorts=["id"])
    check(tag + " counts: sep이 빈 글자면 공백", loads(r)[0], {"배송": 1, "서울": 2, "주문": 1})
    r = preview(sentence(case="upper", counts={}), sorts=["id"])
    check(tag + " counts: case를 적용한 뒤의 값으로 셈", loads(r)[4], {'"HI"': 2, "A\\B": 2, "SAY": 1})
    r = preview(only("name", "sentence", name={"ops": {"counts": {}}}, sentence={"ops": {"counts": {}}}), sorts=["name"])
    check(tag + " counts: 두 컬럼에 동시에, 각각 바로 뒤에", (r["rows"]["columns"], r["before"], r["after"], r["derived"]), (["name", "name_counts", "sentence", "sentence_counts"], 20, 20, [False, True, False, True]))
    check(tag + " counts: name은 공백을 지운 조각", sorted({v for v in values(r, "name_counts")}), sorted(['{"Alice": 1}', '{"CAROL": 1}', '{"Eve": 1, "Smith": 1}', '{"bob": 1}', '{"dave": 1}']))
    r = preview(only("id", "sentence", sentence={"keep": False, "ops": {"counts": {}}}))
    check(tag + " counts: 원래 컬럼을 빼도 만들어짐", r["rows"]["columns"], ["id", "sentence_counts"])
    r = preview(sentence(counts={}), drop=["sentence_counts"])
    check(tag + " counts: drop으로 뺌", r["rows"]["columns"], ["id", "sentence"])
    check(tag + " counts: 행 수 불변(after를 따로 세지 않음)", (preview(sentence(counts={}))["before"], preview(sentence(counts={}))["after"]), (20, 20))

    bad = lambda cols, **extra: error("/api/preview", base="messy", columns=cols, **extra)
    form = lambda op, text: f"'txt' 컬럼의 '{op}' 값은 {text} 형식으로 쓰세요."
    pattern_form = '{"pattern": "정규식", "group": 0}'
    has_form = '{"words": ["서울", "부산"], "regex": false} (words는 비어 있지 않은 서로 다른 글자 1~50개)'
    for name, found, want in [
        ("정수 컬럼에 extract", bad(only("id", id={"ops": {"extract": {"pattern": "1"}}})), "'extract'은 문자 컬럼에만 쓸 수 있습니다. 'id' 컬럼은 정수입니다."),
        ("정수 컬럼에 has", bad(only("id", id={"ops": {"has": {"words": ["1"]}}})), "'has'은 문자 컬럼에만 쓸 수 있습니다. 'id' 컬럼은 정수입니다."),
        ("정수 컬럼에 counts", bad(only("id", id={"ops": {"counts": {}}})), "'counts'은 문자 컬럼에만 쓸 수 있습니다. 'id' 컬럼은 정수입니다."),
        ("빈 pattern", bad(spec(extract={"pattern": ""})), form("extract", pattern_form)),
        ("음수 group", bad(spec(extract={"pattern": "a", "group": -1})), form("extract", pattern_form)),
        ("group이 정수가 아님", bad(spec(extract={"pattern": "a", "group": 1.5})), form("extract", pattern_form)),
        ("has words 51개", bad(spec(has={"words": [f"w{i}" for i in range(51)]})), form("has", has_form)),
        ("has words 없음", bad(spec(has={"words": []})), form("has", has_form)),
        ("has words 중복", bad(spec(has={"words": ["a", "a"]})), form("has", has_form)),
        ("has words 빈 항목", bad(spec(has={"words": ["a", ""]})), form("has", has_form)),
        ("has regex가 true/false가 아님", bad(spec(has={"words": ["a"], "regex": "yes"})), form("has", has_form)),
        ("counts sep이 글자가 아님", bad(spec(counts={"sep": 1})), form("counts", '{"sep": ","}')),
        ("extract 이름이 겹침", bad(spec(extract={"pattern": "a"}), copies=[{"from": "id", "name": "txt_match"}]), "'txt_match' 컬럼이 이미 있어 파생 컬럼을 만들 수 없습니다."),
        ("has 이름이 겹침", bad(spec(has={"words": ["서울"]}), copies=[{"from": "id", "name": "txt_has_서울"}]), "'txt_has_서울' 컬럼이 이미 있어 파생 컬럼을 만들 수 없습니다."),
        ("counts 이름이 겹침", bad(spec(counts={}), copies=[{"from": "id", "name": "txt_counts"}]), "'txt_counts' 컬럼이 이미 있어 파생 컬럼을 만들 수 없습니다."),
    ]:
        check(f"{tag} 거부: {name}", found, want)

    recipe = {"base": "messy", "columns": only("id", "sentence", sentence={"ops": {"counts": {}}}), "sorts": ["id"]}
    r = pc.post("/api/run", json={"recipe": recipe, "target": {"kind": "file", "format": "parquet"}})
    saved = pl.read_parquet(io.BytesIO(r.data))
    check(tag + " run(Parquet 파일): counts 컬럼과 값", (saved.height, saved.columns, json.loads(saved["sentence_counts"][0]), saved["sentence_counts"][2]),
          (20, ["id", "sentence", "sentence_counts"], {"배송": 1, "서울": 2, "주문": 1}, None))


def prep_new(tag, got, error, only, preview, values):
    """3단계 기능(행 거르기, 복사, 파생 컬럼, 새 전처리, 날짜시간 형식, 중복 제거, downcast, 추천). 두 연결에서 같은 값이 나와야 한다."""
    amt, ids = MESSY["amt"], list(range(1, 21))
    near = lambda items: [round(v, 6) if isinstance(v, float) else v for v in items]
    spec = lambda col, **o: only(col, **{col: {"ops": o}})
    ok = [v for v in amt if v is not None]
    mean, sd = statistics.mean(ok), statistics.stdev(ok)

    print("== 행 거르기·복사·파생 컬럼:", tag)
    r = preview(only("id"), filters=["id >= 3", {"any": ["id = 4", "id = 5"]}])
    check(tag + " filters: 조건끼리 and, any는 or", (values(r, "id"), r["before"], r["after"]), ([4, 5], 20, 2))
    r = preview(only("id"), filters=["score is null"])
    check(tag + " filters: 원본 컬럼 기준", (values(r, "id"), r["after"]), ([i for i, v in zip(ids, MESSY["score"]) if v is None], 5))
    r = preview({**only("name"), "name2": {"ops": {"trim": True}}}, copies=[{"from": "name", "name": "name2"}])
    check(tag + " copies: from 뒤에 놓고 원본 값으로 만든 뒤 복사 컬럼에 ops",
          (r["rows"]["columns"], values(r, "name")[:2], values(r, "name2")[:2], r["sources"], r["derived"]),
          (["name", "name2"], ["  Alice  ", "bob"], ["Alice", "bob"], ["name", "name2"], [False, False]))
    r = preview({**only("id"), "id_b": {"rename": "B"}}, copies=[{"from": "id", "name": "id_b"}, {"from": "id", "name": "id_c"}])
    check(tag + " copies: 같은 컬럼의 복사는 적은 순서, 복사 컬럼도 rename", (r["rows"]["columns"], r["sources"]), (["id", "B", "id_c"], ["id", "id_b", "id_c"]))
    r = preview(only("score", score={"ops": {"flag": True, "fill": {"how": "value", "value": 0}}}))
    check(tag + " flag: fill 앞의 빈 값 표시를 새 컬럼으로", (r["rows"]["columns"], values(r, "score"), values(r, "score_isnull"), r["types"], r["sources"], r["derived"]),
          (["score", "score_isnull"], [v or 0 for v in MESSY["score"]], [v is None for v in MESSY["score"]], ["int64", "boolean"], ["score", "score"], [False, True]))
    r = preview(only("id", score={"keep": False, "ops": {"flag": True}}))
    check(tag + " 원래 컬럼을 빼도 파생 컬럼은 남음", (r["rows"]["columns"], r["derived"]), (["id", "score_isnull"], [False, True]))
    r = preview(only("score", score={"rename": "pt", "ops": {"flag": True}}))
    check(tag + " 파생 컬럼 이름은 rename 전 이름 기준", r["rows"]["columns"], ["pt", "score_isnull"])
    r = preview(only("score", score={"ops": {"flag": True}}), drop=["score_isnull", "nothing", "score"])
    check(tag + " drop: 파생 컬럼만 빼고 없는 이름은 무시", r["rows"]["columns"], ["score"])
    check(tag + " 파생 컬럼 이름이 겹치면 거부", error("/api/preview", base="messy", columns=spec("score", flag=True), copies=[{"from": "id", "name": "score_isnull"}]),
          "'score_isnull' 컬럼이 이미 있어 파생 컬럼을 만들 수 없습니다.")
    day = MESSY["day"]
    r = preview(spec("day", parts=["year", "month", "day", "weekday", "ym"]))
    pick = lambda f: [None if d is None else f(d) for d in day]
    check(tag + " parts(날짜): 이름·값·위치(월요일=0, ym은 그 달의 첫날)",
          (r["rows"]["columns"], values(r, "day_year"), values(r, "day_month"), values(r, "day_day"), values(r, "day_weekday"), values(r, "day_ym"), r["types"][1:], r["derived"]),
          (["day", "day_year", "day_month", "day_day", "day_weekday", "day_ym"], pick(lambda d: d.year), pick(lambda d: d.month), pick(lambda d: d.day),
           pick(lambda d: d.weekday()), pick(lambda d: str(d.replace(day=1))), ["int64"] * 4 + ["date"], [False] + [True] * 5))
    r = preview(spec("iso_dt", type={"to": "date", "format": "%Y-%m-%d %H:%M:%S"}, parts=["hour", "year"]))
    check(tag + " parts(날짜시간): hour", (values(r, "iso_dt_hour"), values(r, "iso_dt_year")), ([14, 23, 0, 12, None] * 4, [2026, 2026, 2025, 2024, None] * 4))
    check(tag + " 거부: date에 hour", error("/api/preview", base="messy", columns=spec("day", parts=["hour"])), "'hour'는 날짜시간 컬럼에만 쓸 수 있습니다. 'day' 컬럼은 날짜입니다.")
    check(tag + " 거부: 문자에 parts", error("/api/preview", base="messy", columns=spec("name", parts=["year"])), "'parts'은 날짜 컬럼에만 쓸 수 있습니다. 'name' 컬럼은 문자입니다.")
    seoul = ["Seoul", "Busan", "Seoul", "Daegu", "Seoul", "Busan", "Jeju", "Seoul", "Busan", None] * 2
    code = {"Busan": 0, "Daegu": 1, "Jeju": 2, "Seoul": 3}
    r = preview(only("id", "city", city={"ops": {"label": True}}), sorts=["id"])
    check(tag + " label: 정렬한 순서의 번호, 빈 값은 빈 값", (r["rows"]["columns"], values(r, "city_label"), r["types"][2], r["after"]),
          (["id", "city", "city_label"], [None if c is None else code[c] for c in seoul], "int64", 20))
    r = preview(spec("day", label=True), sorts=["day"])
    check(tag + " label: 날짜", values(r, "day_label")[:4], [0, 1, 2, 3])
    r = preview(only("city", city={"ops": {"onehot": True}}))
    check(tag + " onehot: 값 순서, 빈 값인 행은 모두 0, int8",
          (r["rows"]["columns"], values(r, "city_Seoul"), values(r, "city_Busan")[-2:], r["types"][1:], r["derived"]),
          (["city", "city_Busan", "city_Daegu", "city_Jeju", "city_Seoul"], [int(c == "Seoul") for c in seoul], [1 if seoul[-2] == "Busan" else 0, 0], ["int8"] * 4, [False] + [True] * 4))
    check(tag + " 거부: onehot 상한", (setattr(prep_ops, "ONEHOT_MAX", 3), error("/api/preview", base="messy", columns=spec("city", onehot=True)))[1],
          "'city' 컬럼은 서로 다른 값이 3개를 넘어 onehot을 만들 수 없습니다.")
    prep_ops.ONEHOT_MAX = 50
    check(tag + " 거부: label 상한", (setattr(prep_ops, "LABEL_MAX", 3), error("/api/preview", base="messy", columns=spec("city", label=True)))[1],
          "'city' 컬럼은 서로 다른 값이 3개를 넘어 label을 만들 수 없습니다.")
    prep_ops.LABEL_MAX = 10_000
    check(tag + " 거부: label 이름이 겹침", error("/api/preview", base="messy", columns=spec("city", label=True), copies=[{"from": "id", "name": "city_label"}]),
          "'city_label' 컬럼이 이미 있어 파생 컬럼을 만들 수 없습니다.")

    print("== 새 전처리:", tag)
    cases = [  # (이름, 컬럼, ops, 기대 값)
        ("rare", "city", {"rare": {"min": 3}}, [c if c in ("Seoul", "Busan") or c is None else "기타" for c in seoul]),
        ("rare: 이름 지정", "city", {"rare": {"min": 7, "label": "etc"}}, ["Seoul" if c == "Seoul" else "etc" if c else None for c in seoul]),
        ("outlier range drop", "amt", {"outlier": {"how": "drop", "by": "range", "low": 2, "high": 17}}, [v for v in amt if v is None or 2 <= v <= 17]),
        ("outlier range clip(한쪽만)", "amt", {"outlier": {"how": "clip", "by": "range", "high": 17}}, [None if v is None else min(v, 17) for v in amt]),
        ("outlier iqr drop(Q1=5.5, Q3=14.5, k 1.5 → -8~28)", "amt", {"outlier": {"how": "drop", "by": "iqr"}}, [v for v in amt if v != 100]),
        ("outlier iqr clip(k 0.5 → 1~19)", "amt", {"outlier": {"how": "clip", "by": "iqr", "k": 0.5}}, [None if v is None else min(max(v, 1), 19) for v in amt]),
        ("outlier std drop", "amt", {"outlier": {"how": "drop", "by": "std"}}, [v for v in amt if v != 100]),
        ("outlier std clip(k 0.5)", "amt", {"outlier": {"how": "clip", "by": "std", "k": 0.5}},
         [None if v is None else min(max(v, mean - 0.5 * sd), mean + 0.5 * sd) for v in amt]),
        ("scale minmax", "amt", {"scale": "minmax"}, [None if v is None else (v - 1) / 99 for v in amt]),
        ("scale standard", "amt", {"scale": "standard"}, [None if v is None else (v - mean) / sd for v in amt]),
        ("scale log", "amt", {"scale": "log"}, [None if v is None else math.log1p(v) for v in amt]),
        ("round 2", "ratio", {"round": 2}, [3.14, 2.72, None, 1.41] * 5),
        ("round 0", "ratio", {"round": 0}, [3.0, 3.0, None, 1.0] * 5),
        ("bins", "amt", {"bins": [0, 10, 100]}, [None if v is None else "0–10" if v < 10 else "10–100" for v in amt]),
        ("bins: 밖의 값은 빈 값, 소수 경계", "amt", {"bins": [4.5, 12]}, [None if v is None or not 4.5 <= v <= 12 else "4.5–12" for v in amt]),
        ("type: 날짜시간(%H:%M:%S, 안 맞는 모양은 빈 값)", "stamp", {"type": {"to": "date", "format": "%Y-%m-%d %H:%M:%S"}},
         ["2026-03-05 14:07:09", "2026-12-31 23:59:59", None, None, None] * 4),
        ("type: 날짜시간(%I %p, 대소문자 무시)", "ap", {"type": {"to": "date", "format": "%Y-%m-%d %I:%M %p"}},
         ["2026-03-05 14:07:00", "2026-03-05 00:07:00", None, None, None] * 4),
        ("type: %b", "mon", {"type": {"to": "date", "format": "%d %b %Y"}}, ["2026-03-05", None, None, None, None] * 4),
        ("type: %B(대소문자 무시)", "mon", {"type": {"to": "date", "format": "%d %B %Y"}}, [None, "2026-12-31", None, None, None] * 4),
        ("type: %y", "short", {"type": {"to": "date", "format": "%y/%m/%d"}}, ["2026-03-05", "2026-12-31", None, None, None] * 4),
        ("type: 형식 없이 날짜시간 글자는 빈 값", "iso_dt", {"type": {"to": "date"}}, [None] * 20),
    ]
    for name, col, o, want in cases:
        check(f"{tag} {name}", near(values(preview(spec(col, **o)), col)), near(want))
    windows = [
        ("fill prev, 문자", "grade", {"fill": {"how": "prev", "by": "id"}}, ["A", "B", "B", "B", "C"] * 4),
        ("fill next", "score", {"fill": {"how": "next", "by": "id"}}, [10, 20, 40, 40, 50, 30, 30, 20, 10, 60, 60, 20, 80, 80, 90, 20, 10, 10, 30, 20]),
        ("fill prev", "score", {"fill": {"how": "prev", "by": "id"}}, [10, 20, 20, 40, 50, 50, 30, 20, 10, 10, 60, 20, 20, 80, 90, 20, 20, 10, 30, 20]),
    ]
    for name, col, o, want in windows:
        r = got("/api/preview", base="messy", columns=only("id", col, **{col: {"ops": o}}), sorts=["id"])  # 창 함수는 행 순서를 바꾸므로 정렬한다.
        if tag == "DuckDB":
            check(f"{tag} {name}", values(r, col), want)
        else:
            check(f"{tag} {name}: 창 함수가 없는 연결은 지원하지 않는 계산이라고 알림", r.get("error", "").startswith("이 연결에서는 지원하지 않는 계산입니다."), True)
    for name, col, o, count in [("outlier iqr drop", "amt", {"how": "drop", "by": "iqr"}, 19), ("outlier range drop", "amt", {"how": "drop", "by": "range", "low": 2, "high": 17}, 17)]:
        r = preview(spec(col, outlier=o))
        check(f"{tag} {name}: after", (r["before"], r["after"]), (20, count))
    types = lambda col, **o: preview(spec(col, **o))["types"]
    check(tag + " 날짜 형식에 시각이 있으면 timestamp, 없으면 date", (types("stamp", type={"to": "date", "format": "%Y-%m-%d %H:%M:%S"}), types("mon", type={"to": "date", "format": "%d %b %Y"})),
          (["timestamp"], ["date"]))
    bad = lambda col, **o: error("/api/preview", base="messy", columns=spec(col, **o))
    for name, message, found in [
        ("날짜 형식 글자", "날짜 형식에는 %Y %y %m %d %H %M %S %I %p %b %B만 쓸 수 있습니다. 예: %Y%m%d, %Y-%m-%d %H:%M:%S", bad("stamp", type={"to": "date", "format": "%Y %f"})),
        ("문자에 outlier", "'outlier'은 정수·실수 컬럼에만 쓸 수 있습니다. 'name' 컬럼은 문자입니다.", bad("name", outlier={"how": "drop", "by": "iqr"})),
        ("log 최솟값", "'neg' 컬럼의 최솟값이 -9로 -1 이하라 log로 바꿀 수 없습니다.", bad("neg", scale="log")),
        ("표준편차 0", "'flat' 컬럼은 표준편차가 0이거나 구할 수 없어 standard로 바꿀 수 없습니다.", bad("flat", scale="standard")),
        ("범위 0", "'flat' 컬럼은 값이 모두 같거나 비어 있어(범위가 0) minmax로 바꿀 수 없습니다.", bad("flat", scale="minmax")),
        ("round 음수", "'amt' 컬럼의 'round' 값은 0 이상 15 이하의 정수 형식으로 쓰세요.", bad("amt", round=-1)),
        ("bins 순서", "'amt' 컬럼의 'bins' 값은 오름차순 경계값 2개 이상. 예: [0, 10, 100] 형식으로 쓰세요.", bad("amt", bins=[10, 5])),
        ("rare min", "'city' 컬럼의 'rare' 값은 {\"min\": 2 이상의 정수, \"label\": \"기타\"} 형식으로 쓰세요.", bad("city", rare={"min": 1})),
        ("outlier range에 한계 없음", "'amt' 컬럼의 'outlier' 값은 ", bad("amt", outlier={"how": "drop", "by": "range"})),
        ("prev에 by 없음", "'score' 컬럼의 'fill' 값은 ", bad("score", fill={"how": "prev"})),
        ("prev의 by가 없는 컬럼", "'zzz' 컬럼이 없습니다.", bad("score", fill={"how": "prev", "by": "zzz"})),
    ]:
        check(f"{tag} 거부: {name}", found[:len(message)], message)

    print("== 중복·downcast:", tag)
    r = preview(only("name"), dedupe=["name"])
    check(tag + " dedupe: 일부 컬럼", (sorted(values(r, "name")), r["before"], r["after"]), (sorted(set(MESSY["name"])), 20, 5))
    r = preview(only("name", "code"), dedupe=["name", "code"])
    check(tag + " dedupe: 결과의 모든 컬럼이면 통째로 같은 행만", (sorted(zip(values(r, "name"), [c or "" for c in values(r, "code")])), r["after"]),
          (sorted(set(zip(MESSY["name"], [c or "" for c in MESSY["code"]]))), 5))
    check(tag + " dedupe: 값이 모두 다르면 그대로", preview(only("id"), dedupe=["id"])["after"], 20)
    check(tag + " dedupe: 파생·복사 컬럼도 기준이 됨", preview(only("city", city={"ops": {"onehot": True}}), dedupe=["city_Seoul"])["after"], 2)
    for name, body, want in [
        ("일부 컬럼", dict(columns=only("name", "code"), dedupe=["name"]), {"groups": 5, "rows": 15}),
        ("전체 컬럼", dict(columns=only("name", "code"), dedupe=["name", "code"]), {"groups": 5, "rows": 15}),
        ("중복 없음", dict(columns=only("id"), dedupe=["id"]), {"groups": 0, "rows": 0}),
        ("filters 뒤", dict(columns=only("name"), dedupe=["name"], filters=["id <= 10"]), {"groups": 5, "rows": 5}),
    ]:
        check(f"{tag} /api/dupes: {name}", got("/api/dupes", base="messy", **body), want)
    check(tag + " dedupe 전후 행 수 = dupes의 rows", (preview(only("name"), dedupe=["name"])["after"], 20 - got("/api/dupes", base="messy", columns=only("name"), dedupe=["name"])["rows"]), (5, 5))
    check(tag + " 거부: dedupe 기준 없음", error("/api/dupes", base="messy", dedupe=[]), "중복 기준 컬럼을 고르세요.")
    check(tag + " 거부: dedupe 모르는 컬럼", error("/api/preview", base="messy", dedupe=["zzz"])[:15], "'zzz' 컬럼이 없습니다.")
    check(tag + " 거부: drop이 목록이 아님", error("/api/preview", base="messy", drop="x"), "'drop'은 컬럼 이름 목록이어야 합니다.")
    cols = ["d8", "d16", "d32", "d64", "dnull", "b8", "b16", "rate", "ratio"]
    typed = lambda r: dict(zip(r["rows"]["columns"], r["types"]))
    r = preview(only(*cols))
    check(tag + " downcast 없이는 타입 그대로", typed(r), {**{c: "int64" for c in cols}, "rate": "float64", "ratio": "float64"})
    r = preview(only(*cols), downcast=True)
    check(tag + " downcast: 정수 범위별(경계 포함), 전부 빈 값은 그대로, 값이 안 바뀌는 실수만 float32", typed(r),
          {"d8": "int8", "d16": "int16", "d32": "int32", "d64": "int64", "dnull": "int64", "b8": "int8", "b16": "int16", "rate": "float32", "ratio": "float64"})
    check(tag + " downcast: 값은 그대로", [values(r, c) for c in ("d16", "d64", "b8", "b16", "rate", "ratio")],
          [MESSY["d16"], MESSY["d64"], MESSY["b8"], MESSY["b16"], MESSY["rate"], MESSY["ratio"]])
    r = preview(only("score", "rate"), downcast=True, dedupe=["score"])
    check(tag + " dedupe 뒤에 downcast", r["types"], ["int8", "float32"])

    print("== 추천:", tag)
    patterns = got("/api/patterns", base="messy")["columns"]
    order = lambda s: min((prep_ops.OPS_NAMES.index(k) for k in s.get("ops", {})), default=len(prep_ops.OPS_NAMES))  # 화면이 하는 것처럼 op 순서로 합친다.
    suggest = {c["name"]: sorted(c["suggest"] + patterns.get(c["name"], []), key=order) for c in got("/api/profile", base="messy")["columns"]}
    check(tag + " /api/patterns: 추천이 없는 컬럼은 키가 없음, 문자 컬럼만", sorted(patterns), ["born", "decs", "iso_dt", "isod", "money", "numstr"])
    what = lambda col: [s["ops"] if "ops" in s else {"keep": s["keep"]} for s in suggest[col]]
    want = {
        "id": [], "flag": [], "name": [{"trim": True}],
        "code": [{"nulls": ["", "-", "N/A"]}, {"fill": {"how": "mode"}}],  # A1 같은 코드는 숫자로 바꾸라고 하지 않는다.
        "price": [{"trim": True}, {"fill": {"how": "mode"}}],
        "born": [{"type": {"to": "date", "format": "%Y%m%d"}}, {"fill": {"how": "mode"}}],
        "seen": [{"fill": {"how": "mode"}}],
        "iso_dt": [{"type": {"to": "date", "format": "%Y-%m-%d %H:%M:%S"}}, {"fill": {"how": "mode"}}],
        "isod": [{"type": {"to": "date"}}],
        "numstr": [{"nulls": ["-"]}, {"type": {"to": "int"}}],
        "decs": [{"nulls": ["-", "?"]}, {"type": {"to": "float"}}],
        "money": [{"type": {"to": "int", "clean": True}}, {"fill": {"how": "mode"}}],
        "mixed": [{"case": "lower"}],
        "const": [{"keep": False}],
        "alln": [{"keep": False}],
        "score": [{"fill": {"how": "median"}}], "rate": [{"fill": {"how": "median"}}],
        "amt": [{"fill": {"how": "median"}}],  # 빈 값이 1개(5.0%)라 5% 미만이 아니다.
        "day": [{"fill": {"how": "mode"}}], "city": [{"fill": {"how": "mode"}}],
    }
    for col, ops_list in want.items():
        check(f"{tag} 추천: {col}", what(col), ops_list)
    check(tag + " 추천의 근거 문구에 숫자", (suggest["name"][0]["why"], suggest["amt"][0]["why"], suggest["code"][0]["why"]),
          ("앞뒤에 공백이 있는 값이 12개 있습니다.", "빈 값이 1개(5.0%)입니다.", '빈 값을 뜻하는 글자가 있습니다: "" 4개, "-" 4개, "N/A" 4개'))
    check(tag + " 추천: 빈 값이 5% 미만이면 fill drop", [s["ops"] for s in prep_profile.suggestions({"kind": "int", "nulls": 1, "unique": 9}, {}, 0, 100)], [{"fill": {"how": "drop"}}])
    broken = [(col, i) for col, found in suggest.items() for i, s in enumerate(found) if "ops" in s
              and "error" in got("/api/preview", base="messy", columns=only(col, **{col: {"ops": s["ops"]}}))]
    check(tag + " 추천의 ops를 그대로 recipe에 넣으면 동작", broken, [])


def prep_sampling(got, error):
    """통계·타입 추천을 테이블 전체가 아니라 표본(profiler)으로 구하고 테이블별로 저장해 두는 것. DuckDB 파일 연결과 Parquet 연결에 같은 검사를 돌린다.
    표본에 어느 행이 들어가는지는 방법(블록 샘플링 등)에 따라 달라, 그 결과에 기대지 않는 것만 확인한다."""
    duck = got("/api/files", path=str(SAMPLING))["files"][0]
    flat = got("/api/files", path=str(SAMPLING_FLAT))["flat"]
    calls, original = [], profiler.sample
    profiler.sample = lambda *a: (calls.append(a[4]), original(*a))[1]  # 표본을 읽은 횟수와 크기(size)를 센다.
    try:
        for tag, connect in [("DuckDB", dict(kind="folder", path=str(SAMPLING), file=duck)), ("Parquet", dict(kind="folder", path=str(SAMPLING_FLAT), files=flat))]:
            print("== 표본으로 통계:", tag)
            got("/api/connect", **connect)
            r = got("/api/shape", base="wide")
            check(tag + " /api/shape", (r["rows"], [(c["name"], c["type"], c["kind"]) for c in r["columns"]]),
                  (3000, [("n", "int64", "int"), ("num_all", "string", "string"), ("num_late", "string", "string"), ("bad_head", "string", "string"),
                          ("date_late", "string", "string"), ("kor", "string", "string")]))
            check(tag + " sample 범위·형식 거부", [error(api, base="wide", sample=v) for api in ("/api/profile", "/api/patterns") for v in (999, 1_000_001, "5000", 1500.0)],
                  ["표본 행 수(sample)는 1,000 이상 1,000,000 이하의 정수로 입력하세요."] * 8)
            calls.clear()
            whole = got("/api/profile", base="wide")
            check(tag + " 기본 표본(5,000행)보다 작은 테이블은 그대로 읽음", (whole["rows"], whole["sampled"]["rows"], whole["sampled"]["method"], calls), (3000, 3000, "whole", [5000]))
            again = got("/api/profile", base="wide")
            check(tag + " 다시 열면 저장해 둔 통계(표본을 다시 읽지 않음)", (again["sampled"]["saved"] == whole["sampled"]["saved"], calls), (True, [5000]))
            r = got("/api/patterns", base="wide")["columns"]
            check(tag + " patterns: 같은 표본에서(다시 읽지 않음). 전체를 읽은 표본이라 뒤의 글자(num_late)도 걸러짐", ({k: [s["ops"] for s in v] for k, v in r.items()}, calls),
                  ({"num_all": [{"type": {"to": "int"}}], "date_late": [{"type": {"to": "date", "format": "%Y%m%d"}}]}, [5000]))
            check(tag + " patterns: why의 개수는 표본 기준", [s["why"] for k in ("num_all", "date_late") for s in r[k]],
                  ["빈 값과 빈 값 표시를 뺀 3,000개가 모두 숫자로 바뀝니다.", "빈 값과 빈 값 표시를 뺀 1,500개가 모두 YYYYMMDD 모양입니다."])
            fresh = got("/api/profile", base="wide", refresh=True)
            check(tag + " [다시 구하기](refresh)는 표본을 새로 읽음", (calls, fresh["rows"]), ([5000, 5000], 3000))
            calls.clear()
            part = got("/api/profile", base="wide", sample=1000)
            check(tag + " 테이블보다 작은 표본: 표본 크기만큼만, 블록 샘플링이 모자라면 앞 행", (part["rows"] == part["sampled"]["rows"], 0 < part["rows"] <= 1000,
                  part["sampled"]["method"] in ("block", "head"), calls), (True, True, True, [1000]))
            kor = next(c for c in part["columns"] if c["name"] == "kor")
            check(tag + " 한글 값의 글자 수(바이트 수가 아님)", {k: kor[k] for k in ("min_len", "max_len")}, {"min_len": 3, "max_len": 5})
            check(tag + " 추론: 단일값 아님, 값이 되풀이되는 컬럼은 플래그(번호 컬럼은 아님)", (kor["infer"]["constant"], kor["infer"]["flags"], "flags" in next(c for c in part["columns"] if c["name"] == "n")["infer"]),
                  (False, ["가나다", "한글입니다"], False))
            check(tag + " patterns: 후보가 없으면 빈 것", got("/api/patterns", base="bad", sample=1000), {"columns": {}})
    finally:
        profiler.sample = original
    got("/api/disconnect")


def prep_where(value):
    """설정 prep_where(원격 DB의 전처리를 이 PC에서 계산하는 범위)를 바꾼다. 다른 설정은 그대로 둔다."""
    settings.write(settings.PATH.name, {**(settings.read(settings.PATH.name) or {}), "prep_where": value})


def numbers_section():
    """숫자 모양 통계, 타입 추천, 숫자로 바꾸기. try_cast가 없는 SQLite 주소 연결에서도 DuckDB·Parquet과 같은 값이 나와야 한다."""
    pc = prep_app.app.test_client()
    pc.set_cookie("key", KEY)
    got = lambda api, **body: pc.post(api, json=body).get_json()
    tables = lambda: sorted(pc.get("/api/tables").get_json()["tables"])
    recipe = lambda **ops: dict(base="nums", columns={c: ({"ops": ops[c]} if c in ops else {"keep": False}) for c in NUMS})

    def values(**ops):
        r = got("/api/preview", **recipe(**ops))
        return r.get("error") or (r["types"], *([row[c] for row in r["rows"]["rows"]] for c in ops))

    to = lambda kind, **more: {"type": {"to": kind, **more}}
    cases = [  # (이름, 컬럼, ops, 기대 타입, 기대 값)
        ("정리하고 정수로", "price", to("int", clean=True), "int64", [1200, 3000, None, 45, None, 7]),
        ("실수로(숫자 모양이 아니면 빈 값)", "price", to("float"), "float64", [None, 3000.0, None, 45.0, None, None]),
        ("정수로(+ 부호)", "numstr", to("int"), "int64", [10, 20, None, 40, 50, 60]),
        ("실수로(.5, 1e3은 숫자, inf는 아님)", "decs", to("float"), "float64", [1.5, 2.0, None, 0.5, 1000.0, None]),
        ("정수로(소수·지수 모양은 빈 값)", "decs", to("int"), "int64", [None, 2, None, None, None, None]),
        # 빈 값과 2^53보다 큰 정수가 한 컬럼에 있으면 SQLite 연결이 결과를 읽지 못해, 빈 값을 0으로 채워서 본다.
        ("정수로(int64 범위 밖은 빈 값)", "edge", {**to("int"), "fill": {"how": "value", "value": 0}}, "int64", [str(v) for v in (TOP, 0, -TOP, 12, 0, 0)]),  # ±2^53 넘는 정수가 든 컬럼은 서버가 값을 모두 글자로 보낸다
        ("실수로(큰 수)", "edge", to("float"), "float64", [float(TOP), float(TOP), float(-TOP), 12.0, 1.5, None]),
        ("실수 컬럼을 정수로(범위 밖은 빈 값)", "rate", to("int"), "int64", [1, 2, None, -3, None, 4]),
        ("정수 컬럼을 실수로", "id", to("float"), "float64", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
    ]
    connections = [
        ("DuckDB", dict(kind="folder", path=str(NUMBERS), file=str(NUMBERS / "numbers.duckdb"))),
        ("Parquet", dict(kind="folder", path=str(NUMBERS_FLAT), files=[str(NUMBERS_FLAT / "nums.parquet")])),
        ("SQLite", dict(kind="url", url=f"sqlite://{NUMBERS / 'numbers.db'}")),
    ]
    for tag, connect in connections:
        print("== 숫자로 바꾸기:", tag)
        check(tag + " 연결", "schemas" in got("/api/connect", **connect), True)
        profile = got("/api/profile", base="nums")
        check(tag + " profile: 행 수와 숫자 모양", (profile.get("rows"), {c["name"]: c["numeric"] for c in profile.get("columns", []) if c["kind"] == "string"}),
              (6, {"price": 2, "numstr": 5, "decs": 4, "edge": 5, "money": 0, "day": 0}))
        check(tag + " patterns: 타입 추천", {name: [s["ops"] for s in found] for name, found in got("/api/patterns", base="nums").get("columns", {}).items()},
              {"numstr": [to("int")], "money": [to("int", clean=True)], "day": [to("date")]})
        for name, column, ops, dtype, want in cases:
            check(f"{tag} type: {name}", values(**{column: ops}), ([dtype], want))
    # 여기서부터는 SQLite 연결이다.
    made = recipe(id={}, price=to("int", clean=True), numstr=to("int"), decs=to("float"), edge=to("int"))
    check("SQLite 새 테이블 만들기(숫자로 바꾼 컬럼)", (got("/api/run", recipe=made, target={"kind": "table", "name": "clean"}), tables()), ({"ok": True, "rows": 6}, ["clean", "nums"]))
    check("SQLite 만든 테이블의 타입", [(c["name"], c["type"]) for c in got("/api/shape", base="clean")["columns"]],
          [("id", "int64"), ("price", "int64"), ("numstr", "int64"), ("decs", "float64"), ("edge", "int64")])
    stats = {c["name"]: (c["nulls"], c["min"], c["max"]) for c in got("/api/profile", base="clean")["columns"]}
    check("SQLite 만든 테이블의 값(빈 값 수, 최소, 최대)", stats,
          {"id": (0, 1, 6), "price": (2, 7, 3000), "numstr": (1, 10, 60), "decs": (2, 0.5, 1000.0), "edge": (3, -TOP, TOP)})
    unsupported = [("날짜로 바꾸기", "day", to("date")), ("outlier iqr", "rate", {"outlier": {"how": "clip", "by": "iqr"}}), ("fill median", "rate", {"fill": {"how": "median"}})]
    for name, column, ops in unsupported:  # 기본(prep_where all): 원격 DB도 이 PC의 작업 사본(DuckDB)에서 계산하므로 된다
        check(f"SQLite가 못 하는 계산도 이 PC에서는 됨: {name}", str(values(**{column: ops})).startswith("이 연결에서는"), False)
    prep_where("new")  # 계산 컬럼·PCA가 없으면 DB에서 계산
    for name, column, ops in unsupported:
        check(f"DB에서 계산하면 SQLite가 못 하는 계산은 그 문구로: {name}", str(values(**{column: ops})).startswith("이 연결에서는 지원하지 않는 계산입니다."), True)
    prep_where("all")
    check("SQLite 연결 해제", got("/api/disconnect"), {"ok": True})


def prep_section():
    """전처리 서버(prep_app). 같은 검사를 DuckDB 파일 연결과 Parquet 파일 연결에 모두 돌려 결과가 같은지 본다."""
    pc = prep_app.app.test_client()
    pc.set_cookie("key", KEY)  # prep.html은 다른 작업이라 쿠키만 직접 넣는다.
    got = lambda api, **body: pc.post(api, json=body).get_json()
    error = lambda api, **body: got(api, **body).get("error", "")
    tables = lambda: sorted(pc.get("/api/tables").get_json()["tables"])
    columns = list(MESSY)
    only = lambda *names, **specs: {c: {**specs.get(c, {}), **({} if c in names else {"keep": False})} for c in columns}
    ops = lambda name, **o: only(name, **{name: {"ops": o}})  # 컬럼 하나에 ops를 적용하고 그 컬럼만 본다.
    preview = lambda cols, **extra: got("/api/preview", base="messy", columns=cols, **extra)
    values = lambda r, name: [row[name] for row in r["rows"]["rows"]]
    duck_file = got("/api/files", path=str(PREP))["files"][0]
    flat_files = got("/api/files", path=str(PREP_FLAT))["flat"]
    connections = [("DuckDB", dict(kind="folder", path=str(PREP), file=duck_file)), ("Parquet", dict(kind="folder", path=str(PREP_FLAT), files=flat_files))]

    def stat(name, dtype, kind, nulls, unique, low=None, high=None, **strings):
        return {"name": name, "type": dtype, "kind": kind, "nulls": nulls, "unique": unique, "min": low, "max": high, **strings}

    text = lambda name, nulls, unique, min_len, max_len, blank, spaces, numeric: stat(
        name, "string", "string", nulls, unique, min_len=min_len, max_len=max_len, blank=blank, spaces=spaces, numeric=numeric)
    price_int, price_float = [1200, 3000, None, 45, None], [None, 3000.0, None, 45.0, None]
    cases = [  # (이름, 컬럼, ops, 기대 값)
        ("trim", "name", {"trim": True}, ["Alice", "bob", "CAROL", "dave", "Eve  Smith"] * 4),
        ("squeeze", "name", {"squeeze": True}, [" Alice ", "bob", "CAROL ", " dave", "Eve Smith"] * 4),
        ("remove: 두 칸 공백을 통째로", "name", {"remove": "  "}, ["Alice", "bob", "CAROL", "dave", "EveSmith"] * 4),
        ("remove: 정규식이 아님", "name", {"remove": "."}, MESSY["name"]),
        ("remove: 원", "price", {"remove": "원"}, ["1,200", "3000", "abc", " 45 ", None] * 4),
        ("case upper", "name", {"case": "upper"}, ["  ALICE  ", "BOB", "CAROL  ", "  DAVE", "EVE  SMITH"] * 4),
        ("case lower", "name", {"case": "lower"}, ["  alice  ", "bob", "carol  ", "  dave", "eve  smith"] * 4),
        ("replace: 값이 같을 때만", "name", {"replace": [["bob", "BOB"], ["Eve", "X"]]}, ["  Alice  ", "BOB", "CAROL  ", "  dave", "Eve  Smith"] * 4),
        ("replace: 여러 쌍", "grade", {"replace": [["A", "Z"], ["B", "Y"]]}, ["Z", "Y", None, "Y", "C"] * 4),
        ("regex", "price", {"regex": ["[0-9]+", "#"]}, ["#,#원", "#", "abc", " # ", None] * 4),
        ("nulls", "code", {"nulls": ["-", "N/A", ""]}, [None, None, None, "A1", None] * 4),
        ("type: 정리하고 정수로", "price", {"type": {"to": "int", "clean": True}}, price_int * 4),
        ("type: 실수로(앞뒤 공백은 무시, 못 바꾸면 빈 값)", "price", {"type": {"to": "float"}}, price_float * 4),
        ("type: 날짜(형식 지정)", "born", {"type": {"to": "date", "format": "%Y%m%d"}}, ["2026-01-01", "2025-12-31", "2026-03-15", "2024-02-29", None] * 4),
        ("type: 날짜(형식 없음, 못 바꾸면 빈 값)", "seen", {"type": {"to": "date"}}, ["2026-01-05", None, None, "2026-03-01", None] * 4),
        ("type: 글자로", "score", {"type": {"to": "string"}}, [None if v is None else str(v) for v in MESSY["score"]]),
        ("fill value: 수", "score", {"fill": {"how": "value", "value": 0}}, [v or 0 for v in MESSY["score"]]),
        ("fill value: 글자", "grade", {"fill": {"how": "value", "value": "n/a"}}, ["A", "B", "n/a", "B", "C"] * 4),
        ("fill mean", "score", {"fill": {"how": "mean"}}, [34.0 if v is None else v for v in MESSY["score"]]),
        ("fill median", "score", {"fill": {"how": "median"}}, [20.0 if v is None else v for v in MESSY["score"]]),
        ("fill mode: 수", "score", {"fill": {"how": "mode"}}, [20 if v is None else v for v in MESSY["score"]]),
        ("fill mode: 글자", "grade", {"fill": {"how": "mode"}}, ["A", "B", "B", "B", "C"] * 4),
        ("글자를 실수로 바꾼 뒤 mean", "price", {"type": {"to": "float", "clean": True}, "fill": {"how": "mean"}}, [1200.0, 3000.0, 1415.0, 45.0, 1415.0] * 4),
        ("여러 op는 적어 둔 순서와 상관없이 고정 순서(trim → type → fill)", "price",
         {"fill": {"how": "value", "value": 0}, "type": {"to": "int", "clean": True}, "trim": True}, [1200, 3000, 0, 45, 0] * 4),
        ("nulls 다음에 case: 빈 값으로 볼 글자는 원래 보이는 대로(N/A) 적는다", "code", {"case": "lower", "nulls": ["-", "N/A"]}, [None, "", None, "a1", None] * 4),
    ]
    rejected = [  # (이름, 요청, 오류 문구가 시작하는 글)
        ("모르는 전처리 항목", dict(base="messy", columns={"name": {"ops": {"trimm": True}}}), "'trimm' 전처리 항목이 없습니다. 비슷한 이름: trim"),
        ("모르는 컬럼", dict(base="messy", columns={"zzz": {}}), "'zzz' 컬럼이 없습니다."),
        ("모르는 테이블", dict(base="nothing"), "'nothing' 테이블이 없습니다."),
        ("정수에 trim", dict(base="messy", columns={"id": {"ops": {"trim": True}}}), "'trim'은 문자 컬럼에만 쓸 수 있습니다. 'id' 컬럼은 정수입니다."),
        ("글자에 mean", dict(base="messy", columns={"grade": {"ops": {"fill": {"how": "mean"}}}}), "'mean'은 정수·실수 컬럼에만 쓸 수 있습니다. 'grade' 컬럼은 문자입니다."),
        ("replace 형식", dict(base="messy", columns={"name": {"ops": {"replace": ["a", "b"]}}}), "'name' 컬럼의 'replace' 값은 [[\"찾을 값\""),
        ("trim: false", dict(base="messy", columns={"name": {"ops": {"trim": False}}}), "'name' 컬럼의 'trim' 값은 true 형식으로 쓰세요."),
        ("type의 to", dict(base="messy", columns={"price": {"ops": {"type": {"to": "decimal"}}}}), "'price' 컬럼의 'type' 값은"),
        ("fill에 value가 없음", dict(base="messy", columns={"score": {"ops": {"fill": {"how": "value"}}}}), "'score' 컬럼의 'fill' 값은"),
        ("fill value가 컬럼 타입에 안 맞음", dict(base="messy", columns={"score": {"ops": {"fill": {"how": "value", "value": "abc"}}}}), "'abc'은 정수 값이 아닙니다."),
        ("keep이 true/false가 아님", dict(base="messy", columns={"name": {"keep": "no"}}), "'name' 컬럼의 keep은 true/false"),
        ("모르는 설정 키", dict(base="messy", columns={"name": {"drop": True}}), "'name' 컬럼의 설정은 keep, rename, ops만"),
        ("이름이 겹침", dict(base="messy", columns={"id": {"rename": "name"}}), "결과에 'name' 컬럼이 둘 이상입니다. 이름을 바꾸세요."),
        ("모든 컬럼을 뺌", dict(base="messy", columns={c: {"keep": False} for c in columns}), "남길 컬럼이 없습니다."),
        ("바꾸기 전 이름으로 정렬", dict(base="messy", columns=only("id", id={"rename": "n"}), sorts=["id"]), "'id' 컬럼이 없습니다."),
        ("잘못된 정렬 방향", dict(base="messy", sorts=["id up"]), "형식: 컬럼 [asc|desc]"),
    ]
    run_columns = only("id", "price", "note", price={"ops": {"trim": True, "type": {"to": "int", "clean": True}}})
    expected = {"id": list(range(20, 0, -1)), "price": [price_int[(i - 1) % 5] for i in range(20, 0, -1)],
                "note": [MESSY["note"][i - 1] for i in range(20, 0, -1)]}
    run_file = lambda kind: pc.post("/api/run", json={"recipe": {"base": "messy", "columns": run_columns, "sorts": ["id desc"]}, "target": {"kind": "file", "format": kind}})
    bad_target = "저장 방식은 file(parquet, csv) 또는 table이어야 합니다."

    for tag, connect in connections:
        print("== 전처리:", tag)
        check(tag + " 연결", "schemas" in got("/api/connect", **connect), True)
        profile = got("/api/profile", base="messy")
        check(tag + " profile: 오류 없음", profile.get("error"), None)
        check(tag + " profile: 행 수와 컬럼 순서", (profile["rows"], [c["name"] for c in profile["columns"]]), (20, columns))
        want = [
            stat("id", "int64", "int", 0, 20, 1, 20),
            text("name", 0, 5, 3, 10, 0, 12, 0),
            text("code", 4, 4, 0, 3, 4, 0, 0),
            text("price", 4, 4, 3, 6, 0, 4, 8),
            text("born", 4, 4, 8, 8, 0, 0, 16),
            text("seen", 4, 4, 3, 10, 0, 0, 0),
            text("grade", 4, 3, 1, 1, 0, 0, 0),
            text("note", 4, 4, 1, 4, 0, 0, 0),
            stat("score", "int64", "int", 5, 8, 10, 90),
            stat("rate", "float64", "float", 5, 3, 1.5, 4.0),
            stat("day", "date", "date", 4, 16, "2026-01-01", "2026-01-19"),
            stat("at", "time", "time", 5, 3, "09:30:00", "18:05:00"),
            stat("flag", "boolean", "bool", 0, 2),
        ]
        for w, g in zip(want, profile["columns"]):
            check(f"{tag} profile: {w['name']}", {k: v for k, v in g.items() if k not in ("suggest", "infer")}, w)
        for name, col, o, want in cases:
            check(f"{tag} {name}", values(preview(ops(col, **o)), col), want)
        r = preview(ops("score", fill={"how": "mean"}))
        check(tag + " preview: 컬럼 이름, 타입, 행 수", (r["rows"]["columns"], r["types"], r["before"], r["after"]), (["score"], ["float64"], 20, 20))
        r = preview(ops("score", fill={"how": "drop"}))
        check(tag + " fill drop: 그 컬럼이 빈 행을 뺌", (values(r, "score"), r["before"], r["after"]), ([v for v in MESSY["score"] if v is not None], 20, 15))
        r = preview(only("score", "rate", score={"ops": {"fill": {"how": "drop"}}}, rate={"ops": {"fill": {"how": "mean"}}}))
        kept = [i for i, v in enumerate(MESSY["score"]) if v is not None]  # score에서 행을 뺀 뒤의 rate 평균은 32/11이다(전체 평균은 8/3).
        check(tag + " 컬럼 차례대로: 앞 컬럼에서 뺀 행을 빼고 평균을 구함", [round(v, 6) for v in values(r, "rate")], [MESSY["rate"][i] or round(32 / 11, 6) for i in kept])
        r = preview(only("score", "name", "id", id={"rename": "n"}, name={"rename": "who", "ops": {"trim": True}}), sorts=["n desc", "who"])
        check(tag + " keep·rename·sorts: 원래 순서, 새 이름으로 정렬", (r["rows"]["columns"], values(r, "n")[:3], values(r, "who")[:2]),
              (["n", "who", "score"], [20, 19, 18], ["Eve  Smith", "dave"]))
        for name, body, message in rejected:
            check(f"{tag} 거부: {name}", error("/api/preview", **body)[:len(message)], message)
        check(tag + " profile: 없는 테이블", error("/api/profile", base="nothing"), "'nothing' 테이블이 없습니다.")

        prep_new(tag, got, error, only, preview, values)
        prep_text(tag, pc, error, only, preview, values)

        print("== 전처리 파일 저장:", tag)
        data = run_file("parquet").data
        saved = pl.read_parquet(io.BytesIO(data))
        check(tag + " Parquet: 값과 타입", (saved.columns, saved.to_dict(as_series=False), saved.schema["price"]), (["id", "price", "note"], expected, pl.Int64))
        data = run_file("csv").data
        saved = pl.read_csv(io.BytesIO(data))
        check(tag + " CSV: 다시 읽은 값", (data[:3], saved.columns, saved.to_dict(as_series=False)), (b"\xef\xbb\xbf", ["id", "price", "note"], expected))
        check(tag + " CSV: 수식 막기('를 붙이기)를 하지 않음", (b"=1+1" in data, b"'=1+1" in data, b"'@cmd" in data), (True, False, False))
        check(tag + " Excel은 거부", error("/api/run", recipe={"base": "messy"}, target={"kind": "file", "format": "xlsx"}), bad_target)
        check(tag + " 모르는 저장 방식은 거부", error("/api/run", recipe={"base": "messy"}, target={"kind": "folder"}), bad_target)

    print("== 전처리: 새 테이블 만들기")
    got("/api/connect", **connections[1][1])
    check("Parquet 연결에서 table은 거부", error("/api/run", recipe={"base": "messy"}, target={"kind": "table", "name": "out"}),
          "Parquet·CSV·JSON·Excel 파일 연결에는 테이블을 만들 DB가 없습니다. 파일로 저장하세요.")
    got("/api/connect", **connections[0][1])
    make = lambda name, cols=run_columns, **recipe: got("/api/run", recipe={"base": "messy", "columns": cols, **recipe}, target={"kind": "table", "name": name})
    check("DuckDB 파일에 새 테이블", make("clean_out", sorts=["id"]), {"ok": True, "rows": 20})
    check("테이블 목록에 생김", tables(), ["clean_out", "messy"])
    r = got("/api/preview", base="clean_out")
    check("만든 테이블의 값", (r["rows"]["columns"], values(r, "id"), values(r, "price"), r["types"]),
          (["id", "price", "note"], list(range(1, 21)), price_int * 4, ["int64", "int64", "string"]))
    check("fill drop은 만든 테이블의 행 수가 줄어듦", make("dropped", only("score", score={"ops": {"fill": {"how": "drop"}}})), {"ok": True, "rows": 15})
    check("같은 이름은 만들기 전에 거부(대소문자 무시)", [error("/api/run", recipe={"base": "messy"}, target={"kind": "table", "name": n}) for n in ("clean_out", "CLEAN_OUT")],
          ["'clean_out'은 이미 있는 테이블입니다.", "'CLEAN_OUT'은 이미 있는 테이블입니다."])
    check("테이블 이름 제한", [error("/api/run", recipe={"base": "messy"}, target={"kind": "table", "name": n})[:7] for n in ("", "a b", "a.b", "a'b", '"x"', "x;y")], ["테이블 이름은"] * 6)
    check("한글·숫자·밑줄 이름", make("결과_2", only("id")), {"ok": True, "rows": 20})
    check("잘못된 recipe는 만들지 않고 연결도 그대로", (error("/api/run", recipe={"base": "messy", "columns": {"zzz": {}}}, target={"kind": "table", "name": "bad"})[:15], tables()),
          ("'zzz' 컬럼이 없습니다.", ["clean_out", "dropped", "messy", "결과_2"]))
    check("만든 뒤에도 읽기 전용 연결", (state["opened"]["kind"], raised(lambda: state["con"].create_table("late", schema={"a": "int"})) is not None, got("/api/rowcount", base="clean_out")),
          ("folder", True, {"rows": 20}))
    hold = duckdb.connect(duck_file, read_only=True)  # 다른 프로그램이 읽기 전용으로 열고 있는 경우
    e = error("/api/run", recipe={"base": "messy"}, target={"kind": "table", "name": "blocked"})
    hold.close()
    check("쓰기로 못 열면 이유를 알려 주고 읽기 전용 연결을 되살림", (e.startswith("쓰기로 열 수 없습니다."), got("/api/rowcount", base="dropped")), (True, {"rows": 15}))
    check("그 뒤에는 다시 만들 수 있음", make("blocked", only("id")), {"ok": True, "rows": 20})
    check("label이 든 recipe로 테이블 만들기", make("labeled", only("id", "city", city={"ops": {"label": True}}), sorts=["id"]), {"ok": True, "rows": 20})
    r = got("/api/preview", base="labeled", sorts=["id"])
    check("만든 테이블의 label 값", values(r, "city_label"), [None if c is None else {"Busan": 0, "Daegu": 1, "Jeju": 2, "Seoul": 3}[c] for c in MESSY["city"]])
    check("counts가 든 recipe로 테이블 만들기", make("counts_out", only("id", "sentence", sentence={"ops": {"counts": {}}})), {"ok": True, "rows": 20})
    r = got("/api/preview", base="counts_out", sorts=["id"])
    check("만든 테이블의 counts 값", [json.loads(v) for v in values(r, "sentence_counts") if v], [{"배송": 1, "서울": 2, "주문": 1}, {",": 1, ",,": 1, "a": 1, "a,b": 1, "c": 1}, {'"hi"': 2, "a\\b": 2, "say": 1}] * 4)
    got("/api/connect", kind="url", url=f"duckdb://{duck_file}")
    check("주소 연결은 그 연결로 바로 만듦(스키마 지정)", make("from_url", only("id", "name", name={"ops": {"trim": True}}), schema="main"), {"ok": True, "rows": 20})
    r = got("/api/preview", schema="main", base="from_url", sorts=["id desc"])
    check("주소 연결에서 만든 테이블의 값", (values(r, "name")[:2], r["before"]), (["Eve  Smith", "dave"], 20))
    got("/api/disconnect")
    prep_sampling(got, error)


def notify_section(c, got):
    """다른 앱으로 보내기. 실제로 요청을 보내지 않고 transport.post를 바꿔 끼워 무엇이 나가는지만 본다."""
    print("== 다른 앱으로 보내기")
    error = lambda api, **body: got(api, **body).get("error", "")
    put = lambda name, **destination: got("/api/destinations", name=name, destination=destination)
    sent, reply = [], [b'{"ok": true}']

    def fake(url, data, headers):
        sent.append((url, data, headers))
        answer = reply[0](url) if callable(reply[0]) else reply[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    real, token, hook = transport.post, "xoxb-secret-token-123", "https://hooks.example.com/services/secret-path-456"
    transport.post = fake
    try:
        r = c.get("/api/destinations").get_json()
        check("보낼 곳 종류 표: 라벨·그림·칸(비밀 여부)", ({k: (v["label"], v["image"], [(f["name"], f["secret"]) for f in v["fields"]]) for k, v in r["kinds"].items()}, r["destinations"]),
              ({"slack": ("Slack", True, [("token", True), ("channel", False)]), "notion": ("Notion(페이지에 덧붙이기)", False, [("token", True), ("page", False)]), "webhook": ("웹훅(주소로 보내기)", True, [("url", True), ("content", False), ("template", False)])}, {}))
        webhook = r["kinds"]["webhook"]
        check("webhook의 선택지·기본값·여러 줄 칸·기본 틀", ([f.get("options") for f in webhook["fields"]][1], [f.get("default") for f in webhook["fields"]][1], webhook["fields"][2].get("multiline"),
              [p["label"] for p in webhook["presets"]]), ([["json", "JSON"], ["text", "글자"], ["form", "폼(URL 인코딩)"]], "json", True, ["일반 JSON", "Teams", "Discord", "글자"]))
        check("종류 표에 검사 함수 같은 내부 값이 새지 않음", all(set(f) <= {"name", "label", "secret", "options", "default", "multiline", "hint"} for k in r["kinds"].values() for f in k["fields"]), True)

        print("== 보낼 곳 저장(비밀 값은 암호화)")
        r = put("slack", kind="slack", done=True, token=token, channel="C0123")
        check("저장: 목록에 비밀 값이 없고 저장돼 있다는 true만", r["destinations"], {"slack": {"kind": "slack", "done": True, "keep": "saved", "token": True, "channel": "C0123"}})
        raw = (settings.PATH.parent / "destinations.json").read_bytes()
        check("destinations.json에 토큰이 평문으로 없음", (token.encode() in raw, b"xoxb" in raw, b"C0123" in raw), (False, False, True))
        stored = settings.named("destinations.json")["slack"]["token"]
        check("암호화한 것을 풀면 같은 값, 글자(한글 포함)도 같음", (credentials.reveal(stored), credentials.reveal(credentials.protect("가나다 abc")), "가나다" in credentials.protect("가나다 abc")), (token, "가나다 abc", False))
        r = put("slack", kind="slack", done=False, token="", channel="C999")
        check("고칠 때 비밀 칸을 비우면 저장된 값을 그대로 둠", (r["destinations"]["slack"], credentials.reveal(settings.named("destinations.json")["slack"]["token"])),
              ({"kind": "slack", "done": False, "keep": "saved", "token": True, "channel": "C999"}, token))
        check("새로 만드는데 비밀 칸이 없으면 거부", error("/api/destinations", name="새것", destination={"kind": "slack", "done": False, "channel": "C1"}), "'봇 토큰' 칸을 입력하세요.")
        data = settings.named("destinations.json")
        data["slack"]["token"] = "AAAA"  # 다른 PC·계정에서 복사해 온 것처럼 풀 수 없는 값
        settings.write("destinations.json", data)
        check("풀 수 없는 비밀 값은 false(다시 입력 필요)", c.get("/api/destinations").get_json()["destinations"]["slack"]["token"], False)
        check("풀 수 없는데 비밀 칸을 비우면 거부, 보내기도 거부", (error("/api/destinations", name="slack", destination={"kind": "slack", "done": False, "token": "", "channel": "C999"}),
              error("/api/send", name="slack", title="t")), ("'봇 토큰' 칸을 입력하세요.", "'slack': '봇 토큰'을 읽을 수 없습니다(다른 PC·계정에서 온 파일). 다시 입력해 저장하세요."))
        put("slack", kind="slack", done=False, token=token, channel="C999")
        check("다시 입력하면 true", c.get("/api/destinations").get_json()["destinations"]["slack"]["token"], True)
        check("검증 거부: 종류·칸·done·https·본문 종류", [error("/api/destinations", name="x", destination=d) for d in (
              {"kind": "mail"}, {"kind": "slack", "token": "a", "channel": "C", "extra": 1}, {"kind": "slack", "token": "a", "channel": "C", "done": "yes"},
              {"kind": "webhook", "url": "http://hooks.example.com/a", "content": "json", "template": "{}"},
              {"kind": "webhook", "url": "https://h.example.com/a", "content": "xml", "template": "{}"},
              {"kind": "webhook", "url": "https://h.example.com/a", "content": "json", "template": " "})],
              ["보낼 곳의 종류는 slack, notion, webhook 중 하나입니다.", "보낼 곳에 쓸 수 없는 값이 있습니다: extra", "보낼 곳에 쓸 수 없는 값이 있습니다: done", "주소는 https://로 시작해야 합니다.",
               "'본문 종류'은 json, text, form 중 하나입니다.", "'본문 틀' 칸을 입력하세요."])
        check("이름 제한(보낼 곳 이름은 1~50자, 30개까지)", [error("/api/destinations", name=n, destination=None) for n in ("", "x" * 51)], ["보낼 곳 이름은 1자 이상 50자 이하로 입력하세요."] * 2)
        for i in range(29):
            put(f"n{i}", kind="slack", done=False, token="t", channel="C")
        check("30개까지, 있는 이름은 고칠 수 있음", (error("/api/destinations", name="넘침", destination={"kind": "slack", "done": False, "token": "t", "channel": "C"}),
              len(put("n0", kind="slack", done=True, token="", channel="C2")["destinations"])), ("저장한 보낼 곳은 30개까지입니다. 쓰지 않는 것을 지우세요.", 30))
        for i in range(29):
            got("/api/destinations", name=f"n{i}", destination=None)

        print("== Slack으로 보내기")
        table = {"columns": ["a", "b"], "rows": [[1, "x"], [2, None]]}
        sent.clear()
        check("글만: 주소·인증 헤더·본문", (got("/api/send", name="slack", title="완료", text="끝났습니다", table=table), len(sent), sent[0][0], sent[0][2],
              json.loads(sent[0][1])), ({"ok": True}, 1, "https://slack.com/api/chat.postMessage",
              {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}, {"channel": "C999", "text": "*완료*\n끝났습니다\n```\na | b\n1 | x\n2 | \n```"}))
        png = b"\x89PNG\r\n\x1a\n" + b"image-bytes"
        url = "data:image/png;base64," + base64.b64encode(png).decode()

        def slack_reply(address):
            return {"https://slack.com/api/files.getUploadURLExternal": b'{"ok": true, "upload_url": "https://files.example.com/up/1", "file_id": "F1"}',
                    "https://files.example.com/up/1": b"OK - 11", "https://slack.com/api/files.completeUploadExternal": b'{"ok": true}'}[address]

        sent.clear()
        reply[0] = slack_reply
        got("/api/send", name="slack", title="차트", text="본문", image=url)
        check("그림이 있으면 세 단계: 올릴 주소 받기 → 바이트 올리기 → 완료(채널·설명)", ([s[0] for s in sent], sent[0][1], sent[1][1], sent[1][2], json.loads(sent[2][1])),
              (["https://slack.com/api/files.getUploadURLExternal", "https://files.example.com/up/1", "https://slack.com/api/files.completeUploadExternal"],
               f"filename=image.png&length={len(png)}".encode(), png, {"Content-Type": "application/octet-stream"},
               {"files": [{"id": "F1", "title": "차트"}], "channel_id": "C999", "initial_comment": "*차트*\n본문"}))
        check("올리기 요청에는 인증 헤더가 없고 Slack 요청에만 있음", ["Authorization" in s[2] for s in sent], [True, False, True])
        reply[0] = b'{"ok": false, "error": "channel_not_found"}'
        e = error("/api/send", name="slack", title="t")
        check("ok가 false면 error 값을 넣은 오류", e, "Slack 오류: channel_not_found")
        reply[0] = ValueError(f"보내지 못했습니다(상태 401): invalid {token}")
        e = error("/api/send", name="slack", title="t")
        check("실패 문구에서 토큰을 가림", (token in e, "***" in e, e.startswith("보내지 못했습니다(상태 401)")), (False, True, True))

        print("== 웹훅으로 보내기")
        weird = 'He said "hi"\nüber 한글 & a=b\\c'
        body = lambda name="hook", **more: got("/api/send", name=name, title=weird, text="내용 & x=y", rows=1234, seconds=83, **more)
        reply[0] = b"ok"
        for content, template, check_body, content_type in [
            ("json", '{"t": "{제목}", "n": "{행수}", "s": "{걸린시간}", "z": "{모르는}", "i": "{그림}"}', lambda b: json.loads(b), "application/json"),
            ("text", "{제목}|{걸린시간}|{모르는}", lambda b: b.decode("utf-8"), "text/plain; charset=utf-8"),
            ("form", "a={제목}&b={내용}", lambda b: urllib.parse.parse_qs(b.decode(), keep_blank_values=True), "application/x-www-form-urlencoded"),
        ]:
            put("hook", kind="webhook", done=False, url=hook, content=content, template=template)
            sent.clear()
            body()
            parsed = check_body(sent[0][1])
            want = {"json": {"t": weird, "n": "1,234", "s": "1분 23초", "z": "{모르는}", "i": ""}, "text": f"{weird}|1분 23초|{{모르는}}",
                    "form": {"a": [weird], "b": ["내용 & x=y"]}}[content]
            check(f"webhook {content}: 주소·Content-Type·이스케이프(따옴표·줄바꿈·한글·&·=·역슬래시)·모르는 자리는 그대로", (sent[0][0], sent[0][2], parsed), (hook, {"Content-Type": content_type}, want))
        check("저장 목록에 주소가 없음", ("secret-path" in c.get("/api/destinations").get_data(as_text=True), c.get("/api/destinations").get_json()["destinations"]["hook"]["url"]), (False, True))
        raw = (settings.PATH.parent / "destinations.json").read_bytes()
        check("destinations.json에 주소가 평문으로 없음", (b"secret-path" in raw, b"hooks.example.com" in raw), (False, False))
        rows60 = {"columns": ["n", "v"], "rows": [{"n": i, "v": f"r{i}"} for i in range(60)]}
        text = "\n".join(["n | v"] + [f"{i} | r{i}" for i in range(50)] + ["… 외 10행"])
        presets = {p["label"]: p for p in c.get("/api/destinations").get_json()["kinds"]["webhook"]["presets"]}
        for label, want in [("일반 JSON", {"title": weird, "text": "내용 & x=y", "rows": "1,234", "seconds": "1분 23초", "table": text, "image": base64.b64encode(png).decode()}),
                            ("Teams", {"text": f"**{weird}**\n\n내용 & x=y\n\n{text}"}), ("Discord", {"content": f"**{weird}**\n내용 & x=y\n{text}"}), ("글자", None)]:
            p = presets[label]
            put("hook", kind="webhook", done=False, url="", content=p["content"], template=p["template"])
            sent.clear()
            body(table=rows60, image=url)
            data = sent[0][1].decode("utf-8")
            check(f"기본 틀 {label}: 채운 뒤 유효하고 표는 50행까지({chr(0x2026)} 외 N행)", json.loads(data) if p["content"] == "json" else data,
                  want if want else f"{weird}\n내용 & x=y\n{text}")
        put("hook", kind="webhook", done=False, url="", content="json", template='{"a": {제목}')
        sent.clear()
        check("틀이 깨진 JSON이면 보내지 않고 거부", (error("/api/send", name="hook", title="t").startswith("본문 틀이 JSON이 아닙니다."), sent), (True, []))
        put("hook", kind="webhook", done=False, url="", content="text", template="{표}")
        sent.clear()
        got("/api/send", name="hook", title="t", table={"columns": ["a"], "rows": [[1], [2]]})
        check("표 모양: 머리글과 행을 ' | '로, 줄바꿈으로", sent[0][1], b"a\n1\n2")
        reply[0] = ValueError(f"보내지 못했습니다: {hook} 연결 거부")
        e = error("/api/send", name="hook", title="t")
        check("실패 문구에서 주소를 가림", (hook in e, "secret-path" in e, "***" in e), (False, False, True))
        reply[0] = b"ok"
        sent.clear()
        check("보내기 입력 거부: 그림 형식·크기, 표 모양, 숫자", [error("/api/send", name="hook", **b) for b in (
              {"title": "t", "image": "data:image/jpeg;base64,AAAA"}, {"title": "t", "image": "data:image/png;base64,@@@@"},
              {"title": "t", "image": "data:image/png;base64," + "A" * (5 * 1024 * 1024 * 4 // 3 + 100)}, {"title": "t", "table": [1]}, {"title": "t", "rows": "3"}, {"title": 1})],
              ["그림은 PNG data URL(data:image/png;base64,…)이어야 합니다.", "그림을 읽을 수 없습니다.", "그림은 5MB까지 보낼 수 있습니다.",
               '표는 {"columns": [...], "rows": [...]} 모양이어야 합니다.', "행수는 정수, 걸린 시간은 수(초)여야 합니다.", "제목과 내용은 글자여야 합니다."])
        check("거부된 요청은 나가지 않음, 없는 이름은 거부", (sent, error("/api/send", name="없음", title="t")), ([], "'없음' 보낼 곳이 없습니다."))
    finally:
        transport.post = real
    done = []
    thread = threading.Thread(target=lambda: done.append(c.post("/api/send", json={"name": "없음", "title": "t"}).status_code))
    server.turn.acquire()  # 다른 데이터 작업이 도는 중인 것처럼
    try:
        thread.start()
        thread.join(5)
        check("/api/send는 차례(turn)를 기다리지 않음", done, [400])
    finally:
        server.turn.release()
    check("시간 표현", [notify.duration_text(s) for s in (23, 59.6, 60, 83, 3600)], ["23초", "1분 0초", "1분 0초", "1분 23초", "60분 0초"])
    check("알림 기준 시간(notify_after) 범위: 5~3600초 정수", ([got("/api/settings", notify_after=v).get("ok") for v in (5, 45, 3600)], [error("/api/settings", notify_after=v)[:18] for v in (4, 3601, "30", 1.5)],
          settings.load().get("notify_after")), ([True] * 3, ["'notify_after' 설정에"] * 4, 3600))
    got("/api/settings", notify_after=0)
    check("0을 저장하면 기본으로", "notify_after" in settings.load(), False)
    for name in list(c.get("/api/destinations").get_json()["destinations"]):
        got("/api/destinations", name=name, destination=None)
    check("지우면 목록이 빔", c.get("/api/destinations").get_json()["destinations"], {})


class FakeApi:
    """127.0.0.1의 임의 포트에 띄우는 가짜 API 서버. routes[경로](요청 기록) → (상태, 헤더, 몸통). 헤더의 _cut은 몸통을 그 바이트에서 끊는다."""

    def __init__(self):
        import http.server
        self.routes, self.log = {}, []
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self):
                parts = urllib.parse.urlsplit(self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                entry = {"t": time.time(), "method": self.command, "path": parts.path, "query": dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True)),
                         "raw_query": parts.query, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body}
                fake.log.append(entry)
                route = fake.routes.get(parts.path)
                status, headers, data = route(entry) if route else (404, {}, b"no route")
                headers = dict(headers)
                cut = headers.pop("_cut", None)
                if "Date" in headers:
                    self.send_response_only(status)
                else:
                    self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                if "Content-Length" not in headers:
                    self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data if cut is None else data[:cut])

            do_GET = do_POST = do_PUT = do_DELETE = answer

        self.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.daemon_threads = True
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.http.server_port}"

    def hits(self, path):
        return [e for e in self.log if e["path"] == path]


def ranged(data, etag, entry, support=True):
    """Range 요청에 답한다(지원하지 않으면 200으로 전체). If-Range가 지금 ETag와 다르면 전체."""
    rng, if_range = entry["headers"].get("range"), entry["headers"].get("if-range")
    if support and rng and (not if_range or if_range == etag):
        m = re.match(r"bytes=(\d+)-(\d*)", rng)
        start, end = int(m[1]), min(int(m[2]) if m[2] else len(data) - 1, len(data) - 1)
        return 206, {"Content-Range": f"bytes {start}-{end}/{len(data)}", **({"ETag": etag} if etag else {})}, data[start:end + 1]
    return 200, {"ETag": etag} if etag else {}, data


def email_date(seconds):
    import email.utils
    return email.utils.formatdate(seconds, usegmt=True)


def raised_error(work):
    try:
        work()
    except Exception as e:
        return e


def collect_section(c, got):
    """수집(1단계 서버). 바깥 인터넷 없이 127.0.0.1의 가짜 서버로만 확인한다. 데이터는 여기서 지어낸다."""
    from collect import collect_http
    from collect import collect_limits
    from collect import collect_request as creq
    print("== 수집: 예시 읽기")
    error = lambda api, **body: got(api, **body).get("error", "")
    parse = lambda text: got("/api/collect/parse", text=text)
    r = parse("curl 'https://api.example.com/v1/items?serviceKey=abc%2B1&pageNo=1' \\\n  -X POST -H 'Authorization: Bearer tok-123' -H 'X-Api-Key: k-456' \\\n"
              "  -H 'Content-Length: 9' --compressed -k --data-raw '{\"q\": \"x\", \"auth\": {\"token\": \"t-789\"}}' -H 'Content-Type: application/json' -u me:pw-1")
    check("curl: 방식·주소(쿼리는 목록으로, -u는 user:pass@)·헤더(Content-Length는 뺌)·본문 종류", (r["format"], r["request"]["method"], r["request"]["url"], r["request"]["query"],
          [h[0] for h in r["request"]["headers"]], r["request"]["body_kind"]),
          ("curl", "POST", "https://me:pw-1@api.example.com/v1/items", [["serviceKey", "abc+1"], ["pageNo", "1"]], ["Authorization", "X-Api-Key", "Content-Type"], "json"))
    check("curl: 모르는 옵션은 unread", r["unread"], ["-k"])
    check("비밀 찾기: 주소의 비밀번호, Authorization, 이름에 key, 쿼리 serviceKey, JSON 본문 auth.token", [(s["where"], s["name"], s["path"]) for s in r["secrets"]],
          [("url", "password", ""), ("header", "Authorization", ""), ("header", "X-Api-Key", ""), ("query", "serviceKey", ""), ("body", "token", "auth.token")])
    cmd = parse('curl ^"https://h.example.com/a?x=1^" ^\n  -H ^"Accept: text/csv^" ^\n  -G --data-urlencode ^"q=a b^&c^" -d ^"n=2^" -A ^"agent/1^" -b ^"sid=s1^"')
    check("curl cmd(^ 이음): -G로 데이터가 쿼리로, --data-urlencode 인코딩, -A·-b는 헤더", (cmd["request"]["method"], cmd["request"]["query"], cmd["request"]["headers"], cmd["request"]["body_kind"]),
          ("GET", [["x", "1"], ["q", "a b&c"], ["n", "2"]], [["Accept", "text/csv"], ["User-Agent", "agent/1"], ["Cookie", "sid=s1"]], "none"))
    ps = parse("curl.exe --url https://h.example.com/b `\n  --json '{\"a\": 1}' -d @body.json --data-urlencode name@file.txt -b cookies.txt --retry 3")
    check("curl PowerShell(` 이음): --json, @파일 첨부·쿠키 파일·모르는 옵션은 unread", (ps["request"]["method"], ps["request"]["body"], ps["request"]["body_kind"], ps["unread"]),
          ("POST", '{"a": 1}', "json", ["-d @body.json (파일 첨부는 읽지 않습니다)", "--data-urlencode name@file.txt (파일 첨부는 읽지 않습니다)",
                                       "-b cookies.txt (쿠키 파일은 읽지 않습니다)", "--retry", "3"]))
    form = parse("curl https://h.example.com/f -d 'user=a&password=p1&n=2'")
    check("curl -d form: 본문 종류 form, form 필드 password가 비밀", (form["request"]["body_kind"], [(s["where"], s["name"]) for s in form["secrets"]]), ("form", [("body", "password")]))
    h = parse("POST /v2/search?page=1&access_token=a1 HTTP/1.1\nHost: api.example.com\nContent-Type: application/x-www-form-urlencoded\nCookie: s=1\nAccept-Encoding: gzip\n\nq=hello&n=1")
    check("HTTP 요청 글자: Host로 https 주소, 쿼리 나눔, 버릴 헤더(Host·Accept-Encoding) 뺌, form 본문, 비밀(쿼리·Cookie)",
          (h["format"], h["request"]["url"], h["request"]["query"], [k for k, _ in h["request"]["headers"]], h["request"]["body"], h["request"]["body_kind"],
           sorted((s["where"], s["name"]) for s in h["secrets"])),
          ("http", "https://api.example.com/v2/search", [["page", "1"], ["access_token", "a1"]], ["Content-Type", "Cookie"], "q=hello&n=1", "form",
           [("header", "Cookie"), ("query", "access_token")]))
    j = parse(json.dumps({"method": "put", "url": "https://h.example.com/j?a=1", "headers": {"X-Session": "s9"}, "query": [["b", "2"]], "body": {"k": [1, 2]}, "extra": 1}))
    check("JSON 예시: headers 객체·query 목록·객체 본문(json), 모르는 키는 unread", (j["format"], j["request"]["method"], j["request"]["query"], j["request"]["headers"], j["request"]["body"],
          j["request"]["body_kind"], j["unread"], [s["name"] for s in j["secrets"]]),
          ("json", "PUT", [["a", "1"], ["b", "2"]], [["X-Session", "s9"], ["Content-Type", "application/json"]], '{"k": [1, 2]}', "json", ["extra"], ["X-Session"]))
    check("예시 거부: 모르는 모양, ftp 주소, 주소 없는 curl", [error("/api/collect/parse", text=t) for t in ("hello", "curl ftp://x.example.com/a", "curl -H 'A: b'")],
          ['curl 명령, HTTP 요청 글자, JSON({"method", "url", …}) 중 하나로 붙여 넣으세요.', "주소는 http:// 또는 https://로 시작해야 합니다.", "curl 명령에서 주소를 찾지 못했습니다."])

    print("== 수집: 비밀을 {env:이름}으로, 저장 거부")
    replace = [{"where": s["where"], "name": s["name"], "path": s["path"], "env": f"E{i}"} for i, s in enumerate(r["secrets"])]
    s = got("/api/collect/secretize", request=r["request"], replace=replace)["request"]
    check("secretize: 주소 비밀번호·Bearer 앞말 유지·헤더·쿼리·JSON 본문(모양 유지)", (s["url"], s["headers"][:2], s["query"][0], s["body"]),
          ("https://me:{env:E0}@api.example.com/v1/items", [["Authorization", "Bearer {env:E1}"], ["X-Api-Key", "{env:E2}"]], ["serviceKey", "{env:E3}"],
           '{"q": "x", "auth": {"token": "{env:E4}"}}'))
    check("바꾼 뒤에는 남은 비밀 없음", creq.secrets(s), [])
    check("secretize: env 이름 검사", error("/api/collect/secretize", request=r["request"], replace=[{**replace[0], "env": "1bad"}]).startswith("env 이름은 "), True)
    config = {"version": 1, "mode": "api", "request": r["request"], "output": {"format": "parquet", "path": "", "dedupe": []}}
    e = error("/api/collections", name="raw", collection=config)
    check("구성 저장 거부: 위치 이름만 알리고 값은 넣지 않음", (e, any(v in e for v in ("tok-123", "k-456", "abc+1", "t-789", "pw-1"))),
          ("비밀처럼 보이는 자리에 값이 그대로 들어 있습니다: 주소의 비밀번호, 헤더 Authorization, 헤더 X-Api-Key, 쿼리 serviceKey, 본문 auth.token. env 파일에 넣고 {env:이름}으로 바꾸세요.", False))
    check("비밀 이름은 낱말로 봄: keyword·author·monkey_bars는 아님", [n for n in ("serviceKey", "X-API-KEY", "access_token", "apikey", "sessionId", "keyword", "author", "sort", "monkey_bars")
          if creq.secret_name(n)], ["serviceKey", "X-API-KEY", "access_token", "apikey", "sessionId"])
    sort_key = {**config, "request": {**s, "query": [*s["query"], ["sort_key", "date"]], "headers": [*s["headers"], ["Cookie", "c=1"]]}}
    check("비밀이 아니라고 뺀 이름(plain)은 저장됨, Cookie는 plain이어도 비밀", (error("/api/collections", name="p", collection=sort_key),
          error("/api/collections", name="p", collection={**sort_key, "plain": ["SORT_KEY", "Cookie"]})),
          ("비밀처럼 보이는 자리에 값이 그대로 들어 있습니다: 헤더 Cookie, 쿼리 sort_key. env 파일에 넣고 {env:이름}으로 바꾸세요.",
           "비밀처럼 보이는 자리에 값이 그대로 들어 있습니다: 헤더 Cookie. env 파일에 넣고 {env:이름}으로 바꾸세요."))
    saved = got("/api/collections", name="공공", collection={**config, "request": s})
    check("바꾼 구성은 저장됨(빠진 칸은 기본값), 목록", (list(saved["collections"]), saved["collections"]["공공"]["request"]["url"], saved["collections"]["공공"]["paging"]["kind"],
          c.get("/api/collections").get_json()["collections"]["공공"]["request"]["query"][0]), (["공공"], s["url"], "none", ["serviceKey", "{env:E3}"]))
    check("구성 검사: version·모르는 키·잘못된 값", [error("/api/collections", name="x", collection=v) for v in (
          {"version": 2}, {"version": 1, "extra": 1}, {"request": {"nope": 1}}, {"paging": {"kind": "scroll"}}, {"balance": {"concurrency": 0}}, {"request": {"headers": [["a"]]}})],
          ["구성의 version은 1이어야 합니다.", "구성에 쓸 수 없는 값이 있습니다: extra", "구성에 쓸 수 없는 값이 있습니다: request.nope",
           "구성의 paging.kind는 none, page, offset, cursor, next_url 중 하나입니다.", "구성의 balance.concurrency는 1~32의 정수여야 합니다.",
           "구성의 request.headers는 [[이름, 값], …] 모양이어야 합니다."])
    check("파일 모드의 헤더·주소 쿼리도 검사", error("/api/collections", name="x", collection={"mode": "file", "file": {"url": "https://h.example.com/d.csv?token=zz", "headers": [["Authorization", "Bearer q"]]}}),
          "비밀처럼 보이는 자리에 값이 그대로 들어 있습니다: 파일 주소의 token, 파일 헤더 Authorization. env 파일에 넣고 {env:이름}으로 바꾸세요.")
    check("구성 지우기", got("/api/collections", name="공공", collection=None), {"collections": {}})

    print("== 수집: env 파일")
    env_file = ROOT / "collect.env"
    secret_key, secret_tok = "Key/With+Plus=xyz789", "tok-SECRET-55"
    env_file.write_bytes(("\ufeff# 주석\n\nexport API_KEY=" + secret_key + "  # 뒤 주석\nTOKEN=\"" + secret_tok + "\"\nQUOTED='a #b'\nESC=\"x\\ny\"\nEMPTY=\n").encode("utf-8"))
    check("env: BOM·주석·export·따옴표·빈 값, 이름만 돌려줌", (got("/api/collect/env", path=str(env_file)), settings.read_env(env_file)),
          ({"names": ["API_KEY", "TOKEN", "QUOTED", "ESC", "EMPTY"]}, {"API_KEY": secret_key, "TOKEN": secret_tok, "QUOTED": "a #b", "ESC": "x\ny", "EMPTY": ""}))
    bad_env = ROOT / "bad.env"
    bad_env.write_text("OK=1\nthis line secret-value\n", encoding="utf-8")
    e = error("/api/collect/env", path=str(bad_env))
    check("env: 읽지 못한 줄은 번호만(값 없음)", (e, "secret-value" in e), ("env 파일 2번째 줄을 읽을 수 없습니다(이름=값 모양이어야 합니다).", False))

    print("== 수집: 자리 채우기")
    ctx = {"env": {"K": 'a b&c=d/한"\n'}, "page": 3, "offset": 200, "cursor": "", "now": dt.datetime(2026, 1, 2, 3, 4, 5)}
    built = creq.build({"method": "POST", "url": "https://h.example.com/p/{env:K}/{page}", "query": [["k", "{env:K}"], ["d", "{today:%Y%m%d}"], ["n", "{now}"]],
                        "headers": [["X-D", "{today}"]], "body_kind": "json", "body": '{"k": "{env:K}", "page": {page}, "o": "{offset}", "c": "{cursor}", "lit": {"a": 1}}'}, ctx)
    check("주소 경로는 quote, 쿼리는 URL 인코딩, 날짜 형식", (built["url"], built["headers"]),
          ("https://h.example.com/p/a%20b%26c%3Dd%2F%ED%95%9C%22%0A/3?k=a+b%26c%3Dd%2F%ED%95%9C%22%0A&d=20260102&n=2026-01-02+03%3A04%3A05",
           [["X-D", "2026-01-02"], ["Content-Type", "application/json"]]))
    check("JSON 본문: 글자 안은 JSON 이스케이프, 밖({page})은 그대로, 다른 중괄호는 그대로", json.loads(built["body"]),
          {"k": 'a b&c=d/한"\n', "page": 3, "o": "200", "c": "", "lit": {"a": 1}})
    built = creq.build({"method": "POST", "url": "https://h.example.com/f", "body_kind": "form", "body": "k={env:K}&p={page}"}, ctx)
    check("form 본문은 quote_plus, Content-Type", (urllib.parse.parse_qs(built["body"].decode()), built["headers"]),
          ({"k": ['a b&c=d/한"\n'], "p": ["3"]}, [["Content-Type", "application/x-www-form-urlencoded"]]))
    check("채운 JSON이 깨지면 거부, 헤더 줄바꿈 거부", [str(raised_error(lambda: creq.build({"url": "https://h.example.com", "body_kind": "json", "body": '{"a": {page'}, ctx))),
          str(raised_error(lambda: creq.build({"url": "https://h.example.com", "headers": [["X", "{env:K}"]]}, ctx)))],
          ["자리를 채운 본문이 JSON이 아닙니다. 본문과 자리 표시를 확인하세요.", "헤더 이름·값에 줄바꿈을 넣을 수 없습니다."])
    check("주소의 user:pass@는 Basic 인증 헤더로(주소에서는 뺌)", (lambda b: (b["url"], b["headers"]))(creq.build({"url": "https://me:{env:K}@h.example.com/a"}, {**ctx, "env": {"K": "p@ss"}})),
          ("https://h.example.com/a", [["Authorization", "Basic " + base64.b64encode(b"me:p@ss").decode()]]))

    print("== 수집: 응답 매핑(미리보기)")
    sample_text = json.dumps({"data": {"list": [{"id": 1, "n": 1.5, "m": 1, "o": {"a": {"b": "x"}}, "tags": [1, "가"], "mix": 1},
                                                 {"id": 2, "n": 2, "m": 2.5, "o": {"a": {"b": "y"}}, "tags": [], "mix": "two", "extra": True}]}, "next": {"cursor": "c2"}, "total": "40"})
    base_cfg = {"response": {"records": "data.list", "columns": []}, "paging": {"kind": "cursor", "cursor_path": "next.cursor", "total_path": "total"}}
    r = got("/api/collect/sample", config=base_cfg, text=sample_text)
    check("평탄화(a.b), 목록은 JSON 글자, 타입(섞이면 문자, 정수+실수는 실수), 커서·전체 수", (r["count"], [(x["name"], x["type"]) for x in r["columns"]], r["rows"][0], r["cursor"], r["total"]),
          (2, [("id", "정수"), ("n", "실수"), ("m", "실수"), ("o.a.b", "문자"), ("tags", "문자"), ("mix", "문자"), ("extra", "참거짓")],
           {"id": 1, "n": 1.5, "m": 1, "o.a.b": "x", "tags": '[1, "가"]', "mix": 1}, "c2", 40))
    r = got("/api/collect/sample", config={"response": {"records": "data.list.1", "columns": [{"name": "아이디", "path": "id"}, {"name": "b", "path": "o.a.b"}, {"name": "없음", "path": "zz"}]}},
            text=sample_text)
    check("기록 경로에 숫자(배열 번호), 객체 하나는 기록 하나, columns 경로만", (r["count"], r["rows"]), (1, [{"아이디": 2, "b": "y", "없음": None}]))
    r = got("/api/collect/sample", config={"response": {"records": ""}}, text="[1, \"a\", null]")
    check("객체가 아닌 기록은 value 컬럼", (r["rows"], r["columns"]), ([{"value": 1}, {"value": "a"}, {"value": None}], [{"name": "value", "path": "value", "type": "문자"}]))
    check("기록 경로가 없으면·JSON이 아니면 거부", [error("/api/collect/sample", config={"response": {"records": "nope"}}, text=sample_text), error("/api/collect/sample", config={}, text="<x/>")],
          ["기록 경로 'nope'를 응답에서 찾지 못했습니다.", "응답 예시가 JSON이 아닙니다."])

    print("== 수집: 작업(가짜 서버)")
    api = FakeApi()
    collect_http.BACKOFF = 0.02  # 다시 시도 기다림을 짧게
    out = ROOT / "collect_out"
    out.mkdir()

    def item(i):
        return {"id": i, "name": f"n{i}", "score": i + 0.5, "flag": i % 2 == 0, "tags": ["a", i], "meta": {"a": i, "b": {"c": "x"}}, "mixed": i if i % 2 else f"s{i}", "maybe": None}

    def items_route(e):
        page = int(e["query"].get("page", 1))
        rows = [item((page - 1) * 3 + k) for k in (1, 2, 3)] if page <= 5 else []
        return 200, {"Content-Type": "application/json"}, json.dumps({"response": {"body": {"items": rows, "total": 15}}}).encode()

    api.routes["/items"] = items_route

    def config(path, name, **more):
        found = {"version": 1, "mode": "api", "env_path": str(env_file), "request": {"method": "GET", "url": api.base + path, "query": [], "headers": []},
                 "response": {"records": "response.body.items", "columns": []}, "paging": {"kind": "page", "start": 1, "max_pages": 100},
                 "balance": {"concurrency": 2, "interval": 0, "retries": 3, "timeout": 10}, "output": {"format": "parquet", "path": str(out / name) if name else "", "dedupe": []}}
        for key, value in more.items():
            found[key] = {**found.get(key, {}), **value} if isinstance(value, dict) else value
        return found

    def wait(seconds=30):
        end = time.time() + seconds
        while time.time() < end and c.get("/api/collect/status").get_json()["running"]:
            time.sleep(0.02)
        return c.get("/api/collect/status").get_json()

    def run(cfg):
        return got("/api/collect/start", config=cfg), wait()

    cfg = config("/items", "items.parquet", request={"query": [["page", "{page}"], ["key", "{env:API_KEY}"]]})
    started, st = run(cfg)
    frame = pl.read_parquet(out / "items.parquet")
    check("page 쪽 넘기기(동시 2): 빈 쪽에서 멈춤, 15행, 받은 순서", (started, st["phase"], st["rows"], frame["id"].to_list()), ({"ok": True, "resumed": False}, "끝", 15, list(range(1, 16))))
    check("결과 Parquet 타입: 정수·글자·실수·참거짓·목록은 JSON 글자·a.b·섞이면 글자·전부 빈 값은 글자", {k: str(v) for k, v in frame.schema.items()},
          {"id": "Int64", "name": "String", "score": "Float64", "flag": "Boolean", "tags": "String", "meta.a": "Int64", "meta.b.c": "String", "mixed": "String", "maybe": "String"})
    check("결과 값: 목록 JSON 글자, 섞인 값은 글자로", (frame["tags"][0], frame["mixed"].to_list()[:2]), ('["a", 1]', ["1", "s2"]))
    check("쿼리의 env 값이 인코딩돼 나감, 작업 파일은 정리됨", (api.hits("/items")[0]["query"]["key"], sorted(p.name for p in out.iterdir())), (secret_key, ["items.parquet"]))
    check("상태: 끝난 결과 경로·요청 수·쪽 수", (st["output"], st["requests"] >= 6, st["pages"] >= 6, st["mode"]), (str(out / "items.parquet"), True, True, "api"))

    api.routes["/dup"] = lambda e: (200, {}, json.dumps([{"id": k, "name": f"p{e['query']['page']}"} for k in (1, 2, 3)] if int(e["query"]["page"]) <= 3 else []).encode())
    run(config("/dup", "dup.csv", request={"query": [["page", "{page}"]]}, response={"records": ""}, output={"format": "csv", "dedupe": ["id"]}))
    data = (out / "dup.csv").read_bytes()
    check("중복 제거(받은 순서로 첫 행), CSV는 UTF-8 BOM", (data[:3], data[3:].decode("utf-8").splitlines()), (b"\xef\xbb\xbf", ["id,name", "1,p1", "2,p1", "3,p1"]))

    locked = out / "locked.parquet"
    locked.write_bytes(b"old")
    with open(locked, "rb"):  # 다른 프로그램이 결과 파일을 잡고 있는 것처럼
        _, st = run(config("/items", "locked.parquet", request={"query": [["page", "{page}"]]}))
    check("결과 파일이 잡혀 있으면 알리고 받은 것은 남김(바꾸던 파일은 지움)", (st["phase"], st["error"].startswith("다른 프로그램이 파일을 쓰고 있습니다."), locked.read_bytes(),
          sorted(p.name for p in out.iterdir() if p.name.startswith("locked"))), ("실패", True, b"old", ["locked.parquet", "locked.part", "locked.part.json"]))
    count = len(api.hits("/items"))
    started, st = run(config("/items", "locked.parquet", request={"query": [["page", "{page}"]]}))
    check("놓은 뒤 다시 시작하면 다시 받지 않고 바꾸기만 함", (started["resumed"], st["phase"], len(api.hits("/items")) - count, st["requests"], pl.read_parquet(locked).height),
          (True, "끝", 0, 0, 15))

    def offset_route(e):
        start, size = int(e["query"]["offset"]), int(e["query"]["limit"])
        return 200, {}, json.dumps({"total": 10, "rows": [{"v": i} for i in range(start, min(start + size, 10))]}).encode()

    api.routes["/off"] = offset_route
    run(config("/off", "off.parquet", request={"query": [["offset", "{offset}"], ["limit", "4"]]}, response={"records": "rows"},
               paging={"kind": "offset", "start": 0, "step": 4, "total_path": "total"}))
    check("offset: 전체 수(total)에 닿으면 더 요청하지 않음(3번), 10행", (len(api.hits("/off")), pl.read_parquet(out / "off.parquet")["v"].to_list()), (3, list(range(10))))

    chain = {"": "c1", "c1": "c2", "c2": "c1"}
    api.routes["/cur"] = lambda e: (200, {}, json.dumps({"items": [{"c": e["query"].get("cursor", "")}], "next": chain[e["query"].get("cursor", "")]}).encode())
    run(config("/cur", "cur.parquet", request={"query": [["cursor", "{cursor}"]]}, response={"records": "items"}, paging={"kind": "cursor", "cursor_path": "next"}))
    check("cursor: 첫 커서는 빈 글자, 같은 커서가 다시 오면 멈춤", ([e["query"].get("cursor") for e in api.hits("/cur")], pl.read_parquet(out / "cur.parquet")["c"].to_list()),
          (["", "c1", "c2"], ["", "c1", "c2"]))

    def link_route(e):
        p = int(e["query"].get("p", 1))
        return 200, ({"Link": f'</nx?p={p + 1}&k=1>; rel="next", </nx?p=1>; rel="first"'} if p < 3 else {}), json.dumps([{"p": p}]).encode()

    api.routes["/nx"] = link_route
    run(config("/nx", "nx.parquet", request={"query": [["p", "1"]], "headers": [["X-Keep", "h1"]]}, response={"records": ""}, paging={"kind": "next_url"}))
    check("next_url(Link 헤더): 받은 주소를 그대로, 헤더는 유지", ([e["raw_query"] for e in api.hits("/nx")], {e["headers"].get("x-keep") for e in api.hits("/nx")},
          pl.read_parquet(out / "nx.parquet")["p"].to_list()), (["p=1", "p=2&k=1", "p=3&k=1"], {"h1"}, [1, 2, 3]))
    api.routes["/np"] = lambda e: (200, {}, json.dumps({"d": [{"p": e["query"]["p"]}], "next": "/np?p=2" if e["query"]["p"] == "1" else "/np?p=1"}).encode())
    run(config("/np", "np.parquet", request={"query": [["p", "1"]]}, response={"records": "d"}, paging={"kind": "next_url", "next_path": "next"}))
    check("next_url(next_path, 상대 주소): 같은 주소가 다시 오면 멈춤", [e["raw_query"] for e in api.hits("/np")], ["p=1", "p=2"])

    print("== 수집: 받는 쪽 배려(429·5xx·401)")
    seen = {"n": 0}

    def limited(e):
        seen["n"] += 1
        return (429, {"Retry-After": "1"}, b"slow down") if seen["n"] == 1 else items_route(e)

    api.routes["/rl"] = limited
    _, st = run(config("/rl", "rl.parquet", request={"query": [["page", "{page}"]]}, balance={"concurrency": 4}))
    times = [e["t"] for e in api.hits("/rl")]
    check("429 + Retry-After(초): 그만큼 쉬고 다시, 동시 수 반으로(4→2), 실패 1번", (st["phase"], st["rows"], times[1] - times[0] > 0.95, st["concurrency"], st["failures"]),
          ("끝", 15, True, 2, 1))
    later = {"n": 0}

    def limited_date(e):
        later["n"] += 1
        return (429, {"Retry-After": email_date(time.time() + 2)}, b"") if later["n"] == 1 else items_route(e)

    api.routes["/rl2"] = limited_date
    _, st = run(config("/rl2", "rl2.parquet", request={"query": [["page", "{page}"]]}))
    times = [e["t"] for e in api.hits("/rl2")]
    check("429 + Retry-After(HTTP 날짜)도 따름", (st["phase"], times[1] - times[0] > 0.9), ("끝", True))
    api.routes["/far"] = lambda e: (429, {"Retry-After": "3600"}, b"")
    _, st = run(config("/far", "far.parquet", request={"query": [["page", "{page}"]]}))
    check("Retry-After가 15분을 넘으면 멈추고 알림(이어 받기 남김)", (st["phase"], st["message"].startswith("429 응답: 서버가 60분 뒤에"), (out / "far.part.json").exists()), ("멈춤", True, True))
    api.routes["/auth"] = lambda e: (401, {}, b"no")
    _, st = run(config("/auth", "auth.parquet", request={"query": [["page", "{page}"]]}))
    check("401은 다시 시도 없이 바로 멈춤", (st["phase"], st["error"], len(api.hits("/auth"))), ("실패", "인증을 확인하세요(상태 401). env 파일의 키와 헤더를 확인하세요.", 1))
    flaky = {"n": 0}

    def flaky_route(e):
        flaky["n"] += 1
        return (500, {}, b"oops") if flaky["n"] <= 2 else (200, {}, b'[{"a": 1}]')

    api.routes["/flaky"] = flaky_route
    _, st = run(config("/flaky", "flaky.parquet", response={"records": ""}, paging={"kind": "none"}))
    check("5xx는 다시 시도해 성공", (st["phase"], len(api.hits("/flaky")), st["failures"], st["rows"]), ("끝", 3, 2, 1))
    api.routes["/echo"] = lambda e: (400, {}, f"bad key {e['query'].get('k')} / {e['headers'].get('x-token')}".encode())
    _, st = run(config("/echo", "echo.parquet", request={"query": [["k", "{env:API_KEY}"]], "headers": [["X-Token", "{env:TOKEN}"]]}, paging={"kind": "none"}))
    check("다른 4xx는 멈춤, 오류 문구에서 env 값을 ●●●로 가림", (st["phase"], secret_key in st["error"], secret_tok in st["error"], st["error"]),
          ("실패", False, False, "요청이 거부됐습니다(상태 400): bad key ●●● / ●●●"))

    print("== 수집: 시험 보내기·넘겨주기")
    api.routes["/try"] = lambda e: (200, {"Content-Type": "application/json; charset=euc-kr", "X-Echo": e["query"].get("k", "")},
                                    json.dumps({"msg": "한글", "k": e["query"].get("k")}, ensure_ascii=False).encode("cp949"))
    r = got("/api/collect/try", config=config("/try", "", request={"query": [["k", "{env:API_KEY}"]], "headers": [["Authorization", "Bearer {env:TOKEN}"]]}))
    check("시험: sent의 비밀은 ●●●, 응답 charset(euc-kr)을 따름, 응답에 되돌아온 키도 가림", (r["status"], r["sent"]["url"], r["sent"]["headers"], r["json"], dict(r["headers"])["X-Echo"],
          secret_key in json.dumps(r, ensure_ascii=False)),
          (200, api.base + "/try?k=●●●", [["Authorization", "Bearer ●●●"]], {"msg": "한글", "k": "●●●"}, "●●●", False))
    check("시험·시작 전 없는 env 이름은 이름만 알림", [error("/api/collect/try", config=config("/try", "", request={"query": [["k", "{env:NOPE}"], ["j", "{env:NOPE2}"]]})),
          error("/api/collect/start", config=config("/try", "x.parquet", env_path="", request={"query": [["k", "{env:A}"]]}))],
          ["env 파일에 없는 이름이 있습니다: NOPE, NOPE2", "env 파일을 고르세요. 쓰인 이름: A"])
    check("시험·시작 때도 글자 그대로의 비밀은 거부", [error(api_path, config=config("/try", "x.parquet", request={"headers": [["X-Api-Key", "raw"]]})) for api_path in ("/api/collect/try", "/api/collect/start")],
          ["비밀처럼 보이는 자리에 값이 그대로 들어 있습니다: 헤더 X-Api-Key. env 파일에 넣고 {env:이름}으로 바꾸세요."] * 2)
    port = api.http.server_port
    api.routes["/hop"] = lambda e: (302, {"Location": f"http://localhost:{port}/land"}, b"")
    api.routes["/hop2"] = lambda e: (307, {"Location": "/land"}, b"")
    api.routes["/land"] = lambda e: (200, {}, b"[]")
    hop = lambda path: config(path, "", paging={"kind": "none"}, request={"headers": [["Authorization", "Bearer {env:TOKEN}"], ["X-Other", "v-{env:TOKEN}"], ["X-Plain", "p"]]})
    got("/api/collect/try", config=hop("/hop"))
    landed = api.hits("/land")[-1]["headers"] if api.hits("/land") else {}
    check("다른 호스트로 넘겨주면 Authorization·env 값이 든 헤더를 뺌", ("authorization" in landed, "x-other" in landed, landed.get("x-plain")), (False, False, "p"))
    got("/api/collect/try", config=hop("/hop2"))
    check("같은 호스트로 넘겨주면 그대로", api.hits("/land")[-1]["headers"].get("authorization"), f"Bearer {secret_tok}")
    api.routes["/loop"] = lambda e: (302, {"Location": "/loop"}, b"")
    check("넘겨주기는 5번까지", error("/api/collect/try", config=config("/loop", "")), "넘겨주기(redirect)가 5번을 넘었습니다.")

    class FakeReply:
        status, headers = 302, {"Location": "http://h.example.com/x"}

        def close(self):
            pass

    real_opener = collect_http.OPENER
    collect_http.OPENER = type("Opener", (), {"open": lambda self, request, timeout: FakeReply()})()
    try:
        check("https에서 http로 넘겨주면 거부", str(raised_error(lambda: collect_http.open_url("GET", "https://h.example.com/a", [], None, 5))),
              "https 주소에서 http 주소로 넘겨주는 것은 따라가지 않습니다.")
    finally:
        collect_http.OPENER = real_opener
    collect_http.MAX_BYTES = 100
    api.routes["/big"] = lambda e: (200, {}, b"[" + b"1," * 200 + b"1]")
    try:
        check("응답 크기 상한을 넘으면 파일 주소로 받으라고 알림", error("/api/collect/try", config=config("/big", "")).endswith("큰 파일은 파일 주소로 받으세요(파일 모드)."), True)
    finally:
        collect_http.MAX_BYTES = 200 * 1024 * 1024
    check("http·https가 아닌 주소는 거부", error("/api/collect/try", config=config("/x", "", request={"url": "file:///C:/x.json"})), "주소는 http:// 또는 https://로 시작해야 합니다.")
    api.routes["/skew"] = lambda e: (200, {"Date": email_date(time.time() - 3600)}, b"[1]")
    _, st = run(config("/skew", "skew.parquet", response={"records": ""}, paging={"kind": "none"}))
    check("응답 Date가 PC 시계와 5분 넘게 다르면 알림", (st["phase"], st["message"].startswith("서버 시계와 PC 시계가 60분 다릅니다.")), ("끝", True))

    print("== 수집: 사용 한도")
    check("묶음 검사", [error("/api/limits", name="g", group=v) for v in ({"per_second": 0}, {"reset": "24:00"}, {"tz": "Mars/Base"}, {"margin": 60}, {"x": 1})],
          ["'per_second'는 비우거나 1 이상의 정수로 쓰세요.", "초기화 시각(reset)은 HH:MM 모양으로 쓰세요.", "알 수 없는 시간대입니다: Mars/Base", "여유(margin)는 0~50%로 쓰세요.",
           "사용 한도에 쓸 수 없는 값이 있습니다: x"])
    r = got("/api/limits", name="초당", group={"per_second": 2, "concurrency": 4})
    check("묶음 저장과 사용량(기본값 채움)", (r["groups"]["초당"]["tz"], r["usage"]["초당"]["day"], r["usage"]["초당"]["broken"], r["usage"]["초당"]["day_limit"]), ("Asia/Seoul", 0, False, None))
    count = len(api.hits("/items"))
    _, st = run(config("/items", "persec.parquet", limit_group="초당", request={"query": [["page", "{page}"]]}, balance={"concurrency": 4}))
    times = sorted(e["t"] for e in api.hits("/items")[count:])
    check("초당 2번: 어느 1초 안에도 2번 넘게 보내지 않음(보낸 시각 간격)", (st["phase"], len(times) >= 6, min(b - a for a, b in zip(times, times[2:])) >= 0.95, st["usage"]["second"] <= 2),
          ("끝", True, True, True))
    check("Asia/Seoul 시간대(tzdata가 없으면 UTC+9로 대신)", collect_limits.zone("Asia/Seoul").utcoffset(dt.datetime(2026, 1, 1)), dt.timedelta(hours=9))
    got("/api/limits", name="하루", group={"per_day": 3, "concurrency": 1})
    daily = config("/items", "daily.parquet", limit_group="하루", request={"query": [["page", "{page}"]]}, balance={"concurrency": 1})
    count = len(api.hits("/items"))
    _, st = run(daily)
    reset_at = collect_limits.reset_text(collect_limits.groups()["하루"], time.time())
    check("하루 한도에 닿으면 멈추고 이어 받기를 남김", (st["phase"], st["message"], len(api.hits("/items")) - count, st["usage"]["day_left"], (out / "daily.part.json").exists()),
          ("멈춤", f"오늘 한도를 다 썼습니다. {reset_at} 이후 이어 받을 수 있습니다.", 3, 0, True))
    check("estimate: 남은 요청 수·오늘 남은 횟수·이어 받기", got("/api/collect/estimate", config=daily), {"requests": 97, "day_left": 0, "resume": {"pages": 3, "bytes": st["bytes"]}})
    got("/api/limits", name="하루", group={"per_day": 100, "margin": 50, "concurrency": 1})
    started, st = run(daily)
    check("한도를 늘리면 이어 받음(받은 쪽은 다시 요청하지 않음), 여유 50%면 남은 횟수 50-사용", (started["resumed"], st["phase"], len(api.hits("/items")) - count,
          pl.read_parquet(out / "daily.parquet")["id"].to_list(), st["usage"]["day_left"]), (True, "끝", 6, list(range(1, 16)), 50 - 6))
    (settings.PATH.parent / collect_limits.USAGE).write_text("{깨짐", encoding="utf-8")
    usage = c.get("/api/limits").get_json()["usage"]["하루"]
    check("기록 파일이 깨지면 다 쓴 것으로 보고 시작 거부", (error("/api/collect/start", config=daily), usage["broken"], usage["day_left"]), (collect_limits.broken_message(), True, 0))
    r = got("/api/limits/reset", name="하루")
    check("초기화하면 풀림", (r["usage"]["하루"]["broken"], r["usage"]["하루"]["day"], got("/api/collect/start", config=daily)["ok"]), (False, 0, True))
    wait()
    api.routes["/srv"] = lambda e: (200, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1"} if e["query"]["page"] == "1" else {},
                                    json.dumps([{"p": 1}] if e["query"]["page"] in ("1", "2") else []).encode())
    got("/api/limits", name="서버", group={})
    _, st = run(config("/srv", "srv.parquet", limit_group="서버", request={"query": [["page", "{page}"]]}, response={"records": ""}, balance={"concurrency": 1}))
    times = [e["t"] for e in api.hits("/srv")]
    check("서버가 남은 횟수 0을 알리면 초기화까지 쉼, 사용량에 서버 값", (st["phase"], times[1] - times[0] > 0.9, st["usage"]["server"]), ("끝", True, {"remaining": 0, "reset": "1"}))
    check("잠금 파일은 남지 않음", (settings.PATH.parent / collect_limits.LOCK).exists(), False)
    for name in list(c.get("/api/limits").get_json()["groups"]):
        got("/api/limits", name=name, group=None)

    print("== 수집: 파일 모드")
    csv_text = "id,name,city\n" + "".join(f"{i},이름{i},{'서울' if i % 2 else '부산'}\n" for i in range(1, 2001))
    data = csv_text.encode("cp949")
    piece = math.ceil(len(data) / 4)
    api.routes["/k.csv"] = lambda e: ranged(data, '"v1"', e)

    def fcfg(path, name, **more):
        return config(path, name, mode="file", file={"url": api.base + path, "headers": [["Authorization", "Bearer {env:TOKEN}"]], "chunks": 4, **more},
                      output={"format": name.rsplit(".", 1)[1]}, balance={"retries": 1})

    _, st = run(fcfg("/k.csv", "k.parquet"))
    ranges = [e["headers"].get("range") for e in api.hits("/k.csv")]
    frame = pl.read_parquet(out / "k.parquet")
    check("파일 모드: Range 0-0으로 확인 후 4조각, cp949 CSV → Parquet", (st["phase"], ranges[0], sorted(ranges[1:]), frame.height, frame["name"][0], frame["city"][1], st["bytes"], st["total_bytes"]),
          ("끝", "bytes=0-0", sorted(f"bytes={k * piece}-{min(len(data), (k + 1) * piece) - 1}" for k in range(4)), 2000, "이름1", "부산", len(data), len(data)))
    check("조각 요청에도 인증 헤더(env 값)", {e["headers"].get("authorization") for e in api.hits("/k.csv")}, {f"Bearer {secret_tok}"})
    run(fcfg("/k.csv", "k.csv"))
    check("cp949 CSV → CSV: UTF-8로 바꾸고 BOM", (out / "k.csv").read_bytes(), b"\xef\xbb\xbf" + csv_text.encode("utf-8"))
    plain = csv_text.encode("utf-8")
    api.routes["/plain.csv"] = lambda e: ranged(plain, None, e, support=False)
    _, st = run(fcfg("/plain.csv", "plain.parquet"))
    check("Range를 받지 않는 서버는 한 줄기로(요청 1번)", (st["phase"], len(api.hits("/plain.csv")), pl.read_parquet(out / "plain.parquet").height), ("끝", 1, 2000))
    dropped = {"done": False}
    piece = math.ceil(len(plain) / 4)

    def drop(e):
        status, headers, body = ranged(plain, '"d1"', e)
        if status == 206 and not e["headers"]["range"].startswith("bytes=0-") and not dropped["done"]:
            dropped["done"] = True
            return status, {**headers, "_cut": len(body) // 2}, body
        return status, headers, body

    api.routes["/drop.csv"] = drop
    _, st = run(fcfg("/drop.csv", "drop.parquet"))
    starts = sorted(int(re.match(r"bytes=(\d+)", e["headers"]["range"])[1]) for e in api.hits("/drop.csv"))
    check("받는 중에 끊기면 끊긴 곳부터 다시 받아 완성", (st["phase"], any(s % piece for s in starts), pl.read_parquet(out / "drop.parquet")["id"].to_list() == list(range(1, 2001))), ("끝", True, True))
    bad = {"on": True, "etag": '"h1"', "data": plain}

    def halting(e):
        if bad["on"] and e["headers"].get("range", "").startswith(f"bytes={3 * piece}-"):
            return 500, {}, b"down"
        return ranged(bad["data"], bad["etag"], e)

    api.routes["/halt.csv"] = halting
    _, st = run(fcfg("/halt.csv", "halt.parquet"))
    record = json.loads((out / "halt.part.json").read_text(encoding="utf-8"))
    check("조각이 계속 실패하면 멈추고 받은 범위를 기록", (st["phase"], record["done"], (out / "halt.part").stat().st_size), ("실패", [[0, 3 * piece]], len(plain)))
    bad["on"] = False
    count = len(api.hits("/halt.csv"))
    started, st = run(fcfg("/halt.csv", "halt.parquet"))
    check("다시 시작하면 남은 범위만 이어 받음", (started["resumed"], st["phase"], [e["headers"]["range"] for e in api.hits("/halt.csv")[count:]],
          pl.read_parquet(out / "halt.parquet").height, sorted(p.name for p in out.iterdir() if p.name.startswith("halt"))),
          (True, "끝", ["bytes=0-0", f"bytes={3 * piece}-{len(plain) - 1}"], 2000, ["halt.parquet"]))
    bad["on"] = True
    run(fcfg("/halt.csv", "etag.parquet"))
    changed = ("id,name,city\n" + "".join(f"{i},새이름{i},x\n" for i in range(1, 2001))).encode("utf-8")
    bad.update(on=False, etag='"h2"', data=changed)
    count = len(api.hits("/halt.csv"))
    started, st = run(fcfg("/halt.csv", "etag.parquet"))
    asked = [e["headers"]["range"] for e in api.hits("/halt.csv")[count:]][1:]
    check("ETag가 바뀌면 처음부터 다시 받음", (started["resumed"], st["phase"], sum(int(b) - int(a) + 1 for a, b in (re.match(r"bytes=(\d+)-(\d+)", x).groups() for x in asked)),
          pl.read_parquet(out / "etag.parquet")["name"][0]), (True, "끝", len(changed), "새이름1"))
    buffer = io.BytesIO()
    pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]}).write_parquet(buffer)
    api.routes["/d.parquet"] = lambda e: ranged(buffer.getvalue(), '"p"', e)
    run(fcfg("/d.parquet", "d.parquet", chunks=2))
    run(fcfg("/d.parquet", "dp.csv", chunks=2))
    check("Parquet 파일은 그대로, Parquet → CSV 변환", (pl.read_parquet(out / "d.parquet").to_dicts(), (out / "dp.csv").read_bytes()),
          ([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}, {"a": 3, "b": "z"}], b"\xef\xbb\xbfa,b\n1,x\n2,y\n3,z\n"))
    api.routes["/d.json"] = lambda e: ranged(json.dumps([{"k": 1, "v": "가"}, {"k": 2, "v": "나"}], ensure_ascii=False).encode(), None, e)
    run(fcfg("/d.json", "dj.csv"))
    api.routes["/broken.bin"] = lambda e: ranged(b"\x00\x01PAR1garbage", None, e)
    _, st = run(fcfg("/broken.bin", "broken.parquet", kind="parquet"))
    check("JSON → CSV, 열 수 없는 파일은 실패로 알림", ((out / "dj.csv").read_bytes().decode("utf-8-sig").splitlines(), st["phase"], st["error"].startswith("받은 파일을 parquet로 열 수 없습니다: ")),
          (["k,v", "1,가", "2,나"], "실패", True))

    print("== 수집: 멈춤·결과 열기·차례")
    check("estimate: page는 최대 쪽 수, cursor·file은 모름", [got("/api/collect/estimate", config=cfg)["requests"], got("/api/collect/estimate", config=config("/cur", "zz.parquet", paging={"kind": "cursor"}))["requests"],
          got("/api/collect/estimate", config=fcfg("/k.csv", "zz.parquet"))["requests"]], [100, None, None])
    api.routes["/slow"] = lambda e: (time.sleep(0.2), items_route(e))[1]
    slow = config("/slow", "slow.parquet", request={"query": [["page", "{page}"]]}, balance={"concurrency": 1})
    check("이미 도는 중이면 시작 거부, 멈춤", (got("/api/collect/start", config=slow)["ok"], error("/api/collect/start", config=cfg), got("/api/collect/stop")),
          (True, "이미 수집이 돌고 있습니다. 끝나거나 멈춘 뒤에 시작하세요.", {"ok": True}))
    st = wait()
    check("멈추면 '멈춤', 진행 기록은 남김", (st["phase"], st["running"], (out / "slow.part.json").exists()), ("멈춤", False, True))
    asked_path = []
    server.ask_save = lambda name, types: (asked_path.append((name, types)), str(out / "picked"))[1]
    check("ask_path: 형식의 대화상자, 확장자를 붙임", (got("/api/collect/ask_path", format="csv", name="items"), asked_path), ({"path": str(out / "picked.csv")}, [("items.csv", ("CSV (*.csv)",))]))
    server.ask_save = None
    check("/api/collect/open: 결과 파일에 연결하고 테이블 이름", (got("/api/collect/open", path=str(out / "items.parquet")), c.get("/api/state").get_json()["opened"]["files"],
          got("/api/query", base="items", limit=1)["count"]), ({"table": "items"}, [str(out / "items.parquet")], 15))
    check("열 결과가 없으면 거부", error("/api/collect/open", path=str(out / "none.parquet")), "열 결과 파일(Parquet·CSV)이 없습니다.")
    check("읽고 있는 파일에는 수집 결과를 쓰지 않음", error("/api/collect/start", config=config("/items", "items.parquet", request={"query": [["page", "{page}"]]})),
          "지금 읽고 있는 파일에는 저장할 수 없습니다. 다른 이름을 고르세요.")
    got("/api/disconnect")
    done = []
    thread = threading.Thread(target=lambda: done.append(c.get("/api/collect/status").status_code))
    server.turn.acquire()  # 다른 데이터 작업이 도는 중인 것처럼
    try:
        thread.start()
        thread.join(5)
        check("수집 상태·한도 라우트는 차례(turn)를 기다리지 않음, open은 기다림", (done, "/api/limits" in server.FREE, "/api/collect/open" in server.FREE), ([200], True, False))
    finally:
        server.turn.release()
    check("/collect 화면 라우트", "/collect" in [rule.rule for rule in app.url_map.iter_rules()], True)
    api.http.shutdown()


class FakeSite:
    """가짜 수집용 창(collect_browser.Page 대신). pages: 쪽 번호(1부터) → 항목 [(글자, 링크)].
    주소의 page로 쪽을 고르고, 다음 버튼은 마지막 쪽 전까지 있고, 스크롤은 step개씩 더 보여 준다.
    scroll: 모든 쪽의 항목을 이어 붙여 step개씩 보여 준다. repeat: 없는 쪽 번호면 마지막 쪽을 보여 준다. moved: 이 쪽을 열면 다른 호스트(로그인 화면)로 넘어간다. gone: 이 수만큼 명령을 받은 뒤 창이 닫힌다."""

    def __init__(self, base, pages, step=3, repeat=False, moved=None, gone=None, scroll=False):
        self.base, self.pages, self.step, self.repeat, self.moved, self.gone, self.scroll = base, pages, step, repeat, moved, gone, scroll
        self.at, self.shown, self.flags, self.landing, self.log, self.url = 1, step, set(), None, [], base + "/list?page=1"

    def go(self, url):
        self.log.append(("go", url))
        self.at = int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("page", ["1"])[0])
        self.url, self.flags, self.shown = url, set(), self.step
        if self.at == self.moved:
            self.url = "http://login.example.com/?back=1"

    def items(self):
        if self.url.startswith("http://login."):
            return []
        if self.scroll:
            return [x for k in sorted(self.pages) for x in self.pages[k]][:self.shown]
        last = max(self.pages) if self.pages else 0
        return self.pages.get(self.at, self.pages.get(last, []) if self.repeat and self.at > last else [])

    def run(self, method, *args, timeout=None):
        from collect import collect_browser
        if self.gone is not None and len(self.log) >= self.gone:
            raise collect_browser.Gone()
        if method == "tag":
            self.flags.add(args[0])
            return True
        if method == "probe":
            found = self.items()
            return {"count": len(found), "sig": f"{found[:1]}{found[-1:]}", "url": self.url, "ready": "complete", "same": bool(args[1]) and args[1] in self.flags}
        if method == "read":
            _, fields, start, limit = args
            found = self.items()[start:None if limit is None else start + limit]
            return {"count": len(self.items()), "url": self.url, "rows": [[text if f["attr"] == "text" else link for f in fields] for text, link in found]}
        if method == "next":
            self.log.append(("next", args[1]))
            if self.at >= max(self.pages):
                return {"found": False, "href": ""}
            href = f"{self.base}/list?page={self.at + 1}"
            if args[1]:
                self.at, self.flags, self.url = self.at + 1, set(), href
            return {"found": True, "href": href}
        if method == "scroll":
            self.log.append(("scroll",))
            self.shown += self.step
            return 1
        raise AssertionError(method)


def web_section(c, got):
    """수집 2단계(웹페이지)의 서버 쪽. 수집용 창은 가짜(FakeSite)로 바꿔 끼우고, robots.txt는 127.0.0.1의 가짜 서버로만 확인한다."""
    from collect import collect_browser
    from collect import collect_limits
    from collect import collect_web
    from contextlib import contextmanager
    print("== 수집: 웹페이지")
    error = lambda api, **body: got(api, **body).get("error", "")
    api = FakeApi()
    out = ROOT / "web_out"
    out.mkdir()
    collect_web.STABLE, collect_web.POLL = 0.05, 0.01
    real = {name: getattr(collect_browser, name) for name in ("session", "page", "open_page", "alive")}
    sessions = []
    site = {"now": None}

    @contextmanager
    def fake_session(show, url):
        sessions.append((show, url))
        site["now"].landing = url  # 방금 이 주소로 띄운 창처럼
        site["now"].go(url)
        site["now"].log.pop()
        yield site["now"]

    collect_browser.session = fake_session
    pages = {k: [(f"상품{k}-{j}", f"{api.base}/item/{k}{j}") for j in (1, 2, 3)] for k in (1, 2, 3)}
    fields = [{"name": "이름", "selector": "a", "attr": "text"}, {"name": "링크", "selector": "a", "attr": "href"}]

    def wcfg(name, kind="url", **more):
        web = {"url": api.base + "/list?page={page}", "items": "ul > li", "fields": fields, "paging": {"kind": kind, "next": "a.next", "start": 1, "max_pages": 50},
               "wait": 1, "robots": False, "show": True}
        for key, value in more.items():
            web[key] = {**web[key], **value} if isinstance(value, dict) else value
        return {"mode": "web", "web": web, "balance": {"interval": 0, "timeout": 5}, "output": {"format": "parquet", "path": str(out / name), "dedupe": []}}

    def wait():
        end = time.time() + 30
        while time.time() < end and c.get("/api/collect/status").get_json()["running"]:
            time.sleep(0.02)
        return c.get("/api/collect/status").get_json()

    def run(cfg, fake):
        site["now"] = fake
        return got("/api/collect/start", config=cfg), wait()

    try:
        fake = FakeSite(api.base, pages)
        started, st = run(wcfg("url.parquet"), fake)
        frame = pl.read_parquet(out / "url.parquet")
        check("url: {page}로 넘기다 0건이면 끝, 항목 순서, 첫 쪽은 띄운 창 그대로(이동 안 함)", (started["resumed"], st["phase"], st["rows"], frame.columns, frame["이름"].to_list()[:4],
              [u for k, u in fake.log if k == "go"], sessions[-1]), (False, "끝", 9, ["이름", "링크"], ["상품1-1", "상품1-2", "상품1-3", "상품2-1"],
              [f"{api.base}/list?page={k}" for k in (2, 3, 4)], (True, f"{api.base}/list?page=1")))
        check("상태: 쪽 수·요청 수(쪽을 연 수)·결과", (st["pages"], st["requests"], st["mode"], st["output"], sorted(p.name for p in out.iterdir())),
              (3, 4, "web", str(out / "url.parquet"), ["url.parquet"]))
        fake = FakeSite(api.base, pages, repeat=True)
        _, st = run(wcfg("repeat.parquet", show=False), fake)
        check("url: 바로 전 쪽과 같은 항목이면 끝(없는 쪽에 마지막 쪽을 보여 주는 사이트), 숨김 실행", (st["phase"], st["rows"], len([1 for k, _ in fake.log if k == "go"]), sessions[-1][0]),
              ("끝", 9, 3, False))
        _, st = run(wcfg("none.parquet", "none"), FakeSite(api.base, pages))
        check("none: 한 쪽만", (st["phase"], st["rows"], st["requests"]), ("끝", 3, 1))
        _, st = run(wcfg("max.parquet", paging={"max_pages": 2}), FakeSite(api.base, pages))
        check("url: 최대 쪽 수", (st["phase"], st["rows"]), ("끝", 6))

        got("/api/limits", name="웹", group={"per_day": 2, "concurrency": 1})
        daily = {**wcfg("daily.parquet"), "limit_group": "웹"}
        _, st = run(daily, FakeSite(api.base, pages))
        check("사용 한도: 쪽 하나를 여는 것이 1번, 다 쓰면 멈추고 이어 받기를 남김", (st["phase"], st["pages"], st["usage"]["day"], (out / "daily.part.json").exists()), ("멈춤", 2, 2, True))
        check("estimate(url): 남은 쪽 수, 이어 받기", got("/api/collect/estimate", config=daily), {"requests": 48, "day_left": 0, "resume": {"pages": 2, "bytes": st["bytes"]}})
        got("/api/limits", name="웹", group={"per_day": 100, "concurrency": 1})
        fake = FakeSite(api.base, pages)
        started, st = run(daily, fake)
        check("url 이어 받기: 받은 쪽은 건너뛰고 3쪽부터(창도 3쪽 주소로 띄움)", (started["resumed"], st["phase"], sessions[-1][1], [u for k, u in fake.log if k == "go"],
              pl.read_parquet(out / "daily.parquet")["이름"].to_list()[-3:], st["rows"]),
              (True, "끝", f"{api.base}/list?page=3", [f"{api.base}/list?page=4"], ["상품3-1", "상품3-2", "상품3-3"], 9))

        fake = FakeSite(api.base, pages)
        _, st = run(wcfg("next.parquet", "next"), fake)
        check("next: 다음 버튼을 눌러 넘기고 버튼이 없으면 끝", (st["phase"], st["rows"], [x for x in fake.log if x == ("next", True)], st["requests"]), ("끝", 9, [("next", True)] * 2, 3))
        got("/api/limits", name="웹", group={"per_day": 2, "concurrency": 1})
        collect_limits.reset("웹")
        nxt = {**wcfg("next2.parquet", "next"), "limit_group": "웹"}
        _, st = run(nxt, FakeSite(api.base, pages))
        check("next: 한도에서 멈춤", (st["phase"], st["pages"]), ("멈춤", 2))
        check("estimate(next): 이어 받아도 처음부터 다시 넘기므로 최대 쪽 수", got("/api/collect/estimate", config=nxt)["requests"], 50)
        got("/api/limits", name="웹", group={"per_day": 100, "concurrency": 1})
        fake = FakeSite(api.base, pages)
        started, st = run(nxt, fake)
        check("next 이어 받기: 처음부터 다시 넘기되 받은 쪽은 다시 저장하지 않음", (started["resumed"], st["phase"], st["message"].startswith("이어 받습니다. 처음 쪽부터"),
              [x for x in fake.log if x == ("next", True)], pl.read_parquet(out / "next2.parquet")["이름"].to_list()), (True, "끝", True, [("next", True)] * 2,
              [t for k in (1, 2, 3) for t, _ in pages[k]]))
        got("/api/limits", name="웹", group=None)

        fake = FakeSite(api.base, {1: [(f"글{j}", f"{api.base}/p/{j}") for j in range(8)]}, scroll=True)
        _, st = run(wcfg("scroll.parquet", "scroll"), fake)
        check("scroll: 내려 새로 생긴 항목만 다음 쪽으로, 더 생기지 않으면 끝", (st["phase"], st["pages"], pl.read_parquet(out / "scroll.parquet")["이름"].to_list()),
              ("끝", 3, [f"글{j}" for j in range(8)]))
        _, st = run(wcfg("empty.parquet"), FakeSite(api.base, {1: []}))
        check("첫 쪽이 0건이면 실패", (st["phase"], st["error"]), ("실패", collect_web.EMPTY))
        _, st = run(wcfg("moved.parquet"), FakeSite(api.base, pages, moved=2))
        check("다른 호스트로 넘어가면 다시 로그인이 필요하다고 실패", (st["phase"], st["error"].startswith("다시 로그인이 필요할 수 있습니다: 페이지가 다른 주소(login.example.com)로")), ("실패", True))
        _, st = run(wcfg("gone.parquet"), FakeSite(api.base, pages, gone=1))
        check("실행 중 창이 닫히면 알림, 받은 것은 남김", (st["phase"], st["error"], (out / "gone.part.json").exists()), ("실패", "수집용 창이 닫혀 멈췄습니다.", True))

        print("== 수집: robots.txt")
        robots = {"status": 200, "text": "User-agent: *\nDisallow: /list\nAllow: /\n"}
        api.routes["/robots.txt"] = lambda e: (robots["status"], {}, robots["text"].encode())
        fake = FakeSite(api.base, pages)
        _, st = run(wcfg("robots.parquet", robots=True), fake)
        check("robots.txt가 막으면 쪽을 열지 않고 실패", (st["phase"], st["error"], fake.log, st["requests"]), ("실패", collect_web.BLOCKED, [], 0))
        verdict = lambda: got("/api/collect/estimate", config=wcfg("r.parquet", robots=True)).get("robots")
        texts = [verdict()]
        robots["status"] = 404
        texts.append(verdict())
        robots["status"] = 503
        texts.append(verdict())
        robots.update(status=200, text="User-agent: other\nDisallow: /\n")
        texts.append(verdict())
        check("estimate의 robots: 금지·4xx는 허용·5xx는 확인 못 함·다른 User-agent 규칙은 상관없음", texts, ["금지: /list", "허용", "확인 못 함: robots.txt 응답 상태가 503입니다", "허용"])
        check("robots.txt를 받는 것은 사용 한도·요청 수에 세지 않음(호스트마다 한 번)", len(api.hits("/robots.txt")), 5)
        dead = FakeApi()
        dead.http.shutdown()
        dead.http.server_close()
        cfg_dead = wcfg("d.parquet", robots=True, url=dead.base + "/list")
        check("연결이 안 되면 확인 못 함", got("/api/collect/estimate", config=cfg_dead)["robots"].startswith("확인 못 함: robots.txt를 받지 못했습니다"), True)
        check("robots를 끄면 estimate에 없음, none은 1쪽", got("/api/collect/estimate", config=wcfg("r.parquet", "none")), {"requests": 1, "day_left": None, "resume": None})

        print("== 수집: 웹페이지 구성 검사·라우트")
        check("구성 검사: kind, fields 모양, 컬럼 이름 겹침, wait 범위, 모르는 칸", [error("/api/collections", name="w", collection=v) for v in (
              {"mode": "web", "web": {"paging": {"kind": "page"}}}, {"web": {"fields": [{"name": "a", "selector": ""}]}},
              {"web": {"fields": [{"name": "A", "selector": "", "attr": "text"}, {"name": "a", "selector": "", "attr": "href"}]}}, {"web": {"wait": 121}},
              {"web": {"paging": {"step": 1}}}, {"web": {"fields": [{"name": "a", "selector": "", "attr": "on click"}]}})],
              ["구성의 web.paging.kind는 none, next, url, scroll 중 하나입니다.", *["구성의 web.fields는 [{\"name\", \"selector\", \"attr\"}, …] 모양이어야 합니다(이름은 비우지 않음, attr은 text 또는 속성 이름, 200개까지)."],
               "컬럼 이름이 겹칩니다(대소문자 무시).", "구성의 web.wait는 1~120의 수여야 합니다.", "구성에 쓸 수 없는 값이 있습니다: web.paging.step",
               "구성의 web.fields는 [{\"name\", \"selector\", \"attr\"}, …] 모양이어야 합니다(이름은 비우지 않음, attr은 text 또는 속성 이름, 200개까지)."])
        check("시작 전 검사: {env}·http 아닌 주소·항목·컬럼·다음 버튼", [error("/api/collect/start", config=v) for v in (
              wcfg("x.parquet", url="https://h.example.com/{env:K}"), wcfg("x.parquet", url="file:///C:/a.html"), wcfg("x.parquet", items=" "),
              wcfg("x.parquet", fields=[]), wcfg("x.parquet", "next", paging={"next": ""}))],
              ["웹페이지 주소에는 {env:…}를 쓸 수 없습니다(로그인은 수집용 창에서 하세요).", "http:// 또는 https:// 주소만 열 수 있습니다.", "항목 선택자를 정하세요.",
               "컬럼을 하나 이상 정하세요.", "다음 버튼 선택자를 정하세요."])
        check("웹페이지 주소의 비밀 같은 쿼리도 저장 거부", error("/api/collections", name="w", collection=wcfg("x.parquet", url="https://h.example.com/l?token=zz")),
              "비밀처럼 보이는 자리에 값이 그대로 들어 있습니다: 웹페이지 주소의 token. env 파일에 넣고 {env:이름}으로 바꾸세요.")
        check("try는 웹페이지 모드에서 쓰지 않음", error("/api/collect/try", config=wcfg("")), "웹페이지 모드는 [브라우저 열기]와 미리보기로 확인하세요.")
        check("창이 없을 때: 상태, 고르기·미리보기 거부, 닫기는 됨", (c.get("/api/collect/browser/state").get_json(), error("/api/collect/browser/pick", target="field"),
              error("/api/collect/web/sample", config=wcfg("")), got("/api/collect/browser/close")),
              ({"open": False, "url": "", "title": "", "picking": "", "pick": None, "busy": False}, collect_browser.CLOSED, collect_browser.CLOSED, {"ok": True}))
        opened = []
        collect_browser.open_page = opened.append
        check("open: {page}는 start(없으면 1)로, http·https·{env} 거부, start는 정수", ([got("/api/collect/browser/open", url=api.base + "/l?p={page}"), got("/api/collect/browser/open", url=api.base + "/l?p={page}", start=3)],
              opened, [error("/api/collect/browser/open", url=u) for u in ("ftp://h.example.com/", "https://h.example.com/{env:A}")], error("/api/collect/browser/open", url=api.base, start="2")),
              ([{"ok": True}] * 2, [api.base + "/l?p=1", api.base + "/l?p=3"], ["http:// 또는 https:// 주소만 열 수 있습니다.", "http:// 또는 https:// 주소만 열 수 있습니다({env:…}는 쓸 수 없습니다)."],
               "start는 정수여야 합니다."))
        check("pick: target 검사", error("/api/collect/browser/pick", target="all"), "target은 field, next, off 중 하나입니다.")
        collect_browser.page = lambda: FakeSite(api.base, {1: [(f"n{j}", f"/x{j}") for j in range(30)]})
        r = got("/api/collect/web/sample", config=wcfg(""))
        check("미리보기: 지금 페이지의 항목 수, 컬럼, 앞 20행", (r["count"], r["columns"], len(r["rows"]), r["rows"][0]), (30, ["이름", "링크"], 20, {"이름": "n0", "링크": "/x0"}))
        collect_browser.page = lambda: type("Bad", (), {"run": lambda self, *a, **k: {"count": 1, "rows": [[1, "x"]], "url": "http://h.example.com/"}})()
        check("페이지가 준 값의 모양이 틀리면 거부", error("/api/collect/web/sample", config=wcfg("")), "페이지에서 읽은 값의 모양이 올바르지 않습니다.")
        collect_browser.page = real["page"]
        collect_browser.child["busy"] = True
        check("작업이 창을 쓰는 중이면 고르기·닫기 거부, 상태의 busy", (error("/api/collect/browser/pick", target="off"), error("/api/collect/browser/close"),
              c.get("/api/collect/browser/state").get_json()["busy"]), (collect_browser.BUSY, collect_browser.BUSY, True))
        collect_browser.child["busy"] = False
        pick = collect_browser.clean_pick({"target": "field", "items": "ul > li", "count": 3, "selector": "a", "text": "가" * 300,
                                           "attrs": {"href": "https://h.example.com/a", "onclick": "x()", "data-id": "7", "src": 5}})
        check("고른 결과 검사: 글자 200자, 쓸 만한 속성만, 모양이 틀리면 버림", (pick["text"], pick["attrs"], [collect_browser.clean_pick(v) for v in (
              {"target": "x"}, {"target": "field", "items": 1, "count": 1, "selector": "", "text": "", "attrs": {}},
              {"target": "next", "items": "", "count": -1, "selector": "", "text": "", "attrs": {}}, {"target": "field", "items": "a" * 2001, "count": 1, "selector": "", "text": "", "attrs": {}})]),
              ("가" * 200, {"href": "https://h.example.com/a", "data-id": "7"}, [None] * 4))
        check("수집용 창 라우트는 차례(turn)를 기다리지 않음", all(p in server.FREE for p in ("/api/collect/browser/open", "/api/collect/browser/close", "/api/collect/browser/state",
              "/api/collect/browser/pick", "/api/collect/browser/forget", "/api/collect/web/sample")), True)

        print("== 수집: 로그인 정보 지우기(forget)")
        folder = collect_browser.FOLDER
        check("수집용 창 폴더는 설정 폴더 아래", folder.parent, settings.PATH.parent)
        outside = ROOT / "browser_outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("x", encoding="utf-8")
        (folder / "EBWebView" / "Default").mkdir(parents=True)
        (folder / "EBWebView" / "Default" / "Cookies").write_text("c", encoding="utf-8")
        inner = subprocess.run(["cmd", "/c", "mklink", "/J", str(folder / "EBWebView" / "link"), str(outside)], capture_output=True).returncode
        collect_browser.alive = lambda: True
        check("창이 떠 있으면 거부", (error("/api/collect/browser/forget"), folder.exists()), ("수집용 창을 닫은 뒤에 지우세요.", True))
        collect_browser.alive = real["alive"]
        check("지우면 폴더만 없어지고, 안의 정션이 가리키는 바깥 내용은 그대로", (inner, got("/api/collect/browser/forget"), folder.exists(), (outside / "keep.txt").exists()),
              (0, {"ok": True}, False, True))
        check("없으면 아무 일도 하지 않음", got("/api/collect/browser/forget"), {"ok": True})
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(folder), str(outside)], capture_output=True).returncode
        check("폴더가 정션이면 거부(가리키는 곳은 그대로)", (made, error("/api/collect/browser/forget").endswith("링크라서 지우지 않습니다."), (outside / "keep.txt").exists()), (0, True, True))
        os.rmdir(folder)
    finally:
        for name, value in real.items():
            setattr(collect_browser, name, value)
        collect_browser.child["busy"] = False
        api.http.shutdown()


def load_section(c, got):
    """적재(수집 3단계 서버): DB 테이블(임시 DuckDB·SQLite 파일)과 Kafka(librdkafka의 시험용 메모리 브로커, 127.0.0.1). 데이터는 여기서 지어낸다."""
    import decimal
    import logging
    import pyarrow as pa
    import pyarrow.parquet as pq
    from confluent_kafka import Consumer, Producer
    from collect import collect_config
    from collect import collect_store
    from collect import load_job
    from collect import load_kafka
    from collect import load_table
    print("== 적재: 준비")
    error = lambda api, **body: got(api, **body).get("error", "")
    out = ROOT / "load_out"
    out.mkdir()
    db = out / "warehouse.duckdb"
    secret = "Pw-Load-7781!"  # 지어낸 비밀번호: 끝에 모든 파일·상태에서 찾아본다.
    load_job.BACKOFF = 0.01
    rows = [{"id": i, "name": f"품목{i}", "amount": i * 1.5, "day": dt.date(2026, 1, 1) + dt.timedelta(days=i)} for i in range(1, 26)]
    pl.DataFrame(rows).write_parquet(out / "items.parquet")
    pl.DataFrame([{"id": i, "name": f"새{i}"} for i in range(1, 11)]).write_parquet(out / "fresh.parquet")

    def lcfg(name, **load):
        table = {"connection": {"kind": "folder", "file": str(db)}, "name": "items", "how": "new", **load.pop("table", {})}
        return {"version": 1, "mode": "api", "request": {"url": "http://127.0.0.1:9/none"}, "output": {"format": name.rsplit(".", 1)[1], "path": str(out / name)},
                "load": {"target": "table", "table": table, "batch": 10, **load}}

    def wait(seconds=60):
        end = time.time() + seconds
        while time.time() < end and c.get("/api/collect/status").get_json()["running"]:
            time.sleep(0.02)
        return c.get("/api/collect/status").get_json()

    def load(cfg, **body):
        started = c.post("/api/load/start", json={"config": cfg, **body}).get_json()
        return started, wait()

    def table_rows(name, file=db):
        con = duckdb.connect(str(file), read_only=True)
        try:
            return con.execute(f'select * from "{name}" order by 1').fetchall()
        finally:
            con.close()

    print("== 적재: 구성 검사")
    check("구성: 비밀번호 칸은 받지 않음, 주소의 비밀번호 거부, 값 범위", [error("/api/collect/estimate", config=v) for v in (
          {"load": {"table": {"connection": {"kind": "url", "url": "postgres://h/db", "password": "x"}}}},
          {"load": {"table": {"connection": {"kind": "url", "url": "postgres://me:pw@h/db"}}}},
          {"load": {"batch": 50}}, {"load": {"target": "s3"}}, {"load": {"table": {"key": "id"}}})],
          ["비밀번호 같은 비밀 값은 구성에 넣지 않습니다(저장하지 않고 시작할 때 따로 입력합니다).",
           "DB 주소에 비밀번호를 넣지 마세요. 비밀번호는 저장하지 않고 시작할 때 따로 입력합니다.",
           "구성의 load.batch는 100~1000000의 정수여야 합니다.", "구성의 load.target는 file, table, kafka 중 하나입니다.",
           "구성의 load.table.key는 컬럼 이름 목록이어야 합니다."])
    check("기본 구성은 적재 없음(target file)", collect_config.normalize({})["load"]["target"], "file")
    limits = collect_config.NUMBERS[("load", "batch")]
    collect_config.NUMBERS[("load", "batch")] = (int, 1, limits[2])  # 작은 묶음(10행)으로 시험한다.
    try:
        print("== 적재: DB 테이블 new·append·replace")
        check("시작 전 거부: 이름, 파일, key는 append에서만", [error("/api/load/start", config=v) for v in (
              lcfg("items.parquet", table={"name": "bad name"}), lcfg("items.parquet", table={"connection": {"kind": "folder", "file": str(out / "x.txt")}}),
              lcfg("items.parquet", table={"key": ["id"]}), lcfg("none.parquet"), {**lcfg("items.parquet"), "load": {"target": "file"}})],
              ["테이블 이름은 영문·숫자·밑줄·한글만 쓸 수 있습니다(공백·따옴표·점 없이).", "적재할 DB 파일(.duckdb)을 고르세요.",
               "이미 있는 키를 건너뛰는 것(key)은 덧붙이기(append)에서만 씁니다.", "적재할 결과 파일이 없습니다. 먼저 수집하세요.", "적재할 곳(DB 테이블 또는 Kafka)을 고르세요."])
        r = got("/api/load/table/check", config=lcfg("items.parquet"), password=secret)
        check("확인: 없는 테이블은 새로 만듦, 결과 파일의 컬럼", r, {"exists": False, "columns": [["id", "정수"], ["name", "문자"], ["amount", "실수"], ["day", "날짜"]],
              "table_columns": None, "ours": False, "ok": True, "message": "새 테이블로 만듭니다."})
        started, st = load(lcfg("items.parquet"), secrets={"password": secret})
        check("new: 묶음 10행씩 3묶음, 상태의 load 칸, 기록 파일은 다 넣으면 지움", (started, st["phase"], st["load"], (out / "items.load.json").exists()),
              ({"ok": True}, "끝", {"target": "table", "rows": 25, "total": 25, "batches": 3, "skipped": 0, "failures": 0, "where": "warehouse.duckdb · items"}, False))
        check("new: 넣은 값(날짜 타입 그대로)", table_rows("items"), [(r["id"], r["name"], r["amount"], r["day"]) for r in rows])
        made = json.loads((settings.PATH.parent / load_table.FILE).read_text(encoding="utf-8"))["tables"]
        check("만든 테이블 기록(load_tables.json): 연결·스키마·이름·컬럼", [(t["connection"], t["schema"], t["name"], t["columns"]) for t in made],
              [(os.path.normcase(str(db)), "", "items", [["id", "정수"], ["name", "문자"], ["amount", "실수"], ["day", "날짜"]])])
        _, st = load(lcfg("items.parquet"))
        check("new: 이미 있으면 실패로 알림", (st["phase"], st["error"]), ("실패", "'items'은 이미 있는 테이블입니다. 다른 이름을 쓰거나 덧붙이기·바꾸기를 고르세요."))
        r = got("/api/load/table/check", config=lcfg("items.parquet", table={"how": "append", "key": ["id"]}))
        check("확인: 같은 컬럼이면 덧붙일 수 있음, 이 프로그램이 만든 것", (r["ok"], r["message"], r["ours"], r["table_columns"] == r["columns"]), (True, "컬럼이 같아 덧붙일 수 있습니다.", True, True))
        _, st = load(lcfg("items.parquet", table={"how": "append", "key": ["id"]}))
        check("append + key: 이미 있는 키는 모두 건너뜀", (st["phase"], st["load"]["skipped"], len(table_rows("items"))), ("끝", 25, 25))
        _, st = load(lcfg("items.parquet", table={"how": "append"}, unit="single", batch=100))
        check("append 한 건씩: 25행 더 들어감", (st["phase"], st["load"]["rows"], st["load"]["batches"], len(table_rows("items"))), ("끝", 25, 1, 50))
        con = duckdb.connect(str(db))
        con.execute("create table other (id integer, label varchar)")
        con.execute("insert into other values (1, 'x')")
        con.close()
        r = got("/api/load/table/check", config=lcfg("items.parquet", table={"name": "other", "how": "append"}))
        check("확인: 컬럼이 다르면 다른 점", (r["ok"], r["message"], r["ours"]),
              (False, "컬럼이 달라 덧붙일 수 없습니다. 다른 점: 결과 파일에만 있는 컬럼 name, amount, day / 테이블에만 있는 컬럼 label", False))
        _, st = load(lcfg("items.parquet", table={"name": "other", "how": "append"}))
        check("append: 컬럼이 다르면 넣지 않음", (st["phase"], st["error"].startswith("컬럼이 달라 덧붙일 수 없습니다."), table_rows("other")), ("실패", True, [(1, "x")]))
        r = got("/api/load/table/check", config=lcfg("fresh.parquet", table={"name": "other", "how": "replace"}))
        _, st = load(lcfg("fresh.parquet", table={"name": "other", "how": "replace"}))
        check("replace: 이 프로그램이 만든 테이블이 아니면 거부(확인·시작 모두), 내용 그대로", (r["ok"], r["message"], st["phase"], st["error"], table_rows("other")),
              (False, "이 프로그램이 만든 테이블이 아니라 바꿀 수 없습니다.", "실패", "이 프로그램이 만든 테이블이 아니라 바꿀 수 없습니다.", [(1, "x")]))
        _, st = load(lcfg("fresh.parquet", table={"how": "replace"}))
        con = duckdb.connect(str(db), read_only=True)
        names = sorted(t for (t,) in con.execute("show tables").fetchall())
        con.close()
        made = {t["name"]: t["columns"] for t in json.loads((settings.PATH.parent / load_table.FILE).read_text(encoding="utf-8"))["tables"]}
        check("replace: 우리가 만든 테이블은 새 내용으로 바뀌고 임시 테이블은 남지 않음, 기록의 컬럼도 바뀜", (st["phase"] + st["error"], table_rows("items"), names, made),
              ("끝", [(i, f"새{i}") for i in range(1, 11)], ["items", "other"], {"items": [["id", "정수"], ["name", "문자"]]}))
        con = duckdb.connect(str(db))
        con.execute("alter table items add column extra integer")
        con.close()
        _, st = load(lcfg("fresh.parquet", table={"how": "replace"}))
        check("replace: 만든 뒤 컬럼이 바뀐 테이블은 바꾸지 않음", (st["phase"], st["error"]), ("실패", "이 프로그램이 만든 뒤 테이블의 컬럼이 바뀌어 바꾸지 않습니다."))

        print("== 적재: 실패한 묶음 다시 시도·이어 넣기·멈춤")
        real_insert, calls = load_table.insert, {"n": 0, "fail": {3}}

        def flaky(con, schema, name, data):
            calls["n"] += 1
            if calls["n"] in calls["fail"]:
                raise RuntimeError("가짜 실패")
            return real_insert(con, schema, name, data)

        load_table.insert = flaky
        try:
            calls.update(n=0, fail={2})
            _, st = load(lcfg("items.parquet", table={"name": "retried"}, retries=2))
            check("묶음이 한 번 실패하면 다시 넣어 성공(행이 겹치지 않음)", (st["phase"], calls["n"], [r[0] for r in table_rows("retried")]), ("끝", 4, list(range(1, 26))))
            calls.update(n=0, fail={3, 4})
            _, st = load(lcfg("items.parquet", table={"name": "resumed"}, retries=1))
            record = json.loads((out / "items.load.json").read_text(encoding="utf-8"))
            check("다시 시도도 실패하면 멈추고 넣은 묶음까지 기록(결과 파일은 남김)", (st["phase"], st["error"], record["rows"], record["batches"], len(table_rows("resumed")),
                  (out / "items.parquet").exists()), ("실패", "가짜 실패", 20, 2, 20, True))
        finally:
            load_table.insert = real_insert
        started, st = load(lcfg("items.parquet", table={"name": "resumed"}, retries=1))
        check("같은 설정으로 다시 하면 넣은 묶음 다음부터(겹치지 않음)", (st["phase"], st["message"], [r[0] for r in table_rows("resumed")], (out / "items.load.json").exists()),
              ("끝", "이어 넣습니다.", list(range(1, 26)), False))
        c.post("/api/load/start", json={"config": lcfg("items.parquet", table={"name": "paused"}, interval=0.3)})
        time.sleep(0.4)
        got("/api/collect/stop")
        st = wait()
        record = json.loads((out / "items.load.json").read_text(encoding="utf-8"))
        check("멈추기는 묶음 사이에서 멈추고 기록을 남김", (st["phase"], 0 < record["rows"] < 25, record["rows"] % 10, len(table_rows("paused")) == record["rows"]), ("멈춤", True, 0, True))
        _, st = load(lcfg("items.parquet", table={"name": "paused", "schema": "main"}))
        check("스키마 등 서명이 다르면 처음부터(new는 이미 있어 거부, 묶음 크기는 서명에 없음)", (st["phase"], st["error"][:23]), ("실패", "'paused'은 이미 있는 테이블입니다."))
        _, st = load(lcfg("items.parquet", table={"name": "paused"}))
        check("같은 서명이면(간격은 서명에 없음) 이어서 끝냄", (st["phase"], [r[0] for r in table_rows("paused")]), ("끝", list(range(1, 26))))

        print("== 적재: 쿼리 화면의 연결·CSV·SQLite·수집에 이어 적재")
        got("/api/connect", kind="folder", path=str(out), file=str(db))
        check("쿼리·분석 화면이 같은 DB 파일을 열고 있으면 거부(확인·시작)", (error("/api/load/table/check", config=lcfg("items.parquet")), error("/api/load/start", config=lcfg("items.parquet"))),
              (load_table.OPENED_HERE,) * 2)
        got("/api/disconnect")
        pl.DataFrame(rows).write_csv(out / "plain.csv")
        collect_store.with_bom(out / "plain.csv", out / "items.csv")
        _, st = load(lcfg("items.csv", table={"name": "from_csv"}))
        check("CSV 결과 파일(BOM)도 묶음으로 읽어 넣음(전체 수는 모름)", (st["phase"], st["load"]["total"], table_rows("from_csv")[0], len(table_rows("from_csv"))),
              ("끝", 25, (1, "품목1", 1.5, dt.date(2026, 1, 2)), 25))
        lite = out / "lite.db"
        con = sqlite3.connect(lite)
        con.execute("create table items (id integer, name text)")
        con.commit()
        con.close()
        lite_cfg = lcfg("fresh.parquet", table={"connection": {"kind": "url", "url": f"sqlite://{lite}"}, "how": "append"})
        check("SQLite 주소: 확인", got("/api/load/table/check", config=lite_cfg)["message"], "컬럼이 같아 덧붙일 수 있습니다.")
        _, st = load(lite_cfg)
        con = sqlite3.connect(lite)
        check("SQLite 주소: append(연결 스레드에서 열고 넣고 닫음)", (st["phase"], st["load"]["where"].endswith("lite.db · items"), con.execute("select count(*), max(name) from items").fetchone()),
              ("끝", True, (10, "새9")))
        con.close()
        bad_url = lcfg("items.parquet", table={"connection": {"kind": "url", "url": "nosuch://h/db", "user": "loader"}})
        e = error("/api/load/table/check", config=bad_url, password=secret)
        _, st = load(bad_url, secrets={"password": secret})
        check("주소 연결이 실패해도 비밀번호는 오류 문구에 없음", (bool(e), secret in e, st["phase"], secret in json.dumps(st, ensure_ascii=False)), (True, False, "실패", False))

        api = FakeApi()
        api.routes["/rows"] = lambda e: (200, {}, json.dumps([{"id": i, "name": f"받음{i}"} for i in range(1, 8)], ensure_ascii=False).encode())
        cfg = {"version": 1, "mode": "api", "request": {"url": api.base + "/rows"}, "response": {"records": ""}, "balance": {"interval": 0},
               "output": {"format": "parquet", "path": str(out / "got.parquet")}, "load": {"target": "table", "table": {"connection": {"kind": "folder", "file": str(db)}, "name": "got"}}}
        seen = []
        real_run = load_job.run
        load_job.run = lambda *a: (seen.append(c.get("/api/collect/status").get_json()["phase"]), real_run(*a))[1]
        try:
            started = got("/api/collect/start", config=cfg, secrets={"password": secret})
            st = wait()
        finally:
            load_job.run = real_run
        check("수집이 결과 파일을 만든 뒤 바로 적재(적재 중 → 끝)", (started, seen, st["phase"], st["output"], st["load"]["rows"], len(table_rows("got"))),
              ({"ok": True, "resumed": False}, ["적재 중"], "끝", str(out / "got.parquet"), 7, 7))
        got("/api/collect/start", config=cfg)
        st = wait()
        check("수집 뒤 적재가 실패해도 결과 파일은 남음", (st["phase"], st["error"][:20], (out / "got.parquet").exists()), ("실패", "'got'은 이미 있는 테이블입니다.", True))
        api.http.shutdown()
        single = lcfg("items.parquet", unit="single")
        check("estimate: 한 건씩이면 load_note(결과 파일이 있으면 예상 시간)", (got("/api/collect/estimate", config=single)["load_note"],
              got("/api/collect/estimate", config=lcfg("none.parquet", unit="single"))["load_note"], "load_note" in got("/api/collect/estimate", config=lcfg("items.parquet"))),
              ("한 건씩 넣으면 오래 걸릴 수 있습니다(예상 약 1분).", "한 건씩 넣으면 오래 걸릴 수 있습니다.", False))

        print("== 적재: Kafka(시험용 메모리 브로커)")
        logs = []
        catch = logging.getLogger("tests.kafka_mock")
        catch.addHandler(type("Keep", (logging.Handler,), {"emit": lambda self, r: logs.append(r.getMessage())})())
        catch.propagate = False
        catch.setLevel(logging.INFO)
        holder = Producer({"bootstrap.servers": "127.0.0.1:1", "test.mock.num.brokers": 1, "logger": catch})  # 이것이 살아 있는 동안 브로커가 떠 있다(테스트에서만)
        holder.poll(0.5)
        broker = next(m[1] for m in (re.search(r"(127\.0\.0\.1:\d+)", line) for line in logs) if m)
        check("Kafka 등록 검사: 비밀번호 칸 거부, 서버 모양, 토픽 이름, SASL 아이디", [error("/api/load/kafka", name="x", item=v) for v in (
              {"servers": broker, "topic": "t", "password": "x"}, {"servers": "nohost", "topic": "t"}, {"servers": broker, "topic": "a b"},
              {"servers": broker, "topic": "t", "security": "SASL_SSL"})],
              ["비밀번호는 저장하지 않습니다. 확인·시작할 때 따로 입력하세요.", "서버는 host:port 모양으로, 여럿이면 쉼표로 이어 쓰세요(예: host1:9092,host2:9092).",
               "토픽 이름은 영문·숫자·점·밑줄·빼기로 1~249자입니다.", "SASL을 쓰면 아이디를 입력하세요."])
        r = got("/api/load/kafka", name="로컬", item={"servers": broker, "topic": "items"})
        check("Kafka 등록 저장(빠진 칸 채움)·목록", (r, c.get("/api/load/kafka").get_json()), ({"kafka": {"로컬": {**load_kafka.DEFAULT, "servers": broker, "topic": "items"}}},) * 2)
        for i in range(load_kafka.MAX_DESTINATIONS - 1):
            got("/api/load/kafka", name=f"k{i}", item={"servers": broker, "topic": "t"})
        check("Kafka 등록은 30개까지", error("/api/load/kafka", name="넘침", item={"servers": broker, "topic": "t"}), "저장한 적재할 곳은 30개까지입니다. 쓰지 않는 것을 지우세요.")
        for i in range(load_kafka.MAX_DESTINATIONS - 1):
            got("/api/load/kafka", name=f"k{i}", item=None)
        check("Kafka 접속 확인: 브로커 수, 토픽은 아직 없음(메시지를 보내지 않음)", got("/api/load/kafka/check", name="로컬", password=""), {"ok": True, "topic": False, "brokers": 1})

        def consume(topic, count, seconds=20):
            reader = Consumer({"bootstrap.servers": broker, "group.id": f"g-{topic}-{time.time()}", "auto.offset.reset": "earliest", "logger": catch})
            reader.subscribe([topic])
            found, end = [], time.time() + seconds
            while time.time() < end and len(found) < count:
                m = reader.poll(0.2)
                if m is not None and not m.error():
                    found.append((m.key(), m.value()))
            reader.close()
            return found

        rich = pa.table({"id": pa.array([1, 2, 3], pa.int64()), "name": ["가", None, "다"], "day": pa.array([dt.date(2026, 1, 2)] * 3),
                         "at": pa.array([dt.datetime(2026, 1, 2, 3, 4, 5)] * 3, pa.timestamp("us")), "price": pa.array([decimal.Decimal("1.20")] * 3, pa.decimal128(10, 2)),
                         "raw": pa.array([b"\x00\xff"] * 3), "score": [1.5, float("nan"), None]})
        pq.write_table(rich, out / "rich.parquet")
        kcfg = lambda name, topic_key="", **load: {"version": 1, "mode": "api", "request": {"url": "http://127.0.0.1:9/none"},
                                                  "output": {"format": name.rsplit(".", 1)[1], "path": str(out / name)},
                                                  "load": {"target": "kafka", "kafka": {"destination": "로컬", "key": topic_key}, "batch": 2, **load}}
        _, st = load(kcfg("rich.parquet", "id"), secrets={"password": secret})
        sent = consume("items", 3)
        check("Kafka 묶음: 3행 = 메시지 3개, 상태", (st["phase"], st["load"], len(sent)),
              ("끝", {"target": "kafka", "rows": 3, "total": 3, "batches": 2, "skipped": 0, "failures": 0, "where": "로컬 · items"}, 3))
        check("메시지: JSON(한글 그대로, 날짜·시간 ISO, Decimal 글자, bytes base64, NaN null), 키는 컬럼 값 글자", sorted(sent)[0],
              (b"1", json.dumps({"id": 1, "name": "가", "day": "2026-01-02", "at": "2026-01-02T03:04:05", "price": "1.20", "raw": "AP8=", "score": 1.5}, ensure_ascii=False).encode()))
        check("NaN과 빈 값은 null", [json.loads(v)["score"] for k, v in sorted(sent)], [1.5, None, None])
        got("/api/load/kafka", name="로컬", item={"servers": broker, "topic": "single"})
        _, st = load(kcfg("items.parquet", unit="single"))
        sent = consume("single", 25)
        check("Kafka 한 건씩: 25개, 키 없음", (st["phase"], st["load"]["rows"], len(sent), {k for k, _ in sent}, sorted(json.loads(v)["id"] for _, v in sent)),
              ("끝", 25, 25, {None}, list(range(1, 26))))
        got("/api/load/kafka", name="로컬", item={"servers": broker, "topic": "big"})
        pl.DataFrame({"id": [1, 2, 3], "text": ["a", "x" * 2_000_000, "c"]}).write_parquet(out / "big.parquet")
        _, st = load(kcfg("big.parquet", batch=100))
        check("너무 큰 행은 실패로 세고 계속", (st["phase"], st["load"]["failures"], sorted(json.loads(v)["id"] for _, v in consume("big", 2))), ("끝", 1, [1, 3]))
        pl.DataFrame({"id": [1, 2], "text": ["x" * 2_000_000] * 2}).write_parquet(out / "huge.parquet")
        _, st = load(kcfg("huge.parquet", batch=100))
        check("묶음이 모두 실패하면 멈춤", (st["phase"], st["error"].startswith("1번째 묶음을 하나도 넣지 못해 멈췄습니다."), st["load"]["failures"]), ("실패", True, 2))
        _, st = load(kcfg("items.parquet", "nope"))
        check("메시지 키 컬럼이 결과 파일에 없으면 거부", (st["phase"], st["error"]), ("실패", "메시지 키 컬럼 'nope'이 결과 파일에 없습니다."))
        got("/api/load/kafka", name="sasl", item={"servers": "127.0.0.1:1", "topic": "t", "security": "SASL_PLAINTEXT", "mechanism": "SCRAM-SHA-256", "user": "u"})
        load_kafka.CHECK_TIMEOUT = 2
        e = error("/api/load/kafka/check", name="sasl", password=secret)
        check("Kafka 접속 실패: 오류 문구에 비밀번호 없음", (e.startswith("Kafka에 접속하지 못했습니다:"), secret in e), (True, False))
        timeouts = load_kafka.SEND_TIMEOUT, load_kafka.FLUSH_MARGIN
        load_kafka.SEND_TIMEOUT, load_kafka.FLUSH_MARGIN = 1, 1
        try:
            _, st = load({**kcfg("items.parquet", batch=100), "load": {**kcfg("items.parquet")["load"], "kafka": {"destination": "sasl", "key": ""}, "batch": 100, "retries": 1}},
                         secrets={"password": secret})
        finally:
            load_kafka.SEND_TIMEOUT, load_kafka.FLUSH_MARGIN = timeouts
        check("Kafka 전달이 모두 실패하면 다시 시도한 뒤 멈춤, 넣은 것 없음, 오류에 비밀번호 없음",
              (st["phase"], st["error"].startswith("Kafka에 전달하지 못했습니다:"), secret in json.dumps(st, ensure_ascii=False), st["load"]["rows"]), ("실패", True, False, 0))
        del holder
    finally:
        collect_config.NUMBERS[("load", "batch")] = limits

    print("== 적재: 비밀번호가 남지 않음")
    status_text = c.get("/api/collect/status").get_data(as_text=True)
    found = [str(p) for folder in (ROOT / "appdata", out) for p in folder.rglob("*") if p.is_file() and secret.encode() in p.read_bytes()]
    check("비밀번호는 설정 폴더(kafka.json·load_tables.json 등)·결과 폴더(.load.json 등)·상태 어디에도 없음",
          (found, secret in status_text, sorted(p.name for p in (ROOT / "appdata" / "DataLoupe").glob("*.json") if p.name in ("kafka.json", "load_tables.json"))),
          ([], False, ["kafka.json", "load_tables.json"]))
    check("작업이 끝나면 가릴 값(비밀번호)도 버림", collect_store.current["hide"], [])
    check("적재 라우트는 차례(turn)를 기다리지 않음", all(p in server.FREE for p in ("/api/load/kafka", "/api/load/kafka/check", "/api/load/table/check", "/api/load/start")), True)


class FakeTasks:
    """schtasks 대신 부르는 가짜. 받은 명령을 모두 적고, 등록한 작업을 {경로: (명령줄, XML)}로 들고 있는다."""

    def __init__(self):
        self.calls, self.tasks = [], {}

    def __call__(self, args):
        self.calls.append(list(args))
        verb, name = args[1], args[args.index("/TN") + 1]
        if verb == "/Create":
            text = Path(args[args.index("/XML") + 1]).read_bytes().decode("utf-16")
            found = lambda tag: re.search(f"<{tag}>(.*?)</{tag}>", text, re.S)[1]
            plain = lambda value: value.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
            self.tasks[name] = (f"{plain(found('Command'))} {plain(found('Arguments'))}", text)
            return 0, "성공: 예약 작업을 만들었습니다.", ""
        if verb == "/Delete":
            return (0, "성공", "") if self.tasks.pop(name, None) else (1, "", "오류: 지정된 파일을 찾을 수 없습니다.")
        head = ",".join(f'"h{i}"' for i in range(28))
        row = lambda task: ",".join(f'"{v}"' for v in ["PC", task, "2026-10-16 오전 9:00:00", "준비", "대화형만", "2026-10-15 오후 1:05:00", "0", "me", self.tasks[task][0].replace('"', '""')] + ["N/A"] * 19)
        if name.endswith("\\"):  # 폴더 조회: 폴더 안의 작업 전부(폴더가 비었거나 없으면 오류)
            mine = [t for t in self.tasks if t.lower().startswith(name.lower())]
            return (0, head + "\n" + "\n".join(row(t) for t in mine) + "\n", "") if mine else (1, "", "오류: 지정된 파일을 찾을 수 없습니다.")
        if name not in self.tasks:
            return 1, "", "오류: 지정된 파일을 찾을 수 없습니다."
        return 0, head + "\n" + row(name) + "\n", ""


def schedule_section(c, got):
    """수집 예약(4단계 서버). Windows 작업은 schtasks 대신 가짜(FakeTasks)로, 수집은 127.0.0.1 가짜 API로만 확인한다. 데이터는 여기서 지어낸다."""
    from collect import collect_job
    from collect import load_kafka
    from integrations import schedule
    from integrations import wintask
    print("== 예약: 다음 시각")
    error = lambda api, **body: got(api, **body).get("error", "")
    at = dt.datetime
    daily, weekly, every = {"every": "daily", "time": "09:00"}, {"every": "weekly", "time": "09:00", "days": [0, 6]}, {"every": "interval", "minutes": 45}
    check("매일: 지금과 같은 시각은 다음 날, 1초 전이면 오늘", (schedule.next_time(daily, at(2026, 10, 15, 9, 0)), schedule.next_time(daily, at(2026, 10, 15, 8, 59, 59))),
          (at(2026, 10, 16, 9, 0), at(2026, 10, 15, 9, 0)))
    check("매주(월·일): 일요일 9시 정각이면 다음 월요일, 9시 전이면 그날, 같은 요일 하나면 다음 주", (schedule.next_time(weekly, at(2026, 10, 18, 9, 0)),
          schedule.next_time(weekly, at(2026, 10, 18, 8, 0)), schedule.next_time({**weekly, "days": [3]}, at(2026, 10, 15, 9, 0)),
          schedule.next_time({**weekly, "days": [3]}, at(2026, 10, 15, 8, 0))),
          (at(2026, 10, 19, 9, 0), at(2026, 10, 18, 9, 0), at(2026, 10, 22, 9, 0), at(2026, 10, 15, 9, 0)))
    check("간격(45분): 기준 시각에서 45분 단위, 같은 시각이면 다음 칸, 날을 넘김", (schedule.next_time(every, at(2026, 10, 15, 9, 0)), schedule.next_time(every, at(2026, 10, 15, 9, 45)),
          schedule.next_time(every, at(2026, 10, 15, 23, 59))), (at(2026, 10, 15, 9, 45), at(2026, 10, 15, 10, 30), at(2026, 10, 16, 0, 0)))

    print("== 예약: 검사·저장(가짜 schtasks)")
    fake = FakeTasks()
    real_execute, real_clock, real_post = wintask.execute, schedule.clock, transport.post
    wintask.execute = fake
    out = ROOT / "schedule_out"
    out.mkdir()
    api = FakeApi()
    slow = threading.Event()

    def items(e):
        if slow.is_set():
            time.sleep(1.5)
        return 200, {"Content-Type": "application/json"}, json.dumps({"items": [{"id": i, "name": f"예약{i}"} for i in range(1, 8)]}).encode()

    api.routes["/sched"] = items

    def cfg(name, **more):
        return {"version": 1, "mode": "api", "request": {"method": "GET", "url": api.base + "/sched"}, "response": {"records": "items", "columns": []},
                "balance": {"concurrency": 1, "interval": 0, "retries": 1, "timeout": 10}, "output": {"format": "parquet", "path": str(out / name), "dedupe": []}, **more}

    load_kafka.save("sasl", {"servers": "127.0.0.1:9", "topic": "t", "security": "SASL_SSL", "user": "u"})
    for name, item in (("예약 하나", cfg("one.parquet")), ("예약/둘?", cfg("two.parquet")),
                       ("비번 DB", cfg("db.parquet", load={"target": "table", "table": {"connection": {"kind": "url", "url": "postgres://h/db", "user": "me"}, "name": "t"}})),
                       ("비번 Kafka", cfg("k.parquet", load={"target": "kafka", "kafka": {"destination": "sasl"}}))):
        got("/api/collections", name=name, collection=item)
    put = lambda name, item: got("/api/schedules", name=name, schedule=item)
    check("예약 거부: 형식, 없는 구성, 비밀번호가 필요한 구성(DB 주소+아이디, Kafka SASL)", [error("/api/schedules", name=n, schedule=s) for n, s in (
          ("예약 하나", {"every": "hourly"}), ("예약 하나", {"time": "24:00"}), ("예약 하나", {"every": "weekly", "days": []}), ("예약 하나", {"every": "interval", "minutes": 4}),
          ("예약 하나", {"days": [7]}), ("예약 하나", {"inapp": 1}), ("예약 하나", {"notify": "없는 곳"}), ("예약 하나", {"extra": 1}),
          ("없는 구성", daily), ("비번 DB", daily), ("비번 Kafka", daily))],
          ["예약의 every는 daily, weekly, interval 중 하나입니다.", "시각은 HH:MM(00:00~23:59)으로 쓰세요.", "매주 예약은 요일을 하나 이상 고르세요.",
           "간격(minutes)은 5~1440분의 정수여야 합니다.", "요일(days)은 0(월)~6(일)의 겹치지 않는 목록이어야 합니다.", "예약의 inapp는 true·false여야 합니다.",
           "보낼 곳 '없는 곳'이 없습니다.", "예약에 쓸 수 없는 값이 있습니다: extra", "구성 '없는 구성'이 없습니다. 구성을 먼저 저장하세요.",
           "DB 테이블에 적재할 때 비밀번호가 필요한 연결(아이디가 있는 주소)이라 예약할 수 없습니다. 비밀번호는 저장하지 않기 때문입니다.",
           "Kafka에 적재할 때 비밀번호가 필요한 곳(SASL)이라 예약할 수 없습니다. 비밀번호는 저장하지 않기 때문입니다."])
    check("거부한 것은 저장하지 않고 schtasks도 부르지 않음", (c.get("/api/schedules").get_json()["schedules"], fake.calls), ({}, []))
    schedule.clock = lambda: at(2026, 10, 15, 8, 30)
    r = put("예약 하나", {**weekly, "inapp": False, "windows": True})
    xml = fake.tasks["\\DataLoupe\\예약 하나"][1]
    check("Windows 작업 등록: \\DataLoupe\\ 폴더, /XML 파일, /F", [a[:3] + a[4:5] + a[6:] for a in fake.calls if a[1] != "/Query"], [["schtasks", "/Create", "/TN", "/XML", "/F"]])
    check("작업 XML: InteractiveToken·LeastPrivilege·StartWhenAvailable·IgnoreNew·12시간·전원 조건 없음·매주 월·일·첫 시각",
          [f for f in ("<LogonType>InteractiveToken</LogonType>", "<RunLevel>LeastPrivilege</RunLevel>", "<StartWhenAvailable>true</StartWhenAvailable>",
                       "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>", "<ExecutionTimeLimit>PT12H</ExecutionTimeLimit>",
                       "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>", "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>",
                       "<DaysOfWeek><Sunday/><Monday/></DaysOfWeek>", "<StartBoundary>2026-10-18T09:00:00</StartBoundary>", "<URI>\\DataLoupe\\예약 하나</URI>") if f not in xml], [])
    check("작업 동작: 지금 python.exe + desktop.py 절대 경로 + --run \"구성 이름\", 작업 폴더는 프로그램 폴더",
          (fake.tasks["\\DataLoupe\\예약 하나"][0], re.search("<WorkingDirectory>(.*)</WorkingDirectory>", xml)[1]),
          (subprocess.list2cmdline([sys.executable]) + " " + subprocess.list2cmdline([str(Path("desktop.py").resolve()), "--run", "예약 하나"]), str(Path("desktop.py").resolve().parent)))
    check("GET: 등록됨·경로 맞음·다음·마지막(성공), 프로그램 안 예약은 꺼짐", (r["schedules"]["예약 하나"], r["tasks"], r["next_inapp"], r["stale"]),
          ({**weekly, "minutes": 60, "inapp": False, "windows": True, "notify": "", "notify_on": "fail"},
           {"예약 하나": {"registered": True, "path_ok": True, "next": "2026-10-16 09:00", "last": "2026-10-15 13:05 · 성공"}}, {"예약 하나": None}, []))
    put("예약/둘?", {**every, "inapp": False, "windows": True})
    xml = fake.tasks["\\DataLoupe\\예약_둘_"][1]
    check("간격 작업: 다음 칸부터 PT45M 반복(기간 없음), 작업 이름은 안전한 글자로", ("<Interval>PT45M</Interval>" in xml, "<Duration>" in xml,
          "<StartBoundary>2026-10-15T09:00:00</StartBoundary>" in xml, sorted(fake.tasks)), (True, False, True, ["\\DataLoupe\\예약 하나", "\\DataLoupe\\예약_둘_"]))
    got("/api/collections", name="예약_둘_", collection=cfg("three.parquet"))
    put("예약_둘_", {**daily, "inapp": False, "windows": True})
    check("이름이 겹치면 _2를 붙이고 schedules.json에 대응을 적음", {n: s.get("task") for n, s in schedule.schedules().items()},
          {"예약 하나": "예약 하나", "예약/둘?": "예약_둘_", "예약_둘_": "예약_둘__2"})
    check("작업 이름은 폴더 밖·아래를 가리킬 수 없음", [raised(lambda t=t: wintask.full(t)) for t in ("..\\x", "a\\b", "", " a", "x" * 61)], ["ValueError"] * 5)
    fake.tasks["\\DataLoupe\\예약 하나"] = ("C:\\old\\DataLoupe.exe --run \"예약 하나\"", "")
    r = c.get("/api/schedules").get_json()
    check("경로가 다른 작업은 stale", (r["stale"], r["tasks"]["예약 하나"]["path_ok"]), (["예약 하나"], False))
    r = got("/api/schedules/reregister")
    check("다시 등록하면 지금 경로로", (r["stale"], fake.tasks["\\DataLoupe\\예약 하나"][0].startswith(subprocess.list2cmdline([sys.executable]))), ([], True))
    put("예약_둘_", {**daily, "windows": False})
    check("windows를 끄면 작업을 지움", ("\\DataLoupe\\예약_둘__2" in fake.tasks, "task" in schedule.schedules()["예약_둘_"]), (False, False))
    put("예약/둘?", None)
    put("예약_둘_", None)
    check("예약을 지우면 작업도 지움", (sorted(fake.tasks), sorted(schedule.schedules())), (["\\DataLoupe\\예약 하나"], ["예약 하나"]))
    check("schtasks에 넘긴 작업은 모두 \\DataLoupe\\ 바로 안", all(a[a.index("/TN") + 1].startswith("\\DataLoupe\\") and a[a.index("/TN") + 1].count("\\") == 2 for a in fake.calls), True)
    fake.tasks["\\ibis_search\\예약 하나"] = ("C:\\old\\ibis_search.exe --run \"예약 하나\"", "")
    fake.tasks["\\ibis_search\\남의 작업"] = ("other.exe", "")  # 예약 목록에 없는 작업은 예전 폴더 안이어도 건드리지 않는다
    fake.tasks["\\Other\\예약 하나"] = ("other.exe", "")
    check("예전 폴더의 우리 작업을 새 폴더·지금 프로그램으로 옮기고 예전 것은 지움", (schedule.move_old_tasks(), sorted(fake.tasks), fake.tasks["\\DataLoupe\\예약 하나"][0].startswith(subprocess.list2cmdline([sys.executable]))),
          (["예약 하나"], ["\\DataLoupe\\예약 하나", "\\Other\\예약 하나", "\\ibis_search\\남의 작업"], True))
    check("한 번 옮기면 다시 옮길 것이 없음", schedule.move_old_tasks(), [])
    wintask.execute = lambda args: (1, "", "오류: 액세스가 거부되었습니다.")
    check("schtasks가 실패하면 한국어 안내와 그 문구", str(raised_error(lambda: wintask.register("x", "x", daily, at(2026, 1, 1)))),
          "Windows 예약 작업을 등록하지 못했습니다. 액세스가 거부되었습니다.")
    wintask.execute = fake
    check("시각 읽기: 오전·오후, 없음, 한 번도 안 돈 것, 모르는 형식은 그대로", [wintask.when(t) for t in ("2026-10-16 오후 1:05:00", "2026-10-16 오전 12:30:00", "N/A", "1999-11-30 오전 12:00:00", "10/16/2026 1:05:00 PM")],
          ["2026-10-16 13:05", "2026-10-16 00:30", None, None, "10/16/2026 1:05:00 PM"])

    print("== 예약: 잠금")
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    lock = schedule.lock_path("잠금 시험")
    lock.parent.mkdir(parents=True, exist_ok=True)
    cases = {}
    for label, text in (("살아 있는 프로세스", f"{os.getpid()} {psutil.Process().create_time()}"), ("깨진 잠금 파일", "깨짐"), ("빈 잠금 파일", "")):
        lock.write_text(text, encoding="utf-8")
        cases[label] = (schedule.hold(lock), lock.read_text(encoding="utf-8") == text)
    check("살아 있는 것·깨진 것·빈 것은 그대로 두고 잡지 않음", cases, {k: (False, True) for k in cases})
    for label, text in (("끝난 프로세스", f"{gone.pid} 1.0"), ("번호를 다시 쓴 프로세스", f"{os.getpid()} 1.0")):
        lock.write_text(text, encoding="ascii")
        cases[label] = (schedule.hold(lock), lock.read_text(encoding="ascii").split()[0] == str(os.getpid()))
        schedule.let_go(lock)
    check("끝난 프로세스의 잠금은 지우고 잡음, 풀면 파일이 없어짐", ({k: cases[k] for k in ("끝난 프로세스", "번호를 다시 쓴 프로세스")}, lock.exists()),
          ({"끝난 프로세스": (True, True), "번호를 다시 쓴 프로세스": (True, True)}, False))
    lock.write_text("다른 것", encoding="utf-8")
    schedule.let_go(lock)
    check("남의 잠금은 풀지 않음", lock.exists(), True)
    lock.unlink()

    print("== 예약: 지금 실행·기록·알림")
    sent = []
    transport.post = lambda url, data, headers: sent.append(json.loads(data)) or b"ok"
    got("/api/destinations", name="알림", destination={"kind": "webhook", "done": True, "url": "https://hooks.example.invalid/sched", "content": "json",
                                                    "template": '{"title": "{제목}", "text": "{내용}", "rows": "{행수}"}'})

    def settle(seconds=30):
        end = time.time() + seconds
        while time.time() < end and (collect_job.running() or any(r["result"] == "도는 중" for r in c.get("/api/runs").get_json()["runs"])):
            time.sleep(0.05)
        return c.get("/api/runs").get_json()

    put("예약 하나", {**daily, "inapp": False, "windows": True, "notify": "알림", "notify_on": "always"})
    check("지금 실행", got("/api/schedules/run", name="예약 하나"), {"ok": True})
    r = settle()
    check("기록: 지금 실행·끝·행 수, 결과 파일", ([(x["name"], x["how"], x["result"], x["rows"], x["message"], x["end"] is not None) for x in r["runs"]], r["unseen"], (out / "one.parquet").is_file()),
          ([("예약 하나", "now", "끝", 7, "", True)], 0, True))
    check("알림(always): 끝났다는 제목, 구성 이름·행 수", (len(sent), sent and sent[0]["title"], sent and "구성: 예약 하나" in sent[0]["text"], sent and sent[0]["rows"]), (1, "수집이 끝났습니다", True, "7"))
    check("지금 실행 거부: 비밀번호가 필요한 구성, 없는 구성", [error("/api/schedules/run", name=n) for n in ("비번 DB", "없는 구성")],
          ["DB 테이블에 적재할 때 비밀번호가 필요한 연결(아이디가 있는 주소)이라 예약할 수 없습니다. 비밀번호는 저장하지 않기 때문입니다.", "구성 '없는 구성'이 없습니다. 구성을 먼저 저장하세요."])
    lock = collect_job.lock_of(schedule.collection("예약 하나"))  # 수집은 결과 파일마다 잠근다
    lock.write_text(f"{os.getpid()} {psutil.Process().create_time()}", encoding="ascii")
    check("같은 구성이 잠겨 있으면 지금 실행 거부", error("/api/schedules/run", name="예약 하나"), schedule.LOCKED)
    sent.clear()
    put("예약 하나", {**daily, "inapp": False, "windows": True, "notify": "알림", "notify_on": "fail"})
    schedule.execute("예약 하나", "inapp")
    r = settle()
    check("잠겨 있으면 건너뜀 기록·알림(실패처럼), 남의 잠금은 그대로", (r["runs"][0]["result"], r["runs"][0]["message"], [s["title"] for s in sent], lock.exists()),
          ("건너뜀", schedule.SKIP_LOCKED, ["수집을 건너뛰었습니다"], True))
    lock.unlink()
    api.routes["/sched"] = lambda e: (500, {}, b"server says no")
    sent.clear()
    schedule.execute("예약 하나", "inapp")
    r = settle()
    check("실패 기록(오류 문구)·unseen·알림(fail)", (r["runs"][0]["result"], bool(r["runs"][0]["message"]), r["unseen"], [s["title"] for s in sent]),
          ("실패", True, 1, ["수집이 실패했습니다"]))
    check("본 것으로 표시하면 unseen 0", (got("/api/runs/seen"), c.get("/api/runs").get_json()["unseen"]), ({"ok": True}, 0))
    api.routes["/sched"] = items
    sent.clear()
    schedule.execute("예약 하나", "inapp")
    settle()
    check("notify_on fail이면 끝난 것은 알리지 않음, 잠금은 풀림", (sent, lock.exists()), ([], False))
    schedule.change_runs(lambda runs: runs.insert(0, {"id": "x", "name": "남은 것", "how": "windows", "start": "2026-10-15 09:00:00", "end": None, "result": "도는 중",
                                                      "rows": None, "message": "", "seen": False, "owner": f"{gone.pid} 1.0"}))
    r = c.get("/api/runs").get_json()
    check("끝난 프로세스의 '도는 중' 기록은 실패로", (r["runs"][0]["result"], r["runs"][0]["message"], r["unseen"]), ("실패", schedule.GONE, 1))
    schedule.change_runs(lambda runs: runs.extend([{**runs[-1], "id": str(i)} for i in range(250)]))
    check("기록은 최근 200개까지, 화면에는 안의 값(id·seen·owner)을 주지 않음", (len(c.get("/api/runs").get_json()["runs"]), sorted(c.get("/api/runs").get_json()["runs"][0])),
          (200, ["end", "how", "message", "name", "result", "rows", "start"]))
    settings.write(schedule.RUNS, {"runs": []})

    print("== 예약: 프로그램 안 예약(시계를 바꿔 끼움)")
    put("예약 하나", {**every, "minutes": 5, "inapp": True, "windows": False})
    due, now = {}, [at(2026, 10, 15, 9, 2)]
    schedule.clock = lambda: now[0]
    schedule.tick(due)
    check("처음 본 예약은 다음 칸부터(놓친 것은 돌리지 않음)", (due["예약 하나"][1], c.get("/api/schedules").get_json()["next_inapp"]),
          (at(2026, 10, 15, 9, 5), {"예약 하나": "2026-10-15 09:05"}))
    now[0] = at(2026, 10, 15, 9, 4, 59)
    schedule.tick(due)
    check("때가 되기 전에는 돌리지 않음", c.get("/api/runs").get_json()["runs"], [])
    now[0] = at(2026, 10, 15, 9, 5, 10)
    schedule.tick(due)
    r = settle()
    check("때가 되면 돌리고 다음 칸을 잡음", ([(x["how"], x["result"], x["rows"]) for x in r["runs"]], due["예약 하나"][1]), ([("inapp", "끝", 7)], at(2026, 10, 15, 9, 10)))
    slow.set()
    got("/api/collect/start", config=cfg("busy.parquet"))
    now[0] = at(2026, 10, 15, 9, 10)
    schedule.tick(due)
    r = c.get("/api/runs").get_json()
    check("다른 수집이 돌고 있으면 건너뜀", (r["runs"][0]["how"], r["runs"][0]["result"], r["runs"][0]["message"]), ("inapp", "건너뜀", schedule.SKIP_RUNNING))
    check("다른 수집이 돌고 있으면 지금 실행은 거부", error("/api/schedules/run", name="예약 하나"), collect_job.RUNNING)
    collect_job.stop(wait=10)
    slow.clear()
    put("예약 하나", {**every, "minutes": 5, "inapp": False})
    schedule.tick(due)
    check("inapp을 끄면 계획에서 빠짐", (due, schedule.planned), ({}, {}))
    check("예약 라우트는 차례(turn)를 기다리지 않음", all(p in server.FREE for p in ("/api/schedules", "/api/schedules/run", "/api/schedules/reregister", "/api/runs", "/api/runs/seen")), True)

    print("== 예약: 창 없이 실행(--run, 소스 실행 프로세스)")
    settings.write(schedule.RUNS, {"runs": []})
    env = {**os.environ, "TEMP": str(ROOT), "TMP": str(ROOT)}
    (out / "one.parquet").unlink()
    done = subprocess.run([sys.executable, "-B", "-W", "ignore", "desktop.py", "--run", "예약 하나"], env=env, capture_output=True, timeout=180)
    r = c.get("/api/runs").get_json()
    check("--run: 종료 코드 0, 기록(windows·끝·7행), 결과 파일", (done.returncode, [(x["name"], x["how"], x["result"], x["rows"]) for x in r["runs"]],
          (out / "one.parquet").is_file() and pl.read_parquet(out / "one.parquet").height), (0, [("예약 하나", "windows", "끝", 7)], 7))
    done = subprocess.run([sys.executable, "-B", "-W", "ignore", "desktop.py", "--run", "비번 DB"], env=env, capture_output=True, timeout=180)
    r = c.get("/api/runs").get_json()
    check("--run: 비밀번호가 필요한 구성은 실행하지 않고 실패로 기록, 종료 코드 1", (done.returncode, r["runs"][0]["result"], r["runs"][0]["message"], (out / "db.parquet").exists()),
          (1, "실패", "DB 테이블에 적재할 때 비밀번호가 필요한 연결(아이디가 있는 주소)이라 예약할 수 없습니다. 비밀번호는 저장하지 않기 때문입니다.", False))
    check("--run이 남긴 잠금 없음", [p.name for p in schedule.locks().glob("*.lock")], [])

    wintask.execute, schedule.clock, transport.post = real_execute, real_clock, real_post
    settings.write(schedule.FILE, {})
    got("/api/destinations", name="알림", destination=None)
    api.http.shutdown()


def memory_section(got, duck):
    """데이터 라이브러리 지연 로딩과 DuckDB 파일 조회의 작업 메모리 상한."""
    print("== 지연 로딩·메모리 상한")
    error = lambda api, **body: got(api, **body).get("error", "")
    code = """
import json, sys
sys.path.insert(0, sys.argv[1]); sys.dont_write_bytecode = True
import lazy
from core import server
from query import search_app
from prep import prep_app
from integrations import notify
from collect import collect_app
names = ("ibis", "polars", "duckdb", "pyarrow")
is_lazy = lambda: {n: type(sys.modules[n]).__name__ == "_LazyModule" for n in names}
out = {"imported": is_lazy(), "purge": __import__("os").environ.get("MIMALLOC_PURGE_DELAY")}
c = server.app.test_client(); c.set_cookie("key", server.KEY)
out["connect"] = c.post("/api/connect", json={"kind": "folder", "path": sys.argv[2], "files": [sys.argv[3]]}).get_json()
out["parquet"] = is_lazy()
print(json.dumps(out))
"""
    flat = PREP_FLAT / "messy.parquet"
    found = subprocess.run([sys.executable, "-c", code, str(Path(__file__).parent), str(PREP_FLAT), str(flat)], capture_output=True, text=True, encoding="utf-8")
    out = json.loads(found.stdout.strip().splitlines()[-1]) if found.returncode == 0 else {"error": found.stderr[-300:]}
    check("프로젝트 모듈을 불러온 직후에는 ibis·polars·duckdb·pyarrow가 모두 지연 상태", out.get("imported"), dict.fromkeys(("ibis", "polars", "duckdb", "pyarrow"), True))
    check("쿼리가 끝나면 Parquet·CSV 계산에 쓴 메모리를 바로 돌려주는 설정(lazy가 라이브러리보다 먼저 정함)", out.get("purge"), "0")
    check("Parquet 연결 뒤: ibis·polars는 올라오고 duckdb는 여전히 지연 상태", (out.get("connect"), {n: out["parquet"][n] for n in ("ibis", "polars", "duckdb")}),
          ({"schemas": []}, {"ibis": False, "polars": False, "duckdb": True}))

    text = lambda con=None: (con or server.state["con"]).con.execute("select current_setting('memory_limit')").fetchone()[0]
    text = lambda con=None: (con or server.state["con"]).con.execute("select current_setting('memory_limit')").fetchone()[0]
    limit = lambda con=None: round(float(text(con).split()[0]) * {"KiB": 2**10, "MiB": 2**20, "GiB": 2**30}[text(con).split()[1]] / 1e9, 1)  # DuckDB의 MB·GB는 10^6·10^9 바이트
    connect = lambda: got("/api/connect", kind="folder", path=str(DATA), file=duck)
    check("memory 설정(MB): 256~1,000,000의 정수만", [error("/api/settings", memory=v)[:15] for v in (255, 1_000_001, "2", 1500.0)], ["'memory' 설정에 쓸 수"[:15]] * 4)
    check("옛 값(글자 \"2\", 비율 \"50\")이 저장돼 있어도 읽을 때 버려짐", (settings.write("settings.json", {"memory": "2", "theme": "dark"}), settings.load()), (None, {"theme": "dark"}))
    got("/api/settings", memory=0)
    connect()
    check("memory 0: 제한 없음(임시 폴더 없음)", hasattr(server.state["con"], "scratch"), False)
    got("/api/settings", memory=1500)
    connect()
    first = server.state["con"].scratch
    check("memory 1500: 상한이 1.5GB이고 임시 폴더를 씀", (abs(limit() - 1.5) < 0.15, os.path.isdir(first)), (True, True))
    got("/api/settings", memory=4000)
    connect()
    check("연결을 바꾸면 이전 연결의 임시 폴더를 지움", (os.path.exists(first), abs(limit() - 4.0) < 0.15, os.path.isdir(server.state["con"].scratch)), (False, True, True))
    scratch = server.state["con"].scratch
    got("/api/disconnect")
    check("연결을 끊으면 임시 폴더를 지움", os.path.exists(scratch), False)
    con = source.open_duckdb(duck, False, 1500)  # 새 테이블을 만들 때처럼 쓰기로 열어도 같은 상한
    writable = (abs(limit(con) - 1.5) < 0.15, os.path.isdir(con.scratch))
    source.close(con)
    check("쓰기로 여는 길도 같은 상한, 닫으면 임시 폴더를 지움", (writable, os.path.exists(con.scratch)), ((True, True), False))
    got("/api/connect", kind="folder", path=str(PREP_FLAT), files=[str(flat)])
    check("Parquet 연결에는 적용하지 않음", hasattr(server.state["con"], "scratch"), False)
    got("/api/connect", kind="url", url=f"duckdb://{duck}")
    check("주소 연결에는 적용하지 않음", hasattr(server.state["con"], "scratch"), False)
    got("/api/settings", memory=0)
    got("/api/disconnect")
    with app.test_request_context():
        found = [server.on_error(Exception(text))[0].get_json()["error"] for text in ("Out of Memory Error: failed to allocate", "OUT OF MEMORY", "다른 오류")]
    check("메모리 부족 오류에는 안내 문구가 붙음", found, ["메모리가 모자랍니다. 작업 메모리 상한을 올리거나 조건을 좁히세요. Out of Memory Error: failed to allocate",
          "메모리가 모자랍니다. 작업 메모리 상한을 올리거나 조건을 좁히세요. OUT OF MEMORY", "다른 오류"])

    cached = lambda: server.state["con"].con.execute("select coalesce(sum(memory_usage_bytes), 0) from duckdb_memory() where tag = 'BASE_TABLE'").fetchone()[0]
    for memory in (1500, 0):
        got("/api/settings", memory=memory)
        connect()
        base = source.list_tables(server.state["con"])[0]
        before = text()
        server.state["con"].con.execute(f'select count(*) from "{base}"').fetchall()  # 비우지 않고 돌린 쿼리는 캐시를 남긴다
        kept = cached() > 0
        query = got("/api/query", base=base, limit=5)
        summary = got("/api/summary", base=base)
        check(f"memory {memory}: 쿼리가 끝나면 DB 캐시를 비우고(64MB 아래로) 상한은 그대로, 결과는 정상",
              (kept, cached() < 64 * 10**6, text() == before, len(query["rows"]["rows"]), len(summary["summary"]["rows"]) > 0), (True, True, True, 5, True))
    got("/api/settings", memory=0)
    got("/api/connect", kind="url", url=f"duckdb://{duck}")
    sent = []
    real, server.state["con"].raw_sql = server.state["con"].raw_sql, lambda *a, **k: sent.append(a) or real(*a, **k)
    source.release(server.state["con"])
    check("주소로 연결한 DB는 비우지 않음(쿼리를 더 보내지 않음)", (getattr(server.state["con"], "local", False), sent), (False, []))
    got("/api/disconnect")


def sqlite_section(c, got):
    """SQLite 주소 연결. 이 연결은 만든 스레드에서만 쓸 수 있어, 연결을 쓰는 일이 모두 한 스레드에서 도는지 여기서 드러난다."""
    print("== SQLite 주소 연결")
    n = lambda keep: sum(1 for o in orders if keep(o))
    tables = lambda: sorted(c.get("/api/tables").get_json().get("tables", []))
    check("SQLite 연결", got("/api/connect", kind="url", url=f"sqlite://{DATA / 'shop.db'}"), {"schemas": ["main"]})
    check("SQLite 테이블 목록", tables(), ["customers", "orders"])
    check("SQLite /api/state", c.get("/api/state").get_json()["schemas"], ["main"])
    r = got("/api/query", base="orders", joins=["customers left customer_id=id"], conditions=["status = paid", "amount >= 20000"], sorts=["order_id"], columns=["order_id, name"], limit=3)
    mine = [o for o in orders if o["status"] == "paid" and o["amount"] >= 20000]
    names = {p["id"]: p["name"] for p in customers}
    check("SQLite 검색(조인·조건·정렬·표시 컬럼)", r, {"count": len(mine), "rows": {"columns": ["order_id", "name"],
          "rows": [{"order_id": o["order_id"], "name": names.get(o["customer_id"])} for o in mine[:3]]}, "held": True, "server": False, "note": ""})  # 주소로 접속한 DB는 결과를 받아 둔다
    check("SQLite 행 수·값 목록", (got("/api/rowcount", base="orders"), got("/api/values", base="orders", column="status")),
          ({"rows": len(orders)}, {"values": sorted(STATUSES), "more": False}))
    rows = {row["name"]: row for row in got("/api/summary", base="orders")["summary"]["rows"]}
    amounts = [o["amount"] for o in orders]
    check("SQLite 집계", (list(rows), rows["amount"]["min"], rows["amount"]["max"], round(rows["amount"]["mean"], 6), rows["note"]["nulls"]),
          (list(orders[0]), min(amounts), max(amounts), round(sum(amounts) / len(amounts), 6), n(lambda o: o["note"] is None)))
    sizes = Counter(o["status"] for o in orders)
    r = got("/api/chart", base="orders", x="status", series=[{"agg": "count"}])
    check("SQLite 차트", (r["labels"], r["datasets"][0]["values"]), (sorted(STATUSES), [sizes[s] for s in sorted(STATUSES)]))
    data = c.post("/api/export", json={"base": "orders", "sorts": ["order_id"], "columns": ["order_id"], "format": "csv"}).data
    check("SQLite CSV 저장", len(data.decode("utf-8").splitlines()), len(orders) + 1)
    recipe = {"base": "customers", "columns": {"name": {"ops": {"case": "upper"}}}, "sorts": ["id"]}
    r = got("/api/preview", **recipe)
    check("SQLite 전처리 미리보기", ([row["name"] for row in r["rows"]["rows"]], r["before"], r["after"]), ([p["name"].upper() for p in customers[:20]], 40, 40))
    r = got("/api/preview", base="customers", columns={"name": {"ops": {"extract": {"pattern": "[0-9]+"}, "has": {"words": ["customer0"]}}}}, sorts=["id"])
    check("SQLite extract·has: 다른 연결과 같은 값", ([row["name_match"] for row in r["rows"]["rows"]], [row["name_has_customer0"] for row in r["rows"]["rows"]]),
          ([f"{i:02d}" for i in range(1, 21)], [1] * 9 + [0] * 11))
    counts = {"name": {"ops": {"counts": {"sep": "r"}}}}
    check("SQLite counts: 이 PC의 작업 사본에서는 됨(기본)", got("/api/preview", base="customers", columns=counts).get("local"), True)
    prep_where("new")  # 계산 컬럼·PCA가 없으면 DB에서 계산
    check("SQLite counts: DB에서 계산하면 지원하지 않는 계산", got("/api/preview", base="customers", columns=counts).get("error", "")[:22], "이 연결에서는 지원하지 않는 계산입니다.")
    check("SQLite 새 테이블 만들기(DB에서 계산)", (got("/api/run", recipe=recipe, target={"kind": "table", "name": "upper_names"}), tables()),
          ({"ok": True, "rows": 40}, ["customers", "orders", "upper_names"]))
    prep_where("all")
    check("SQLite 만든 테이블의 값", got("/api/query", base="upper_names", sorts=["id"], columns=["name"], limit=1, count=False)["rows"]["rows"], [{"name": "CUSTOMER01"}])
    thread, forever = source.worker, "with recursive n(i) as (select 1 union all select i + 1 from n) select count(*) from n"
    threading.Timer(0.3, lambda: c.post("/api/cancel")).start()
    check("SQLite 취소: 끝나지 않는 조회가 멈춤", raised(lambda: server.run(lambda: state["con"].con.execute(forever).fetchall())), "ValueError")
    check("SQLite 취소 뒤에도 같은 스레드, 같은 연결로 검색", (source.worker is thread, got("/api/query", base="orders")["count"]), (True, len(orders)))
    check("SQLite 다른 연결로 바꿨다가 돌아오기", (got("/api/connect", kind="folder", path=str(DATA), file=str(DATA / "shop.duckdb")).get("schemas") is not None,
          got("/api/connect", kind="url", url=f"sqlite://{DATA / 'shop.db'}"), tables()), (True, {"schemas": ["main"]}, ["customers", "orders", "upper_names"]))
    check("SQLite 연결 해제", (got("/api/disconnect"), c.get("/api/tables").get_json()), ({"ok": True}, {"error": "먼저 연결하세요."}))


def main():
    make_data()
    c = app.test_client()
    got = lambda api, **body: c.post(api, json=body).get_json()
    error = lambda api, **body: got(api, **body).get("error", "")
    query = lambda **body: got("/api/query", base="orders", **body)
    n = lambda keep: sum(1 for o in orders if keep(o))
    tables = lambda: sorted(c.get("/api/tables").get_json().get("tables", []))

    print("== 열쇠")
    check("열쇠 없는 요청은 403", c.get("/api/tables").status_code, 403)
    for path in ("/", "/search", "/prep", "/collect"):
        r = c.get(path + "?key=" + KEY)
        nonce = re.search(r"script-src 'nonce-([^']+)'", r.headers.get("Content-Security-Policy", ""))
        check(f"{path} 화면의 스크립트는 모두 CSP의 nonce를 가짐", (r.status_code, r.text.count("<script") == r.text.count(f'<script nonce="{nonce and nonce.group(1)}"') > 0), (200, True))
        given = re.search(r"const SERVER = (.*);", r.text)
        check(f"{path} 화면에 서버의 범위·기본값(CONSTANTS)이 들어감", given and json.loads(given.group(1)), json.loads(json.dumps(server.constants({"/": "core", "/search": "query", "/prep": "prep", "/collect": "collect"}[path]))))
    check("수집 화면이 쓰는 범위가 모두 있음(구성·사용 한도·예약)", {"paging.max_pages", "limit.margin", "schedule.minutes"} <= set(server.constants("collect")["numbers"]), True)
    static = ROOT / "static"  # 지어낸 파일로 확인한다. 실제 static은 건드리지 않는다.
    static.mkdir()
    (static / "frag.html").write_text("<p>조각</p>", encoding="utf-8")
    (static / "page.html").write_text("<!--include frag.html--><script>const SERVER = /*SERVER*/{};</script>", encoding="utf-8")
    server.CONSTANTS["probe"] = "</script><b>"  # 값에 </script>가 있어도 스크립트가 끝나지 않는지
    try:
        with Flask(__name__, static_folder=str(static)).app_context():
            html = server.page("page.html").get_data(as_text=True)
    finally:
        del server.CONSTANTS["probe"]
    check("page: include를 내용으로 바꾸고 그 안의 스크립트에도 nonce를 붙임", ("include" in html, "<p>조각</p>" in html, "<script nonce=" in html), (False, True, True))
    check("page: SERVER 자리의 값은 <를 \\u003c로 넣어 스크립트를 끝내지 않음", (html.count("</script>"), "\\u003c/script>\\u003cb>" in html), (1, True))
    check("그 뒤로는 쿠키로 통과, 최근 연결은 아직 없음", c.get("/api/recent").get_json(), {"recent": None})

    print("== 연결")
    check("/api/state: 연결 전", c.get("/api/state").get_json(), {"connected": False})
    found = got("/api/files", path=str(DATA))
    check("/api/files", ([Path(f).name for f in found["files"]], [Path(f).name for f in found["flat"]]), (["shop.duckdb"], ["buyers.parquet", "sales.csv"]))
    duck = found["files"][0]
    check("Parquet·CSV 파일 연결", got("/api/connect", kind="folder", path=str(DATA), files=found["flat"]), {"schemas": []})
    check("파일마다 테이블 하나", (tables(), got("/api/query", base="sales")["count"]), (["buyers", "sales"], 5))
    check("/api/state: Parquet·CSV 연결은 files를 포함", c.get("/api/state").get_json(),
          {"connected": True, "opened": {"kind": "folder", "path": str(DATA), "files": found["flat"]}, "schemas": []})
    check("Parquet·CSV 연결은 SQL을 보여 줄 수 없음", error("/api/sql", base="sales"), "이 연결의 조회는 SQL로 보여 줄 수 없습니다.")
    got("/api/connect", kind="url", url=f"duckdb://{duck}")
    check("주소로 연결", tables(), ["customers", "orders"])
    r = c.get("/api/state").get_json()
    check("/api/state: 주소 연결", (r["connected"], r["opened"], "main" in r["schemas"]), (True, {"kind": "url", "url": f"duckdb://{duck}", "user": ""}, True))
    real = state["opened"]
    state["opened"] = {"kind": "url", "url": "postgres://me:secret-pw@host/db", "user": "me", "password": "field-pw"}  # 비밀번호가 든 연결 요청 값
    check("/api/state: 비밀번호는 칸에 쓴 것도 주소 안에 쓴 것도 내보내지 않음", ("secret-pw" in c.get("/api/state").text, "field-pw" in c.get("/api/state").text), (False, False))
    state["opened"] = real
    check("/api/recent", c.get("/api/recent").get_json()["recent"], {"kind": "url", "url": f"duckdb://{duck}", "user": ""})
    e = error("/api/connect", kind="url", url="nosuch://someone:inline-pw-1@host/db", user="me", password="field-pw-2")
    check("연결 오류에서 비밀번호를 가림", ("***" in e, "inline-pw-1" in e, "field-pw-2" in e), (True, False, False))
    check("연결에 실패하면 이전 연결을 다시 엶", tables(), ["customers", "orders"])
    check("DuckDB 파일 연결", "main" in got("/api/connect", kind="folder", path=str(DATA), file=duck).get("schemas", []), True)
    check("/api/state: DuckDB 파일 연결은 file을 포함", c.get("/api/state").get_json()["opened"], {"kind": "folder", "path": str(DATA), "file": duck})
    check("최근 연결에 주소 안의 비밀번호를 남기지 않음", source.recent({"kind": "url", "url": "postgres://me:secret@host/db", "user": "me"}),
          {"kind": "url", "url": "postgres://me@host/db", "user": "me"})
    check("짧은 비밀번호는 주소의 비밀번호 자리에서만 가림", source.mask("db://me:ab@host/ab", ["ab"]), "db://me:***@host/ab")

    print("== 테이블·컬럼")
    check("/api/tables", tables(), ["customers", "orders"])
    check("/api/columns", got("/api/columns", base="orders")["columns"], [["order_id", "정수"], ["customer_id", "정수"], ["status", "문자"],
          ["amount", "정수"], ["ordered_on", "날짜"], ["paid_at", "날짜시간"], ["note", "문자"]])
    check("/api/rowcount", got("/api/rowcount", base="orders"), {"rows": len(orders)})
    check("/api/values", got("/api/values", base="orders", column="status"), {"values": sorted(STATUSES), "more": False})

    print("== 검색")
    name_of, city_of = {p["id"]: p["name"] for p in customers}, {p["id"]: p["city"] for p in customers}
    join = ["customers left customer_id=id"]
    r = query(joins=join, sorts=["amount desc", "order_id"], columns=["order_id, name"], limit=5, offset=5)
    top = sorted(orders, key=lambda o: (-o["amount"], o["order_id"]))[5:10]
    check("조인·정렬·표시 컬럼·건너뛰기", r, {"count": len(orders), "rows": {"columns": ["order_id", "name"],
          "rows": [{"order_id": o["order_id"], "name": name_of.get(o["customer_id"])} for o in top]}, "held": False, "server": False, "note": ""})
    march = lambda o: dt.date(2024, 3, 1) <= o["paid_at"].date() <= dt.date(2024, 3, 31)
    check("기간(끝 날짜는 그날 끝까지)", query(periods=["paid_at 2024-03-01 ~ 2024-03-31"]).get("count"), n(march))
    note = lambda o: o["note"] or ""
    for name, cond, keep in [
        ("= != 비교", ["status != paid", "amount >= 20000"], lambda o: o["status"] != "paid" and o["amount"] >= 20000),
        ("between", ["ordered_on between 2024-02-01 2024-02-29"], lambda o: dt.date(2024, 2, 1) <= o["ordered_on"] <= dt.date(2024, 2, 29)),
        ("in", ["status in new, paid"], lambda o: o["status"] in ("new", "paid")),
        ("except", ["status except new, paid"], lambda o: o["status"] not in ("new", "paid")),
        ("like", ["note like VIP"], lambda o: "vip" in note(o)),
        ("is null", ["note is null"], lambda o: o["note"] is None),
        ("is not null", ["note is not null"], lambda o: o["note"] is not None),
        ("or", ["status = new or amount < 5000"], lambda o: o["status"] == "new" or o["amount"] < 5000),
        ("따옴표로 감싼 값", ["note in 'a, b', 'red or blue'"], lambda o: note(o) in ("a, b", "red or blue")),
        ("any 묶음", [{"any": ["status = new", "note is null"]}], lambda o: o["status"] == "new" or o["note"] is None),
        ("quotes: false", [{"line": "note = 'vip'", "quotes": False}], lambda o: note(o) == "'vip'"),
    ]:
        check("조건 " + name, query(conditions=cond).get("count"), n(keep))
    check("행 수 0과 상한 초과는 거절", [error("/api/query", base="orders", limit=v)[:4] for v in (0, 10001)], ["행 수는"] * 2)
    r = query(limit=7, count=False)
    check("count: false", (r["count"], len(r["rows"]["rows"])), (None, 7))
    check("/api/sql", "SELECT" in got("/api/sql", base="orders", conditions=["amount > 100"]).get("sql", ""), True)
    check("/api/cancel은 도는 작업이 없어도 되고 다음 검색은 정상", (got("/api/cancel"), query().get("count")), ({"ok": True}, len(orders)))

    print("== 집계·차트")
    rows = {row["name"]: row for row in got("/api/summary", base="orders")["summary"]["rows"]}
    amounts, days = [o["amount"] for o in orders], [o["ordered_on"] for o in orders]
    check("집계: 모든 컬럼", list(rows), list(orders[0]))
    got_amount = {**rows["amount"], "mean": round(rows["amount"]["mean"], 6)}
    check("집계: 숫자", got_amount, {"name": "amount", "type": "int64", "count": len(orders), "nulls": 0, "unique": len(set(amounts)),
          "min": min(amounts), "max": max(amounts), "mean": round(sum(amounts) / len(amounts), 6)})
    check("집계: 날짜의 최소·최대", (rows["ordered_on"]["min"], rows["ordered_on"]["max"]), (str(min(days)), str(max(days))))
    check("집계: 문자는 평균 없음, 빈 값 수", (rows["note"]["mean"], rows["note"]["nulls"]), (None, n(lambda o: o["note"] is None)))
    r = got("/api/summary", base="orders", summary_columns=["note", "amount"])
    check("집계: 고른 컬럼만 그 순서로", [row["name"] for row in r["summary"]["rows"]], ["note", "amount"])
    labels, sizes = sorted(STATUSES), Counter(o["status"] for o in orders)
    r = got("/api/chart", base="orders", x="status", series=[{"agg": "count"}])
    check("차트: 계열 하나", (r["labels"], r["datasets"]), (labels, [{"series": 0, "split": None, "values": [sizes[s] for s in labels]}]))
    total = {s: sum(o["amount"] for o in orders if o["status"] == s) for s in labels}
    by_value = sorted(labels, key=lambda s: (-total[s], s))
    r = got("/api/chart", base="orders", x="status", order="value", series=[{"y": "amount", "agg": "sum"}, {"y": "amount", "agg": "mean"}])
    check("차트: 계열 둘, 값이 큰 순", (r["labels"], r["datasets"][0]["values"], [round(v, 6) for v in r["datasets"][1]["values"]]),
          (by_value, [total[s] for s in by_value], [round(total[s] / sizes[s], 6) for s in by_value]))
    cells = Counter((o["status"], city_of[o["customer_id"]]) for o in orders if o["customer_id"] in city_of)
    cities = Counter(city for _, city in cells.elements())
    r = got("/api/chart", base="orders", joins=join, x="status", split="city", series=[{"agg": "count"}])
    check("차트: 값별로 나눔", (r["datasets"], r["more_splits"]), ([{"series": 0, "split": city, "values": [cells.get((s, city)) for s in labels]}
          for city in sorted(cities, key=lambda v: (-cities[v], v))], False))

    print("== 저장")
    export = lambda kind: c.post("/api/export", json={"base": "orders", "sorts": ["order_id"], "columns": ["order_id, note"], "format": kind}).data
    data = export("csv")
    check("CSV: BOM, 전체 행, 수식 값 앞에 '", (data[:3], len(data.decode("utf-8").splitlines()), b"'=1+1" in data), (b"\xef\xbb\xbf", len(orders) + 1, True))
    book = zipfile.ZipFile(io.BytesIO(export("xlsx")))  # 메모리 절약 모드는 글자를 시트 안에 바로 쓴다(sharedStrings가 아니라)
    check("Excel: 수식 값 앞에 '", any("'=1+1" in book.read(n).decode("utf-8") for n in book.namelist() if n.endswith(".xml")), True)
    check("Parquet: 값을 그대로", pl.read_parquet(io.BytesIO(export("parquet")))["note"].to_list(), [o["note"] for o in orders])
    check("모르는 형식은 거절", error("/api/export", base="orders", format="txt")[:6], "파일 이름은")

    print("== 설정·저장한 검색·사용량")
    check("/api/usage", sorted(c.get("/api/usage").get_json()), ["cpu", "memory", "memory_mb", "pc_cpu", "pc_memory"])
    check("설정 저장", (got("/api/settings", theme="dark"), settings.load()), ({"ok": True}, {"theme": "dark"}))
    check("쓸 수 없는 설정 값은 거절", (error("/api/settings", theme="blue")[:11], settings.load()), ("'theme' 설정에", {"theme": "dark"}))
    saved = {"base": "orders", "conditions": ["amount > 100"], "limit": "20"}
    check("검색 저장", got("/api/searches", name="mine", search=saved), {"searches": {"mine": saved}, "order": ["mine"]})
    got("/api/searches", name="mine", search={"base": "customers"})
    check("같은 이름은 덮어씀", c.get("/api/searches").get_json(), {"searches": {"mine": {"base": "customers"}}, "order": ["mine"]})
    check("검색 지우기", got("/api/searches", name="mine", search=None), {"searches": {}, "order": []})
    check("빈 이름은 거절", error("/api/searches", name=" ", search=saved), "검색 이름은 1자 이상 50자 이하로 입력하세요.")
    recipe = {"base": "messy", "columns": {"name": {"ops": {"trim": True}}}, "sorts": ["id"]}
    check("구성 목록은 처음에 비어 있음", c.get("/api/recipes").get_json(), {"recipes": {}})
    check("구성 저장(이름 앞뒤 공백은 뗌)", got("/api/recipes", name=" 정리 ", recipe=recipe), {"recipes": {"정리": recipe}})
    check("구성과 검색은 서로 섞이지 않음", (c.get("/api/searches").get_json(), got("/api/searches", name="s", search={"base": "orders"})["searches"], c.get("/api/recipes").get_json()),
          ({"searches": {}, "order": []}, {"s": {"base": "orders"}}, {"recipes": {"정리": recipe}}))
    check("저장 파일도 따로", sorted(p.name for p in settings.PATH.parent.glob("*.json") if p.name in ("recipes.json", "searches.json")), ["recipes.json", "searches.json"])
    got("/api/recipes", name="정리", recipe={"base": "other"})
    check("같은 이름은 덮어씀", c.get("/api/recipes").get_json(), {"recipes": {"정리": {"base": "other"}}})
    check("구성 지우기(없는 이름도 됨)", (got("/api/recipes", name="정리", recipe=None), got("/api/recipes", name="없음", recipe=None)), ({"recipes": {}},) * 2)
    check("구성 제한: 이름, 객체가 아님, 크기", [error("/api/recipes", name=n, recipe=recipe) for n in ("", "x" * 51)] + [error("/api/recipes", name="a", recipe=[1]),
          error("/api/recipes", name="a", recipe={"x": "가" * settings.MAX_RECIPE_SIZE})],
          ["구성 이름은 1자 이상 50자 이하로 입력하세요."] * 2 + ["저장할 구성이 올바르지 않습니다.", "저장할 구성이 너무 큽니다."])
    check("검색 크기 상한은 구성보다 작음", error("/api/searches", name="a", search={"x": "가" * settings.MAX_SEARCH_SIZE}), "저장할 검색이 너무 큽니다.")
    check("구성은 검색 상한(2만 자)보다 크게 저장됨", len(got("/api/recipes", name="큼", recipe={"x": "가" * (settings.MAX_SEARCH_SIZE + 100)})["recipes"]), 1)
    for i in range(settings.MAX_SAVED - 1):
        got("/api/recipes", name=f"r{i}", recipe={})
    check("구성은 100개까지, 있는 이름은 덮어쓸 수 있음", (error("/api/recipes", name="넘침", recipe={}), len(got("/api/recipes", name="r0", recipe={"a": 1})["recipes"])),
          ("저장한 구성은 100개까지입니다. 쓰지 않는 것을 지우세요.", 100))
    for name in list(c.get("/api/recipes").get_json()["recipes"]):
        got("/api/recipes", name=name, recipe=None)
    got("/api/searches", name="s", search=None)

    print("== 검색 결과를 파일로 저장하고 그 파일에 연결(handoff)")
    out, asked = ROOT / "out", []
    out.mkdir()

    def choose(path):
        """저장 대화상자를 대신해 path를 고르는 함수. 묻는 기본 이름을 asked에 적어 둔다."""
        return lambda name, types: (asked.append((name, types)), path)[1]

    body = dict(base="orders", conditions=["note = =1+1"], columns=["order_id, note"], sorts=["order_id"])
    mine = [o for o in orders if o["note"] == "=1+1"]
    got("/api/connect", kind="folder", path=str(DATA), file=duck)
    server.ask_save = None
    check("저장 위치를 물을 수 없으면 거부", error("/api/handoff", **body), "저장 위치를 물을 수 없습니다. 프로그램 창에서만 쓸 수 있습니다.")
    server.ask_save = choose(str(out / "picked"))
    check("handoff: 확장자 없으면 .parquet을 붙이고 경로·테이블 이름을 돌려줌", (got("/api/handoff", **body), asked),
          ({"path": str(out / "picked.parquet"), "table": "picked"}, [("orders_search.parquet", ("Parquet (*.parquet)",))]))
    saved = pl.read_parquet(out / "picked.parquet")
    check("저장된 Parquet: 조건에 맞는 전체 행, 표시 컬럼, 수식 막기 없는 원래 값", (saved.columns, saved["order_id"].to_list(), set(saved["note"].to_list())),
          (["order_id", "note"], [o["order_id"] for o in mine], {"=1+1"}))
    check("저장 뒤 연결이 그 파일 하나로 바뀜", (c.get("/api/state").get_json(), tables()),
          ({"connected": True, "opened": {"kind": "folder", "path": str(out), "files": [str(out / "picked.parquet")]}, "schemas": []}, ["picked"]))
    check("그 테이블로 profile·rowcount·검색이 됨", (got("/api/profile", base="picked")["rows"], got("/api/rowcount", base="picked"), got("/api/query", base="picked")["count"]),
          (len(mine), {"rows": len(mine)}, len(mine)))
    check("최근 연결에도 남음", c.get("/api/recent").get_json()["recent"], {"kind": "folder", "path": str(out)})
    before = (out / "picked.parquet").stat()
    again = dict(base="picked", conditions=["order_id >= 0"])
    check("지금 읽고 있는 파일(대소문자 달라도)에는 저장하지 않음", [(setattr(server, "ask_save", choose(str(out / n))), error("/api/handoff", **again))[1] for n in ("picked.parquet", "PICKED.parquet", "picked")],
          ["지금 읽고 있는 파일에는 저장할 수 없습니다. 다른 이름을 고르세요."] * 3)
    after = (out / "picked.parquet").stat()
    check("거부해도 파일과 연결은 그대로", ((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), tables()), (True, ["picked"]))
    server.ask_save = lambda name, types: None
    check("취소하면 cancelled", (got("/api/handoff", **again), tables()), ({"cancelled": True}, ["picked"]))
    server.ask_save = choose(str(out / "x.txt"))
    check("다른 확장자에도 .parquet을 붙임", (got("/api/handoff", base="picked", columns=["order_id"])["path"], tables()), (str(out / "x.txt.parquet"), ["x.txt"]))
    server.ask_save = choose(str(out / "Y.PARQUET"))
    check("대문자 확장자는 그대로", got("/api/handoff", **{**again, "base": "x.txt"})["path"], str(out / "Y.PARQUET"))
    server.ask_save = choose(str(out / "picked.parquet"))
    check("읽고 있지 않은 기존 파일은 덮어씀", (got("/api/handoff", base="Y", conditions=["order_id < 100"])["table"], got("/api/rowcount", base="picked")),
          ("picked", {"rows": sum(o["order_id"] < 100 for o in mine)}))
    server.ask_save = choose(str(out / "nodir" / "a.parquet"))
    check("쓰기에 실패하면 이전 연결을 유지하고 만들다 만 파일을 남기지 않음", (bool(error("/api/handoff", **again)), tables(), sorted(p.name for p in out.iterdir())),
          (True, ["picked"], ["Y.PARQUET", "picked.parquet", "x.txt.parquet"]))
    asked.clear()
    check("잘못된 검색은 저장 위치를 묻기 전에 거부", (error("/api/handoff", base="nothing")[:20], asked), ("'nothing' 테이블이 없습니다.", []))
    server.ask_save = None

    print("== 기다리기·연결 해제")
    check("within: 시간을 넘기면 TimeoutError", raised(lambda: source.within(0.05, lambda: time.sleep(0.3), "늦음")), "TimeoutError")
    wake = threading.Event()
    threading.Timer(0.05, wake.set).start()
    check("within: 밖에서 깨우면 InterruptedError", raised(lambda: source.within(5, lambda: time.sleep(0.3), "늦음", wake)), "InterruptedError")
    check("/api/disconnect", got("/api/disconnect"), {"ok": True})
    check("연결을 끊은 뒤", c.get("/api/tables").get_json(), {"error": "먼저 연결하세요."})
    thread = source.worker
    check("within: 연결 스레드에서 돌고, 그 안에서 부른 일도 같은 스레드", source.within(1, lambda: (threading.current_thread(), source.within(1, threading.current_thread, "늦음")), "늦음"), (thread, thread))
    late = []
    check("within: 멈출 수 없는 일은 두고 새 스레드로 넘어가 다음 일이 막히지 않음",
          (raised(lambda: source.within(0.05, lambda: time.sleep(0.5) or "늦은 결과", "늦음", drop=late.append)), source.worker is thread, source.within(0.2, lambda: "다음", "늦음")),
          ("TimeoutError", False, "다음"))
    thread.join(2)
    check("within: 두고 온 일의 늦은 결과는 drop으로 가고 그 스레드는 끝남", (late, thread.is_alive()), (["늦은 결과"], False))
    notify_section(c, got)
    collect_section(c, got)
    web_section(c, got)
    load_section(c, got)
    schedule_section(c, got)
    memory_section(got, duck)
    sqlite_section(c, got)
    prep_section()
    numbers_section()


try:
    main()
except Exception as e:  # 확인 도중 멈춰도 정리하고 실패로 끝낸다.
    print("FAIL 중단됨 ->", repr(e))
    fails.append("중단됨")
finally:
    if state["con"] is not None:
        source.close(state["con"])
    print("== 임시 폴더")
    from core import scratch as temp
    temp.ROOT = ROOT / "scratch"  # 실제 임시 폴더를 건드리지 않게 검사용 폴더로 바꾼다.
    mine = Path(temp.make())
    gone = subprocess.Popen([sys.executable, "-c", "pass"])  # 이미 끝난 프로세스의 번호
    gone.wait()

    def folder(name, mark=None):
        made = temp.ROOT / name
        made.mkdir()
        (made / "data.tmp").write_text("x", encoding="utf-8")
        if mark is not None:
            (made / temp.MARK).write_text(mark, encoding="ascii")
        return made

    dead = f"{gone.pid} 1.0"
    left = folder(f"{gone.pid}_abcdefgh", dead)
    kept = {
        "표식이 없는 폴더": folder(f"{gone.pid}_nomark00"),
        "이름이 모양과 다른 폴더": folder(f"x{gone.pid}_abcdefgh", dead),
        "이름과 표식의 번호가 다른 폴더": folder(f"{gone.pid + 1}_abcdefgh", dead),
        "표식이 깨진 폴더": folder(f"{gone.pid}_broken00", "여기는 표식이 아님".encode("utf-8").hex()[:9]),
        "켜져 있는 프로세스의 폴더": mine,
        "같은 번호지만 시작 시각이 같은(= 켜져 있는) 프로세스": folder(f"{os.getpid()}_sametime", f"{os.getpid()} {psutil.Process().create_time()}"),
        "ROOT 안의 파일": temp.ROOT / f"{gone.pid}_fileonly",
    }
    kept["ROOT 안의 파일"].write_text("x", encoding="utf-8")
    outside = ROOT / "outside"  # ROOT 밖의 폴더: 정션으로 가리켜도, 남은 폴더 안에서 가리켜도 지워지면 안 된다.
    outside.mkdir()
    (outside / "keep.txt").write_text("x", encoding="utf-8")
    (outside / temp.MARK).write_text(dead, encoding="ascii")
    link, inner = temp.ROOT / f"{gone.pid}_junction", left / "inner"
    made = [subprocess.run(["cmd", "/c", "mklink", "/J", str(at), str(outside)], capture_output=True).returncode for at in (link, inner)]
    reused = folder(f"{os.getpid()}_reusedid", f"{os.getpid()} 1.0")  # 번호는 살아 있지만 시작 시각이 다르면 번호를 다시 쓴 것
    temp.sweep()
    check("정션을 만들 수 있었음", made, [0, 0])
    check("끝난 프로세스가 남긴 폴더만 지움(번호를 다시 쓴 경우 포함)", (left.exists(), reused.exists()), (False, False))
    check("모르는 것은 모두 그대로 둠", {name: at.exists() for name, at in kept.items()}, {name: True for name in kept})
    check("정션은 지우지 않고, 남은 폴더 안의 정션이 가리키는 ROOT 밖의 내용도 그대로", (os.path.isjunction(link), (outside / "keep.txt").exists()), (True, True))
    check("만든 폴더는 ROOT 바로 아래에 있고 표식이 있음", (mine.parent == temp.ROOT, (mine / temp.MARK).read_text(encoding="ascii").split()[0]), (True, str(os.getpid())))
    os.rmdir(link)
    shutil.rmtree(ROOT, ignore_errors=True)
print(f"\n확인 {len(done)}개, 실패:", ", ".join(fails) or "없음")
sys.exit(1 if fails else 0)
