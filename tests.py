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
ROOT = Path(tempfile.mkdtemp(prefix="ibis_search_tests_"))
tempfile.tempdir = str(ROOT)  # 프로그램이 만드는 임시 폴더(내보내기)도 이 안에 생긴다.
os.environ["APPDATA"] = str(ROOT / "appdata")
sys.dont_write_bytecode = True  # 프로젝트 폴더에 __pycache__를 남기지 않는다.

import duckdb
from flask import Flask
import polars as pl
import psutil

import notify
import prep_app
import prep_ops
import prep_profile
import settings
import source
from app import KEY, app
import server
from server import state

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


def prep_text(tag, pc, got, error, only, preview, values):
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
        ("round 음수", "'amt' 컬럼의 'round' 값은 0 이상의 정수 형식으로 쓰세요.", bad("amt", round=-1)),
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


def prep_sampling(pc, got, error):
    """통계·모양 검사를 앞 sample행으로 줄이는 것. 같은 검사를 DuckDB 파일 연결과 Parquet 연결에 돌린다."""
    duck = got("/api/files", path=str(SAMPLING))["files"][0]
    flat = got("/api/files", path=str(SAMPLING_FLAT))["flat"]
    calls, original = [], prep_profile.measure
    prep_profile.measure = lambda t, wanted: (calls.append(wanted), original(t, wanted))[1]  # 모양을 검사한 조회를 센다.
    try:
        for tag, connect in [("DuckDB", dict(kind="folder", path=str(SAMPLING), file=duck)), ("Parquet", dict(kind="folder", path=str(SAMPLING_FLAT), files=flat))]:
            print("== 앞 sample행으로 거르기:", tag)
            got("/api/connect", **connect)
            r = got("/api/shape", base="wide")
            check(tag + " /api/shape", (r["rows"], [(c["name"], c["type"], c["kind"]) for c in r["columns"]]),
                  (3000, [("n", "int64", "int"), ("num_all", "string", "string"), ("num_late", "string", "string"), ("bad_head", "string", "string"),
                          ("date_late", "string", "string"), ("kor", "string", "string")]))
            check(tag + " sample 범위·형식 거부", [error(api, base="wide", sample=v) for api in ("/api/profile", "/api/patterns") for v in (999, 1_000_001, "5000", 1500.0)],
                  ["미리 볼 행 수(sample)는 1,000 이상 1,000,000 이하의 정수로 입력하세요."] * 8)
            check(tag + " sample 안 주면 기본값, 경계값은 됨", ("rows" in got("/api/profile", base="wide"), "rows" in got("/api/profile", base="wide", sample=1000),
                  "columns" in got("/api/patterns", base="wide", sample=1_000_000)), (True, True, True))
            kor = next(c for c in got("/api/profile", base="wide", sample=1000)["columns"] if c["name"] == "kor")
            check(tag + " 한글 값의 글자 수(바이트 수가 아님), len_sampled는 글자 수를 못 세는 연결에만", ({k: kor[k] for k in ("min_len", "max_len")}, kor.get("len_sampled")),
                  ({"min_len": 3, "max_len": 5}, True if tag == "Parquet" else None))
            calls.clear()
            r = got("/api/patterns", base="wide", sample=1000)["columns"]
            check(tag + " patterns: 앞부분만 맞는 컬럼은 전체에서 걸러짐, 앞이 모두 빈 값이면 전체에서 확인해 추천", {k: [s["ops"] for s in v] for k, v in r.items()},
                  {"num_all": [{"type": {"to": "int"}}], "date_late": [{"type": {"to": "date", "format": "%Y%m%d"}}]})
            check(tag + " patterns: why의 개수는 전체 기준", [s["why"] for k in ("num_all", "date_late") for s in r[k]],
                  ["빈 값과 빈 값 표시를 뺀 3,000개가 모두 숫자로 바뀝니다.", "빈 값과 빈 값 표시를 뺀 1,500개가 모두 YYYYMMDD 모양입니다."])
            check(tag + " patterns: 앞에서 걸러진 컬럼(bad_head)은 전체 조회에 넣지 않고, 전체에서 틀린 컬럼(num_late)만 다음 모양을 확인", [sorted(w) for w in calls],
                  [["bad_head", "date_late", "kor", "num_all", "num_late"], ["date_late", "num_all", "num_late"], ["num_late"]])
            calls.clear()
            check(tag + " patterns: 후보가 없으면 전체 조회를 보내지 않음", (got("/api/patterns", base="bad", sample=1000), len(calls)), ({"columns": {}}, 1))
    finally:
        prep_profile.measure = original
    got("/api/disconnect")


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
        ("정수로(int64 범위 밖은 빈 값)", "edge", {**to("int"), "fill": {"how": "value", "value": 0}}, "int64", [TOP, 0, -TOP, 12, 0, 0]),
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
    for name, column, ops in unsupported:
        check(f"SQLite가 못 하는 계산은 그 문구로: {name}", str(values(**{column: ops})).startswith("이 연결에서는 지원하지 않는 계산입니다."), True)
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
            check(f"{tag} profile: {w['name']}", {k: v for k, v in g.items() if k not in ("suggest", "len_sampled")}, w)
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
        prep_text(tag, pc, got, error, only, preview, values)

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
          "Parquet·CSV 파일 연결에는 테이블을 만들 DB가 없습니다. 파일로 저장하세요.")
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
    prep_sampling(pc, got, error)


