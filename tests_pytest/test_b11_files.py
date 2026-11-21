"""B11 받은 데이터 폴더(plans/dash-blocks.md "B11 세부 명세", dashboard/dash_files.py): 칸 설명(pull.json), 쓰는 대시보드(users), 크기,
대시보드를 지울 때 그 대시보드만 빼고 아무도 안 쓰는 칸만 지우기, 주인 없는 칸, 모르는 것은 남기기. 폴더와 파일은 시험 안에서 만든다."""
import pytest

from dashboard import dash_files

SLOT, OTHER = "0123456789abcdef", "fedcba9876543210"


def manifest(users, parts=("part-000001.parquet",), table="t"):
    return {"v": dash_files.VERSION, "source": "k", "schema": "", "table": table, "virtual": False, "crypt": {"group": "none", "salt": ""}, "columns": None,
            "held": [], "users": {u: {"columns": None, "terms": [], "window": None, "mark": ""} for u in users}, "parts": list(parts), "rows": 1,
            "pulled_at": "2026-11-21T10:00:00", "lost": None}


def make_slot(slot, users, parts=("part-000001.parquet",)):
    dash_files.write_manifest(slot, manifest(users, parts))
    for name in parts:
        (dash_files.root() / slot / name).write_bytes(b"PAR1data")
    return dash_files.root() / slot


def test_manifest_roundtrip_and_broken(folder):
    make_slot(SLOT, ["d1"])
    assert dash_files.read_manifest(SLOT) == manifest(["d1"])
    assert dash_files.read_manifest(OTHER) is None
    (dash_files.root() / SLOT / dash_files.MANIFEST).write_text('{"v": 3}', encoding="utf-8")
    with pytest.raises(ValueError, match="깨졌어요"):
        dash_files.read_manifest(SLOT)


def test_slots_and_size_follow_users(folder):
    make_slot(SLOT, ["d1", "d2"])
    make_slot(OTHER, ["d2"])
    assert list(dash_files.slots("d1")) == [SLOT] and sorted(dash_files.slots("d2")) == sorted([SLOT, OTHER])
    assert dash_files.size_of("d2") == 2 * len(b"PAR1data") and dash_files.size_of("없음") == 0
    assert sorted(dash_files.all_slots()) == sorted([SLOT, OTHER])


def test_forget_keeps_slots_other_dashboards_use(folder):
    shared = make_slot(SLOT, ["d1", "d2"])
    mine = make_slot(OTHER, ["d1"])
    assert dash_files.forget("d1") == []
    assert shared.is_dir() and list(dash_files.read_manifest(SLOT)["users"]) == ["d2"]
    assert not mine.exists()


def test_unknown_file_keeps_the_slot(folder):
    kept = make_slot(SLOT, ["d1"])
    (kept / "메모.txt").write_text("사용자 파일", encoding="utf-8")
    assert dash_files.forget("d1") == [SLOT]
    assert (kept / "메모.txt").is_file() and dash_files.read_manifest(SLOT)["users"] == {}


def test_drop_user_deletes_only_when_nobody_uses(folder):
    make_slot(SLOT, ["d1", "d2"])
    assert dash_files.drop_user(SLOT, "d1") is False and dash_files.read_manifest(SLOT) is not None
    assert dash_files.drop_user(SLOT, "d2") is True and not (dash_files.root() / SLOT).exists()


def test_orphans_are_slots_no_known_dashboard_uses(folder):
    make_slot(SLOT, ["gone"])
    make_slot(OTHER, ["live", "gone"])
    (dash_files.root() / "남의폴더").mkdir()
    assert dash_files.orphans({"live"}) == [{"slot": SLOT, "schema": "", "table": "t", "bytes": len(b"PAR1data")}]
    assert dash_files.drop_orphans({"live"}) == [SLOT]
    assert (dash_files.root() / OTHER).is_dir() and (dash_files.root() / "남의폴더").is_dir()
