"""예전 버전이 남긴 상태 파일을 오류 없이 읽고 새 칸을 기본값으로 채우는지, 덮어쓰기 전에 .bak을 남기는지."""
import json

from collect import collect_config
from collect import collect_limits
from collect import load_kafka
from integrations import schedule
from core import settings
from core import use_counts


def write_legacy(folder, name, data):
    (folder / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_legacy_recent_becomes_connection(client, folder):
    legacy = {"kind": "url", "url": "postgres://db.example.invalid/sales", "user": "analyst"}
    write_legacy(folder, "recent.json", legacy)
    assert client.get("/api/recent").get_json()["recent"] == legacy
    assert client.get("/api/connections").get_json()["connections"] == [{"info": legacy, "count": 1, "last": ""}]
    client.post("/api/connect", json={"kind": "url", "url": legacy["url"], "user": "analyst", "password": "x"})
    assert client.get("/api/connections").get_json()["connections"][0]["count"] == 2  # 옮겨 담은 것에 이어서 센다
    assert json.loads((folder / "recent.json").read_text(encoding="utf-8")) == legacy  # 예전 파일은 지우지 않는다


def test_legacy_settings_and_configs_get_defaults(folder):
    write_legacy(folder, "settings.json", {"theme": "dark", "removed_option": 1})
    assert settings.load() == {"theme": "dark"}
    old_collection = {"version": 1, "mode": "api", "request": {"url": "https://api.example.invalid/items"}}  # web·load·plain이 없던 때의 구성
    found = collect_config.normalize(old_collection)
    assert found["load"]["target"] == "file" and found["plain"] == [] and found["web"]["robots"] is True
    assert found["request"]["url"] == old_collection["request"]["url"]
    assert schedule.public({"every": "daily", "time": "07:00", "task": "x"}) == {**schedule.DEFAULT, "every": "daily", "time": "07:00"}
    assert load_kafka.checked({"servers": "h:9092", "topic": "t"})["ca"] == ""


def test_broken_or_odd_files_do_not_crash(folder):
    write_legacy(folder, "usage.json", {"searches": {"a": {"count": "많이"}, "b": "이상한 값"}, "connections": []})
    assert use_counts.order("searches", ["a", "b"]) == ["a", "b"]
    assert use_counts.connections() == []


def test_backup_before_overwrite(folder):
    settings.write("searches.json", {"v": 1})
    assert not (folder / "searches.json.bak").exists()  # 처음 만들 때는 남길 원본이 없다
    settings.write("searches.json", {"v": 2})
    assert json.loads((folder / "searches.json.bak").read_text(encoding="utf-8")) == {"v": 1}
    (folder / "searches.json").write_text("{깨진", encoding="utf-8")
    assert settings.read("searches.json") == {"v": 1}  # 깨지면 .bak을 읽는다
    settings.write("searches.json", {"v": 3})
    assert json.loads((folder / "searches.json.bak").read_text(encoding="utf-8")) == {"v": 1}  # 깨진 것으로 성한 .bak을 덮지 않는다
    assert settings.read("searches.json") == {"v": 3}


def test_limit_usage_backup(folder):
    collect_limits.write({"g": {"sent": [1.0], "day": [0, 1]}})
    collect_limits.write({"g": {"sent": [1.0, 2.0], "day": [0, 2]}})
    assert json.loads((folder / (collect_limits.USAGE + ".bak")).read_text(encoding="utf-8"))["g"]["day"] == [0, 1]
