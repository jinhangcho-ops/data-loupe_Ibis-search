"""사용 횟수: 연결과 저장한 검색을 많이 쓴 순(같으면 최근에 쓴 순)으로 보이는지."""
import use_counts


def test_ranked_by_count_then_recent(folder):
    use_counts.use("searches", "a")
    for name in ("b", "b", "c", "c"):
        use_counts.use("searches", name)
    assert use_counts.order("searches", ["a", "b", "c", "never"]) == ["c", "b", "a", "never"]


def test_connections_ranked(client, folder):
    for path in ("first", "second", "second", "third", "second", "third"):
        assert client.post("/api/connect", json={"kind": "folder", "path": str(folder / path), "file": "x.duckdb"}).status_code == 200
    listed = client.get("/api/connections").get_json()["connections"]
    assert [(c["info"]["path"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1], c["count"]) for c in listed] == [("second", 3), ("third", 2), ("first", 1)]
    assert client.get("/api/recent").get_json()["recent"]["path"].endswith("third")  # 칸은 마지막으로 쓴 연결로 채운다


def test_connection_list_is_capped(client, folder):
    for n in range(use_counts.MAX_CONNECTIONS + 5):
        client.post("/api/connect", json={"kind": "folder", "path": str(folder / f"p{n}"), "file": "x.duckdb"})
    assert len(client.get("/api/connections").get_json()["connections"]) == use_counts.MAX_CONNECTIONS


def test_saved_searches_ranked(client):
    for name in ("one", "two", "three"):
        client.post("/api/searches", json={"name": name, "search": {"base": "orders"}})
    assert client.get("/api/searches").get_json()["order"] == ["one", "three", "two"]  # 쓴 적 없으면 이름 순
    client.post("/api/searches/use", json={"name": "two"})
    client.post("/api/searches/use", json={"name": "two"})
    shown = client.post("/api/searches/use", json={"name": "three"}).get_json()
    assert shown["order"] == ["two", "three", "one"]
    assert client.post("/api/searches", json={"name": "two", "search": None}).get_json()["order"] == ["three", "one"]
    client.post("/api/searches", json={"name": "two", "search": {"base": "orders"}})
    assert client.get("/api/searches").get_json()["order"] == ["three", "one", "two"]  # 지웠다 다시 만들면 0번부터
    assert "error" in client.post("/api/searches/use", json={"name": "없는 이름"}).get_json()
