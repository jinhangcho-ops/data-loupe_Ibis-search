"""전처리의 좌표 단계(recipe.geo): 이 PC에서 계산하는 새 컬럼 만들기. 단계는 [{op, …}] 목록이고 계산 컬럼 뒤에 차례로 붙는다.
- tm: x·y 컬럼(TM, 미터)을 epsg 좌표계에서 경도·위도 새 컬럼으로. {op, x, y, epsg(기본 5179), lon(기본 경도), lat(기본 위도)}
- nearest: 다른 테이블(지점 목록)에서 가장 가까운 행의 이름과 거리(km). {op, lat, lon, table, schema?, ref_lat, ref_lon, label?, out(기본 가까운_지점), dist(기본 가까운_거리km)}
- region: 점이 든 영역의 속성. 영역은 GeoJSON을 연결한 테이블(geometry 컬럼) 또는 GeoJSON 파일 경로. {op, lat, lon, table 또는 path, schema?, geometry?(기본 geometry), take: [영역 컬럼들], prefix(기본 영역_)}
계산은 점이 아니라 서로 다른 (위도, 경도) 쌍마다 한 번만 하고(고유 쌍 수는 MAX_PAIRS까지), 결과를 작은 표로 만들어 원래 식에 왼쪽 조인한다. 같은 좌표의 행은 같은 값이 붙는다.
지점·영역 테이블은 쓸 컬럼만 읽는다. 원격 DB면 읽기 전에 처리 용량 확인을 거친다(prep_app.copy_gate). 원본 테이블은 이 PC의 작업 사본이다."""
import numpy as np
from lazy import ibis

from core import held
from core import parse
from core import prep_derive
from core import source
from geo import geojson, spatial, tm

OPS = ("tm", "nearest", "region")
MAX_STEPS = 10
MAX_PAIRS = 5_000_000  # 고유 (위도, 경도) 쌍의 최대 수
MAX_REFERENCE = 2_000_000  # 지점·영역 행의 최대 수
KEYS = {"tm": {"op", "x", "y", "epsg", "lon", "lat"}, "nearest": {"op", "lat", "lon", "table", "schema", "ref_lat", "ref_lon", "label", "out", "dist"},
        "region": {"op", "lat", "lon", "table", "schema", "path", "geometry", "take", "prefix"}}


def number_column(t, name, what):
    parse.check_name(name, t.columns, what)
    if not t[name].type().is_numeric() or t[name].type().is_boolean():
        raise ValueError(f"{what} '{name}'은 숫자 컬럼이 아니에요. 먼저 숫자로 바꾸세요.")
    return name


def new_names(step, defaults):
    """새 컬럼 이름들(글자 규칙 검사). defaults = {키: 기본 이름}."""
    out = {}
    for key, default in defaults.items():
        name = step.get(key) or default
        if not isinstance(name, str) or not prep_derive.NAME.fullmatch(name):
            raise ValueError(f"새 컬럼 이름은 영문·한글·숫자·밑줄로 쓰고 글자로 시작해야 합니다(63자까지): {name!r}")
        out[key] = name
    if len(set(out.values())) != len(out):
        raise ValueError("새 컬럼 이름이 서로 겹칩니다.")
    return out


def check(steps):
    """단계 목록의 모양 검사(데이터를 읽지 않는다)."""
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise ValueError(f"좌표 단계는 {MAX_STEPS}개까지의 목록이어야 합니다.")
    for step in steps:
        if not isinstance(step, dict) or step.get("op") not in OPS:
            raise ValueError(f"좌표 단계는 op가 {', '.join(OPS)} 중 하나인 객체여야 합니다.")
        if extra := set(step) - KEYS[step["op"]]:
            raise ValueError(f"{step['op']} 단계에 쓸 수 없는 항목이 있습니다: {', '.join(sorted(extra))}")
    return steps


def pairs(t, a, b):
    """t의 서로 다른 (a, b) 값 쌍(둘 다 값이 있는 것)의 Polars 표(열 a, b를 실수로)."""
    import polars as pl
    both = t.filter(t[a].notnull(), t[b].notnull()).select(a=t[a].cast("float64"), b=t[b].cast("float64")).distinct()
    frame = both.limit(MAX_PAIRS + 1).to_polars()
    if len(frame) > MAX_PAIRS:
        raise ValueError(f"서로 다른 좌표가 {MAX_PAIRS:,}개를 넘어 이 PC에서 계산하지 않았어요. 기간·조건으로 줄이세요.")
    return frame.select(pl.col("a"), pl.col("b"))


