"""5차 감사 묶음 5P(전처리·좌표) 시험. 값은 시험 안에서 지어내고 손으로 계산했다."""
import itertools
import math
import shutil
import subprocess
from pathlib import Path

import polars as pl
import pytest

from lazy import ibis

from core import held, prep_ops
from geo import geojson
from prep import prep_app, prep_expr

ROOT = Path(__file__).resolve().parent.parent
NAN, INF = float("nan"), float("inf")


def box(x0, x1, y0=0, y1=10):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


# ---------- 5P1 ±180에서 자른 도형 ----------

CROSSES = [  # (경도들, 변경선을 건너 모인 것인가) 손으로 정한 표
    ([-180, 180], False),  # 세계 상자의 두 경도: 같은 경도 하나
    ([-180, 0, 180], False),  # 성긴 도형
    ([-100, 100], False),  # 넓은 선
    ([178, -179], True),  # 피지
    ([170, 180, -180, -170], True),  # ±180에서 자른 피지(둘 다 가진다)
    ([170, 180, -170], True),
    ([-180, -170, 170, 180], True),
    ([-180, 180, -90, 90], False),  # 세계를 덮는 점들
    ([5], False), ([], False), ([10, 20, 30], False),
    ([179.5, -179.5, 178], True),
    ([179, -179, NAN], True),  # NaN 좌표가 판단을 숨기지 않는다
    ([NAN, NAN], False),
]


@pytest.mark.parametrize("xs,want", CROSSES)
def test_crosses_dateline_table(xs, want):
    assert geojson.crosses_dateline(xs) is want


def test_world_box_ring_is_not_dateline():
    g = {"type": "Polygon", "coordinates": [box(-180, 180, -90, 90)]}
    assert geojson.bounds(g) == (-180, -90, 180, 90)
    assert geojson.center(g) == (0.0, 0.0)


def test_rfc7946_split_multipolygon_and_ring():
    split = {"type": "MultiPolygon", "coordinates": [[box(170, 180)], [box(-180, -170)]]}
    assert geojson.bounds(split) == (170, 0, 190, 10)
    assert geojson.center(split) == (180.0, 5.0)  # 한가운데가 변경선(아프리카 0이 아니다)
    ring = {"type": "Polygon", "coordinates": [[[170, 0], [180, 0], [180, 10], [-180, 10], [-170, 10], [-170, 0], [170, 0]]]}
    low = geojson.bounds(ring)
    assert low[0] == 170 and low[2] == 190
    row = geojson.table({"type": "Feature", "properties": {}, "geometry": split}).to_dicts()[0]
    assert (row["min_lon"], row["max_lon"]) == (170, -170) and row["center_lon"] == 180.0


def test_lon_0_to_360_file_keeps_its_range():
    for xs, want in (((0, 359), (0, 359)), ((10, 350), (10, 350)), ((190, 200), (190, 200))):
        g = {"type": "Polygon", "coordinates": [box(*xs)]}
        row = geojson.table({"type": "Feature", "properties": {}, "geometry": g}).to_dicts()[0]
        assert (row["min_lon"], row["max_lon"]) == want, xs


# ---------- 5P2 computes_stats ----------

@pytest.mark.parametrize("recipe,want", [
    ({"sorts": ["x"]}, True),
    ({"formulas": [{"name": "z", "expr": "x - mean(x)"}]}, True),
    ({"formulas": [{"name": "z", "expr": "x - 1"}]}, False),
    ({"formulas": [{"name": "z", "expr": "iif(x > 1, 1, 0)"}]}, False),
    ({"columns": {"x": {"ops": {"fill": {"how": "prev", "by": "d"}}}}}, True),
    ({"columns": {"x": {"ops": {"fill": {"how": "next", "by": "d"}}}}}, True),
    ({"columns": {"s": {"ops": {"counts": {}}}}}, True),
    ({"columns": {"x": {"ops": {"bins": [0, 1, 2]}}}}, False),
    ({"columns": {"x": {"ops": {"fill": {"how": "value", "value": 1}}}}}, False),
    ({"sorts": []}, False),
])
def test_computes_stats_full_reads(recipe, want):
    assert prep_app.computes_stats(recipe) is want


# ---------- 5P3 inf도 빈 값으로 ----------

DATA = {"c": [1.0, INF, 3.0, 4.0, 5.0, None, 7.0, 8.0], "k": list(range(8))}


def run(kind, recipe, data=None):
    data = data or DATA
    if kind == "duckdb":
        t, con = ibis.memtable(pl.DataFrame(data)), ibis.duckdb.connect()
    else:
        con = ibis.polars.connect()
        t = con.create_table("t", pl.DataFrame(data), overwrite=True)
    rows = prep_expr.shape(con, {"base": "t", **recipe}, table=t)[0].to_polars().to_dicts()
    return sorted(rows, key=lambda r: r["k"])


