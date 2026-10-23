"""사전 설정 지표(MetricRegistry): 지표 파일 검사, 분자/분모(분모 0은 빈 값), 나눠 구하기, 사용 횟수 순서, 라우트.
지표 파일과 데이터는 테스트 안에서 지어낸다."""
import duckdb
import polars as pl
import pytest
from lazy import ibis

import metrics

EVENTS = pl.DataFrame({"day": ["d1", "d1", "d1", "d2", "d2", "d3"],
                       "event": ["impression", "impression", "click", "impression", "buy", "buy"],
                       "user": [1, 2, 1, 3, 3, 4], "amount": [0, 0, 0, 0, 500, 300]})
YAML = """
metrics:
  CTR:
    description: 노출 대비 클릭
    format: percent
    numerator: {agg: count, where: "event = click"}
    denominator: {agg: count, where: "event = impression"}
  ARPPU:
    numerator: {agg: sum, column: amount}
    denominator: {agg: nunique, column: user, where: ["event = buy", "amount > 0"]}
  매출:
    numerator: {agg: sum, column: amount}
"""


@pytest.fixture(params=["memory", "polars"])
def t(request):
    return ibis.memtable(EVENTS.to_arrow()) if request.param == "memory" else ibis.polars.connect().create_table("events", EVENTS)


@pytest.fixture
def found(tmp_path):
    path = tmp_path / "metrics.yaml"
    path.write_text(YAML, encoding="utf-8")
    return metrics.read_file(path)


def test_read_and_describe(found):
    assert list(found) == ["CTR", "ARPPU", "매출"]
    assert found["CTR"]["format"] == "percent" and found["매출"]["denominator"] is None
    assert metrics.describe(found["ARPPU"]) == "합계(amount) ÷ 고유 개수(user) [event = buy and amount > 0]"


@pytest.mark.parametrize("text, message", [
    ("metrics: {}", "하나 이상"),
    ("other: 1", "metrics"),
    ("metrics: {A: {denominator: {agg: count}}}", "numerator"),
    ("metrics: {A: {numerator: {agg: bogus, column: x}}}", "agg는"),
    ("metrics: {A: {numerator: {agg: sum}}}", "column이 있어야"),
    ("metrics: {A: {numerator: {agg: count}, format: money}}", "format"),
    ("metrics: {A: {numerator: {agg: count, where: 3}}}", "where"),
    ("metrics: {A: {numerator: {agg: count, expr: 'x'}}}", "모르는 항목"),
    ("metrics: [1, 2", "형식"),
])
def test_bad_files(tmp_path, text, message):
    path = tmp_path / "m.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        metrics.read_file(path)
    with pytest.raises(ValueError, match="yaml"):
        metrics.read_file(tmp_path / "m.txt")


def test_compute(t, found):
    labels, values, more = metrics.compute(t, ["CTR", "ARPPU", "매출"], found)
    assert labels is None and more is False
    assert values == {"CTR": [pytest.approx(1 / 3)], "ARPPU": [400.0], "매출": [800]}
    labels, values, more = metrics.compute(t, ["CTR", "매출"], found, by="day")
    assert labels == ["d1", "d2", "d3"]
    assert values["CTR"] == [0.5, 0.0, None]  # d3은 노출이 없어 분모 0 → 빈 값(오류 없음)
    assert values["매출"] == [0, 500, 300]


def test_compute_errors(t, found):
    with pytest.raises(ValueError, match="지표"):
        metrics.compute(t, ["CTRR"], found)
    with pytest.raises(ValueError, match="고르세요"):
        metrics.compute(t, [], found)
    bad = {"X": metrics.metric("X", {"numerator": {"agg": "sum", "column": "event"}})}
    with pytest.raises(ValueError, match="숫자 컬럼"):
        metrics.compute(t, ["X"], bad)


def test_routes(client, tmp_path, folder):
    """파일 경로 저장, 탐색 조건 위에서 구하기, 많이 쓴 순."""
    import server
    path = tmp_path / "shop.duckdb"
    with duckdb.connect(str(path)) as raw:
        raw.register("df", EVENTS.to_arrow())
        raw.execute("create table events as select * from df")
    server.state.update(con=ibis.duckdb.connect(str(path), read_only=True), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    (tmp_path / "metrics.yaml").write_text(YAML, encoding="utf-8")

    assert client.get("/api/metrics").get_json()["metrics"] == []
    r = client.post("/api/metrics", json={"path": str(tmp_path / "nope.yaml")})
    assert r.status_code == 400 and "읽을 수 없습니다" in r.get_json()["error"]
    d = client.post("/api/metrics", json={"path": str(tmp_path / "metrics.yaml")}).get_json()
    assert [m["name"] for m in d["metrics"]] == ["CTR", "ARPPU", "매출"] and d["metrics"][0]["format"] == "percent"

    run = lambda **body: client.post("/api/metrics/run", json={"base": "events", **body}).get_json()
    got = run(names=["매출"], conditions=["day = d2"])
    assert got["labels"] is None and got["values"] == {"매출": [500]}
    got = run(names=["CTR"], by="day")
    assert got["labels"] == ["d1", "d2", "d3"] and got["values"]["CTR"][2] is None and got["formats"] == {"CTR": "percent"}
    run(names=["CTR"])
    assert client.get("/api/metrics").get_json()["order"] == ["CTR", "매출", "ARPPU"]

    (tmp_path / "metrics.yaml").write_text("metrics: [", encoding="utf-8")  # 파일이 망가지면 이유를 보이고 목록은 비운다
    d = client.get("/api/metrics").get_json()
    assert d["metrics"] == [] and "형식" in d["error"]