def notify_section(c, got):
    """다른 앱으로 보내기. 실제로 요청을 보내지 않고 notify.post를 바꿔 끼워 무엇이 나가는지만 본다."""
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

    real, token, hook = notify.post, "xoxb-secret-token-123", "https://hooks.example.com/services/secret-path-456"
    notify.post = fake
    try:
        r = c.get("/api/destinations").get_json()
        check("보낼 곳 종류 표: 라벨·그림·칸(비밀 여부)", ({k: (v["label"], v["image"], [(f["name"], f["secret"]) for f in v["fields"]]) for k, v in r["kinds"].items()}, r["destinations"]),
              ({"slack": ("Slack", True, [("token", True), ("channel", False)]), "webhook": ("웹훅(주소로 보내기)", True, [("url", True), ("content", False), ("template", False)])}, {}))
        webhook = r["kinds"]["webhook"]
        check("webhook의 선택지·기본값·여러 줄 칸·기본 틀", ([f.get("options") for f in webhook["fields"]][1], [f.get("default") for f in webhook["fields"]][1], webhook["fields"][2].get("multiline"),
              [p["label"] for p in webhook["presets"]]), ([["json", "JSON"], ["text", "글자"], ["form", "폼(URL 인코딩)"]], "json", True, ["일반 JSON", "Teams", "Discord", "글자"]))
        check("종류 표에 검사 함수 같은 내부 값이 새지 않음", all(set(f) <= {"name", "label", "secret", "options", "default", "multiline", "hint"} for k in r["kinds"].values() for f in k["fields"]), True)

        print("== 보낼 곳 저장(비밀 값은 암호화)")
        r = put("slack", kind="slack", done=True, token=token, channel="C0123")
        check("저장: 목록에 비밀 값이 없고 저장돼 있다는 true만", r["destinations"], {"slack": {"kind": "slack", "done": True, "token": True, "channel": "C0123"}})
        raw = (settings.PATH.parent / "destinations.json").read_bytes()
        check("destinations.json에 토큰이 평문으로 없음", (token.encode() in raw, b"xoxb" in raw, b"C0123" in raw), (False, False, True))
        stored = settings.named("destinations.json")["slack"]["token"]
        check("암호화한 것을 풀면 같은 값, 글자(한글 포함)도 같음", (notify.reveal(stored), notify.reveal(notify.protect("가나다 abc")), "가나다" in notify.protect("가나다 abc")), (token, "가나다 abc", False))
        r = put("slack", kind="slack", done=False, token="", channel="C999")
        check("고칠 때 비밀 칸을 비우면 저장된 값을 그대로 둠", (r["destinations"]["slack"], notify.reveal(settings.named("destinations.json")["slack"]["token"])),
              ({"kind": "slack", "done": False, "token": True, "channel": "C999"}, token))
        check("새로 만드는데 비밀 칸이 없으면 거부", error("/api/destinations", name="새것", destination={"kind": "slack", "done": False, "channel": "C1"}), "'봇 토큰' 칸을 입력하세요.")
        data = settings.named("destinations.json")
        data["slack"]["token"] = "AAAA"  # 다른 PC·계정에서 복사해 온 것처럼 풀 수 없는 값
        settings.write("destinations.json", data)
        check("풀 수 없는 비밀 값은 false(다시 입력 필요)", c.get("/api/destinations").get_json()["destinations"]["slack"]["token"], False)
        check("풀 수 없는데 비밀 칸을 비우면 거부, 보내기도 거부", (error("/api/destinations", name="slack", destination={"kind": "slack", "done": False, "token": "", "channel": "C999"}),
              error("/api/send", name="slack", title="t")), ("'봇 토큰' 칸을 입력하세요.", "'slack'의 비밀 값을 읽을 수 없습니다(다른 PC·계정에서 온 파일). 보낼 곳을 다시 저장하세요."))
        put("slack", kind="slack", done=False, token=token, channel="C999")
        check("다시 입력하면 true", c.get("/api/destinations").get_json()["destinations"]["slack"]["token"], True)
        check("검증 거부: 종류·칸·done·https·본문 종류", [error("/api/destinations", name="x", destination=d) for d in (
              {"kind": "mail"}, {"kind": "slack", "token": "a", "channel": "C", "extra": 1}, {"kind": "slack", "token": "a", "channel": "C", "done": "yes"},
              {"kind": "webhook", "url": "http://hooks.example.com/a", "content": "json", "template": "{}"},
              {"kind": "webhook", "url": "https://h.example.com/a", "content": "xml", "template": "{}"},
              {"kind": "webhook", "url": "https://h.example.com/a", "content": "json", "template": " "})],
              ["보낼 곳의 종류는 slack, webhook 중 하나입니다.", "보낼 곳에 쓸 수 없는 값이 있습니다: extra", "보낼 곳에 쓸 수 없는 값이 있습니다: done", "주소는 https://로 시작해야 합니다.",
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
        notify.post = real
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


def memory_section(c, got, duck):
    """데이터 라이브러리 지연 로딩과 DuckDB 파일 조회의 작업 메모리 상한."""
    print("== 지연 로딩·메모리 상한")
    error = lambda api, **body: got(api, **body).get("error", "")
    code = """
import json, sys
sys.path.insert(0, sys.argv[1]); sys.dont_write_bytecode = True
import lazy, server, app, prep_app, notify
names = ("ibis", "polars", "duckdb", "pyarrow")
is_lazy = lambda: {n: type(sys.modules[n]).__name__ == "_LazyModule" for n in names}
out = {"imported": is_lazy()}
c = server.app.test_client(); c.set_cookie("key", server.KEY)
out["connect"] = c.post("/api/connect", json={"kind": "folder", "path": sys.argv[2], "files": [sys.argv[3]]}).get_json()
out["parquet"] = is_lazy()
print(json.dumps(out))
"""
    flat = PREP_FLAT / "messy.parquet"
    found = subprocess.run([sys.executable, "-c", code, str(Path(__file__).parent), str(PREP_FLAT), str(flat)], capture_output=True, text=True, encoding="utf-8")
    out = json.loads(found.stdout.strip().splitlines()[-1]) if found.returncode == 0 else {"error": found.stderr[-300:]}
    check("프로젝트 모듈을 불러온 직후에는 ibis·polars·duckdb·pyarrow가 모두 지연 상태", out.get("imported"), dict.fromkeys(("ibis", "polars", "duckdb", "pyarrow"), True))
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
          "rows": [{"order_id": o["order_id"], "name": names.get(o["customer_id"])} for o in mine[:3]]}})
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
    check("SQLite counts는 지원하지 않는 계산", got("/api/preview", base="customers", columns={"name": {"ops": {"counts": {"sep": "r"}}}}).get("error", "")[:22], "이 연결에서는 지원하지 않는 계산입니다.")
    check("SQLite 새 테이블 만들기", (got("/api/run", recipe=recipe, target={"kind": "table", "name": "upper_names"}), tables()),
          ({"ok": True, "rows": 40}, ["customers", "orders", "upper_names"]))
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
    for path in ("/", "/search", "/prep"):
        r = c.get(path + "?key=" + KEY)
        nonce = re.search(r"script-src 'nonce-([^']+)'", r.headers.get("Content-Security-Policy", ""))
        check(f"{path} 화면의 스크립트는 모두 CSP의 nonce를 가짐", (r.status_code, r.text.count("<script") == r.text.count(f'<script nonce="{nonce and nonce.group(1)}"') > 0), (200, True))
    static = ROOT / "static"  # 지어낸 파일로 확인한다. 실제 static은 건드리지 않는다.
    static.mkdir()
    (static / "frag.html").write_text("<p>조각</p>", encoding="utf-8")
    (static / "page.html").write_text("<!--include frag.html--><script>1</script>", encoding="utf-8")
    with Flask(__name__, static_folder=str(static)).app_context():
        html = server.page("page.html").get_data(as_text=True)
    check("page: include를 내용으로 바꾸고 그 안의 스크립트에도 nonce를 붙임", ("include" in html, "<p>조각</p>" in html, "<script nonce=" in html), (False, True, True))
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
          "rows": [{"order_id": o["order_id"], "name": name_of.get(o["customer_id"])} for o in top]}})
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
    check("Excel: 수식 값 앞에 '", "'=1+1" in zipfile.ZipFile(io.BytesIO(export("xlsx"))).read("xl/sharedStrings.xml").decode("utf-8"), True)
    check("Parquet: 값을 그대로", pl.read_parquet(io.BytesIO(export("parquet")))["note"].to_list(), [o["note"] for o in orders])
    check("모르는 형식은 거절", error("/api/export", base="orders", format="txt")[:6], "파일 이름은")

    print("== 설정·저장한 검색·사용량")
    check("/api/usage", sorted(c.get("/api/usage").get_json()), ["cpu", "memory", "memory_mb", "pc_cpu", "pc_memory"])
    check("설정 저장", (got("/api/settings", theme="dark"), settings.load()), ({"ok": True}, {"theme": "dark"}))
    check("쓸 수 없는 설정 값은 거절", (error("/api/settings", theme="blue")[:11], settings.load()), ("'theme' 설정에", {"theme": "dark"}))
    saved = {"base": "orders", "conditions": ["amount > 100"], "limit": "20"}
    check("검색 저장", got("/api/searches", name="mine", search=saved), {"searches": {"mine": saved}})
    got("/api/searches", name="mine", search={"base": "customers"})
    check("같은 이름은 덮어씀", c.get("/api/searches").get_json(), {"searches": {"mine": {"base": "customers"}}})
    check("검색 지우기", got("/api/searches", name="mine", search=None), {"searches": {}})
    check("빈 이름은 거절", error("/api/searches", name=" ", search=saved), "검색 이름은 1자 이상 50자 이하로 입력하세요.")
    recipe = {"base": "messy", "columns": {"name": {"ops": {"trim": True}}}, "sorts": ["id"]}
    check("구성 목록은 처음에 비어 있음", c.get("/api/recipes").get_json(), {"recipes": {}})
    check("구성 저장(이름 앞뒤 공백은 뗌)", got("/api/recipes", name=" 정리 ", recipe=recipe), {"recipes": {"정리": recipe}})
    check("구성과 검색은 서로 섞이지 않음", (c.get("/api/searches").get_json(), got("/api/searches", name="s", search={"base": "orders"})["searches"], c.get("/api/recipes").get_json()),
          ({"searches": {}}, {"s": {"base": "orders"}}, {"recipes": {"정리": recipe}}))
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
        return lambda name: (asked.append(name), path)[1]

    body = dict(base="orders", conditions=["note = =1+1"], columns=["order_id, note"], sorts=["order_id"])
    mine = [o for o in orders if o["note"] == "=1+1"]
    got("/api/connect", kind="folder", path=str(DATA), file=duck)
    server.ask_save = None
    check("저장 위치를 물을 수 없으면 거부", error("/api/handoff", **body), "저장 위치를 물을 수 없습니다. 프로그램 창에서만 쓸 수 있습니다.")
    server.ask_save = choose(str(out / "picked"))
    check("handoff: 확장자 없으면 .parquet을 붙이고 경로·테이블 이름을 돌려줌", (got("/api/handoff", **body), asked),
          ({"path": str(out / "picked.parquet"), "table": "picked"}, ["orders_search.parquet"]))
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
    server.ask_save = lambda name: None
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
    memory_section(c, got, duck)
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
    import scratch as temp
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