def attach(t, keys, frame, new):
    """t에 frame(열: __k0, __k1, 새 컬럼들)을 키로 왼쪽 조인한다(키 열은 뺀다). 키가 비어 있거나 frame에 없는 행은 새 컬럼이 빈 값이다."""
    small = ibis.memtable(frame)
    keep = t.columns
    t = t.mutate(__ord=ibis.row_number())  # 조인이 행 순서를 바꾸므로 순번을 붙였다가 조인 뒤 그 순으로 되돌린다
    joined = t.left_join(small, [t[k].cast("float64") == small[f"__k{i}"] for i, k in enumerate(keys)])
    return joined.select(*[joined[c] for c in keep], *[joined[n] for n in new], joined["__ord"]).order_by("__ord").drop("__ord")


def reference(step, refs, columns, what):
    """지점·영역 테이블(또는 GeoJSON 파일)에서 columns만 읽은 Polars 표. refs는 지금 연결(원본)."""
    if step.get("path"):
        frame = geojson.read(step["path"])
        for c in columns:
            parse.check_name(c, frame.columns, f"{what} 컬럼")
        return frame.select(columns)
    if not step.get("table"):
        raise ValueError(f"{what} 테이블을 고르세요.")
    schema = step.get("schema") or None
    parse.check_name(step["table"], source.list_tables(refs, schema), "테이블")
    table = source.get_table(refs, step["table"], schema)
    for c in columns:
        parse.check_name(c, table.columns, f"{what} 컬럼")
    if held.remote(refs):  # 원격 DB: 같은 연결·테이블·컬럼이면 한 번 받은 것을 다시 쓴다(미리보기마다 DB를 읽지 않게). 연결을 바꾸면 held.clear가 버린다.
        from core import server
        name, rows = held.ensure(ref_key(step, columns), table.select(*columns).limit(MAX_REFERENCE + 1), server.state["cancel"], cap=False)
        if rows > MAX_REFERENCE:
            held.forget(ref_key(step, columns))
            raise ValueError(f"{what} 테이블이 {MAX_REFERENCE:,}행을 넘어 이 PC에서 계산하지 않았어요.")
        return held.table(name).select(*columns).to_polars()
    frame = table.select(*columns).limit(MAX_REFERENCE + 1).to_polars()
    if len(frame) > MAX_REFERENCE:
        raise ValueError(f"{what} 테이블이 {MAX_REFERENCE:,}행을 넘어 이 PC에서 계산하지 않았어요.")
    return frame


def ref_key(step, columns):
    """원격 DB에서 받아 둔 지점·영역 표를 가리키는 held 열쇠(스키마·테이블·컬럼)."""
    return ("geo", step.get("schema") or None, step["table"], tuple(columns))


def reference_exprs(steps, refs):
    """처리 용량 확인에 쓸, 각 단계가 읽는 (held 열쇠, 테이블 식)들(쓸 컬럼만). 파일 경로(path)는 DB를 읽지 않으니 뺀다. 테이블·컬럼이 틀리면 ValueError."""
    out = []
    for step in steps or []:
        if step.get("op") == "tm" or step.get("path") or not step.get("table"):
            continue
        schema = step.get("schema") or None
        parse.check_name(step["table"], source.list_tables(refs, schema), "테이블")
        table = source.get_table(refs, step["table"], schema)
        want = [step.get("ref_lat"), step.get("ref_lon"), step.get("label")] if step["op"] == "nearest" else [step.get("geometry") or "geometry", *(step.get("take") or [])]
        for c in want:
            if c:
                parse.check_name(c, table.columns, "컬럼")
        cols = [c for c in dict.fromkeys(want) if c]
        out.append((ref_key(step, cols), table.select(*cols)))
    return out


def chosen(values, index):
    """지점·영역 컬럼 values에서 index(-1이면 없음)번째 값들의 Polars 시리즈."""
    import polars as pl
    got = values.to_list()
    return pl.Series(values.name, [None if i < 0 else got[i] for i in index.tolist()], dtype=values.dtype)


def run_tm(t, step, info):
    x, y = number_column(t, step.get("x"), "x"), number_column(t, step.get("y"), "y")
    epsg = step.get("epsg", 5179)
    if type(epsg) is not int or not 1000 <= epsg <= 99999:
        raise ValueError("좌표계 EPSG 번호는 4자리~5자리 정수입니다(예: 5179).")
    names = new_names(step, {"lon": "경도", "lat": "위도"})
    prep_derive.check_name(names["lon"], t, "경도 컬럼")
    prep_derive.check_name(names["lat"], t, "위도 컬럼")
    found = pairs(t, x, y)
    lon, lat = tm.to_wgs84(found["a"].to_numpy(), found["b"].to_numpy(), epsg)
    import polars as pl
    frame = pl.DataFrame({"__k0": found["a"], "__k1": found["b"], names["lon"]: lon, names["lat"]: lat}).with_columns(pl.col(names["lon"]).fill_nan(None), pl.col(names["lat"]).fill_nan(None))
    bad = int(np.isnan(lon).sum())
    info.append({"op": "tm", "pairs": len(found), "failed": bad, "epsg": epsg})
    return attach(t, [x, y], frame, [names["lon"], names["lat"]]), [names["lon"], names["lat"]]