@pytest.mark.parametrize("kind", ["duckdb", "polars"])
def test_inf_is_empty_for_fill_and_scale(kind):
    mean = (1 + 3 + 4 + 5 + 7 + 8) / 6
    got = [r["c"] for r in run(kind, {"columns": {"c": {"ops": {"fill": {"how": "mean"}}}}})]
    assert got == pytest.approx([1, mean, 3, 4, 5, mean, 7, 8])
    got = [r["c"] for r in run(kind, {"columns": {"c": {"ops": {"scale": "standard"}}}})]
    assert got[1] is None and got[5] is None
    assert all(math.isfinite(v) for v in got if v is not None)
    real = [v for v in got if v is not None]
    assert sum(real) == pytest.approx(0, abs=1e-9)


def test_nan_to_null_inf_expression():
    t = ibis.memtable(pl.DataFrame({"c": [1.0, INF, -INF, NAN, None]}))
    got = ibis.duckdb.connect().execute(prep_ops.nan_to_null(t["c"]).name("c")).tolist()
    assert got[0] == 1.0 and all(v is None or v != v for v in got[1:])


# ---------- 5P4 컬럼 순서, outlier 없을 때 ----------

ORDER_DATA = {"s": ["1", "2", "3", "x", "100", None, "5", "6", "7"], "b": [1.0, None, 3.0, 4.0, 5.0, 6.0, None, 8.0, 9.0], "k": list(range(1, 10))}
SPECS = {
    "s": {"ops": {"type": {"to": "float"}, "flag": True, "fill": {"how": "mean"}, "outlier": {"how": "drop", "by": "iqr"}, "scale": "minmax"}},
    "b": {"ops": {"fill": {"how": "mean"}, "outlier": {"how": "drop", "by": "std", "k": 1}}},
    "k": {"ops": {"fill": {"how": "drop"}}},
}


@pytest.mark.parametrize("kind", ["duckdb", "polars"])
def test_column_order_does_not_change_result(kind):
    outs = [run(kind, {"columns": {c: SPECS[c] for c in order}}, ORDER_DATA) for order in itertools.permutations(SPECS)]
    assert all(o == outs[0] for o in outs) and len(outs[0]) == 5


@pytest.mark.parametrize("kind", ["duckdb", "polars"])
def test_no_outlier_fill_type_flag_order_independent(kind):
    specs = {"s": {"ops": {"type": {"to": "float"}, "flag": True, "fill": {"how": "mean"}}}, "b": {"ops": {"fill": {"how": "drop"}}}, "k": {"ops": {"scale": "minmax"}}}
    outs = [run(kind, {"columns": {c: specs[c] for c in order}}, ORDER_DATA) for order in itertools.permutations(specs)]
    assert all(o == outs[0] for o in outs)


def test_unwrapped_unmoved_points_keep_values():
    assert geojson.bounds({"type": "Polygon", "coordinates": [box(0, 359)]}) == (0, 0, 359, 10)


def test_apply_ops_docstring_matches_behavior():
    doc = prep_ops.apply_ops.__doc__
    assert "mean·median·mode" in doc and "상수·prev·next" in doc and "outlier가 없으면 base는 쓰지 않는다" in doc


# ---------- 5P4 cached_key는 가장 최근 사본 ----------

def test_cached_key_picks_most_recent(monkeypatch):
    saved = dict(held.store["items"])
    held.store["items"].clear()
    try:
        monkeypatch.setattr(prep_app, "table_key", lambda b: "T")
        for skipped in (["x"], [], ["y"]):  # 오래된 것부터
            r = {"base": "t", "columns": {c: {"keep": False} for c in skipped}}
            held.store["items"][prep_app.copy_key(r)] = ("n" + "".join(skipped), 5)
        held.store["items"].move_to_end(prep_app.copy_key({"base": "t", "columns": {}}))  # 아무것도 안 뺀 사본을 가장 최근에 썼다
        want = {"base": "t", "columns": {"x": {"keep": False}, "y": {"keep": False}}}
        assert prep_app.cached_key(want)[3] == ()
    finally:
        held.store["items"].clear()
        held.store["items"].update(saved)


# ---------- 5P4 tileRange 감싸기(node) ----------

TILE_SCRIPT = """
const assert = require("assert");
%(source)s
const n = 8;
const over = tileRange(mapFrame([170, 0, 190, 10], 640), 3);  // 180을 넘는 쪽은 감싸 이어 붙인다: 타일 번호가 n 이상
assert.strictEqual(over.n, n); assert.strictEqual(over.x0, 7); assert.ok(over.x1 >= n);
const urls = tilesFor(mapFrame([170, 0, 190, 10], 640), 3).map(t => t.url.split("/")[4]);
assert.ok(urls.includes("7") && urls.includes("0") && urls.every(x => +x >= 0 && +x < n));
const world = tileRange(mapFrame([-190, -80, 190, 80], 640), 2);  // 세계를 양쪽으로 넘으면 한 바퀴만
assert.strictEqual(world.x0, 0); assert.strictEqual(world.x1, 3);
const plain = tileRange(mapFrame([10, 0, 20, 10], 640), 3);  // 넘치지 않으면 그대로
assert.ok(plain.x0 >= 0 && plain.x1 < n && plain.x0 <= plain.x1);
console.log("ok");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_tile_range_wraps_past_180():
    source = (ROOT / "static" / "geo" / "geomap.js").read_text(encoding="utf-8")
    done = subprocess.run(["node", "-e", TILE_SCRIPT % {"source": source}], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-1500:]