def run_nearest(t, step, refs, info):
    import polars as pl
    lat, lon = number_column(t, step.get("lat"), "위도"), number_column(t, step.get("lon"), "경도")
    names = new_names(step, {"dist": "가까운_거리km", **({"out": "가까운_지점"} if step.get("label") else {})})
    for n in names.values():
        prep_derive.check_name(n, t, "새 컬럼")
    ref_cols = [step.get("ref_lat"), step.get("ref_lon"), *([step["label"]] if step.get("label") else [])]
    if not all(ref_cols):
        raise ValueError("지점 테이블의 위도·경도 컬럼을 고르세요.")
    ref = reference(step, refs, list(dict.fromkeys(ref_cols)), "지점")
    for c in (step["ref_lat"], step["ref_lon"]):
        if not ref.schema[c].is_numeric():
            raise ValueError(f"지점 테이블의 '{c}'은 숫자 컬럼이 아니에요.")
    found = pairs(t, lat, lon)
    index, dist = spatial.nearest(found["a"].to_numpy(), found["b"].to_numpy(), ref[step["ref_lat"]].cast(pl.Float64).to_numpy(), ref[step["ref_lon"]].cast(pl.Float64).to_numpy())
    columns = {"__k0": found["a"], "__k1": found["b"], names["dist"]: pl.Series(dist).fill_nan(None)}
    if step.get("label"):
        columns[names["out"]] = chosen(ref[step["label"]], index)
    info.append({"op": "nearest", "pairs": len(found), "matched": int((index >= 0).sum()), "reference": len(ref)})
    new = [names["dist"], *([names["out"]] if step.get("label") else [])]
    return attach(t, [lat, lon], pl.DataFrame(columns), new), new


def run_region(t, step, refs, info):
    import polars as pl
    lat, lon = number_column(t, step.get("lat"), "위도"), number_column(t, step.get("lon"), "경도")
    take = step.get("take") or []
    if not isinstance(take, list) or not all(isinstance(c, str) for c in take):
        raise ValueError("붙일 영역 컬럼(take)은 컬럼 이름 목록이어야 합니다.")
    prefix = step.get("prefix", "영역_")
    if not isinstance(prefix, str) or len(prefix) > 30:
        raise ValueError("영역 컬럼 앞에 붙일 글자(prefix)는 30자까지입니다.")
    geometry = step.get("geometry") or "geometry"
    names = [prefix + c for c in take]
    for n in names:
        prep_derive.check_name(n, t, "영역 컬럼")
    if len(set(names)) != len(names):
        raise ValueError("붙일 영역 컬럼이 겹칩니다.")
    ref = reference(step, refs, list(dict.fromkeys([geometry, *take])), "영역")
    regions = [[[spatial.closed(r) for r in poly] for poly in geojson.polygons(geojson.parse_geometry(text))] for text in ref[geometry].to_list()]
    if not any(regions):
        raise ValueError(f"영역 테이블의 '{geometry}' 컬럼에서 폴리곤을 찾지 못했어요. GeoJSON을 연결한 테이블의 geometry 컬럼을 고르세요.")
    found = pairs(t, lat, lon)
    index = spatial.assign(found["a"].to_numpy(), found["b"].to_numpy(), regions)
    columns = {"__k0": found["a"], "__k1": found["b"]}
    for c, n in zip(take, names):
        columns[n] = chosen(ref[c], index)
    if not take:  # 붙일 컬럼을 안 골랐으면 영역 번호(영역 테이블의 행 순서, 0부터)라도 붙인다
        names = [prefix + "번호"]
        prep_derive.check_name(names[0], t, "영역 컬럼")
        columns[names[0]] = pl.Series([None if i < 0 else int(i) for i in index.tolist()], dtype=pl.Int64)
    info.append({"op": "region", "pairs": len(found), "matched": int((index >= 0).sum()), "regions": len(regions)})
    return attach(t, [lat, lon], pl.DataFrame(columns), names), names


def apply(t, steps, info=None, refs=None):
    """steps를 차례로 붙인다. (식, 만든 컬럼 이름들). info(목록)를 주면 단계마다 계산한 고유 좌표 수·맞춘 수를 남긴다. refs는 지점·영역 테이블을 읽을 연결."""
    info = [] if info is None else info
    made = []
    for step in check(steps):
        if step["op"] == "tm":
            t, new = run_tm(t, step, info)
        elif step["op"] == "nearest":
            t, new = run_nearest(t, step, refs, info)
        else:
            t, new = run_region(t, step, refs, info)
        made += new
    return t, made
