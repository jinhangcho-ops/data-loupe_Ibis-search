"""B11 받은 Parquet 암호화 열쇠(plans/dash-blocks.md "B11 세부 명세", dashboard/dash_keys.py): AES 앱 열쇠, 대시보드 암호문, 암호화 묶음 이름, 칸 열쇠(PBKDF2), 지우기.
Windows DPAPI를 쓰므로 이 PC 계정에서만 돈다. 값은 시험 안에서 지어낸다."""
import base64

import pytest

from core import settings
from dashboard import dash_keys

SALT = base64.b64encode(b"0123456789abcdef").decode()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(dash_keys, "PASS_ROUNDS", 1000)  # 시험은 빨리(규칙은 같다)
    monkeypatch.setattr(dash_keys, "DERIVED", {})


def test_none_has_no_key(folder):
    assert dash_keys.group_of("d1", "none") == "none" and dash_keys.slot_key("none", "d1", "") is None
    assert dash_keys.load() == {"app": {}, "pass": {}}


def test_app_keys_are_shared_and_kept_encrypted(folder):
    a256 = dash_keys.app_key("aes256")
    assert len(base64.b64decode(a256)) == 32 and len(base64.b64decode(dash_keys.app_key("aes128"))) == 16
    assert dash_keys.slot_key("aes256", "d1", "") == dash_keys.slot_key("aes256", "d2", "") == a256  # 같은 종류는 대시보드끼리 같은 열쇠
    assert dash_keys.group_of("d1", "aes256") == "aes256"
    raw = (settings.PATH.parent / dash_keys.KEYS_FILE).read_text(encoding="utf-8")
    assert a256 not in raw and '"blob"' in raw


def test_pass_groups_follow_the_text(folder):
    with pytest.raises(ValueError, match="암호문을"):
        dash_keys.group_of("d1", "pass")
    dash_keys.set_pass("d1", "봄비 내리는 밤")
    dash_keys.set_pass("d2", "봄비 내리는 밤")
    dash_keys.set_pass("d3", "다른 암호문")
    g1 = dash_keys.group_of("d1", "pass")
    assert g1.startswith("pass:") and g1 == dash_keys.group_of("d2", "pass") != dash_keys.group_of("d3", "pass")
    assert dash_keys.slot_key(g1, "d1", SALT) == dash_keys.slot_key(g1, "d2", SALT)
    assert len(base64.b64decode(dash_keys.slot_key(g1, "d1", SALT))) == 32
    with pytest.raises(ValueError):
        dash_keys.set_pass("d1", "")


def test_pass_typed_again_on_another_pc_gives_the_same_key(folder):
    dash_keys.set_pass("d1", "봄비 내리는 밤")
    key = dash_keys.slot_key(dash_keys.group_of("d1", "pass"), "d1", SALT)
    (settings.PATH.parent / dash_keys.KEYS_FILE).unlink()  # 다른 PC: 열쇠 파일을 못 가져왔다
    dash_keys.DERIVED.clear()
    dash_keys.set_pass("d1", "봄비 내리는 밤")
    assert dash_keys.slot_key(dash_keys.group_of("d1", "pass"), "d1", SALT) == key  # 칸 소금(pull.json)으로 같은 열쇠(사용자 답 2-10)


def test_forget_drops_only_the_dashboard_pass(folder):
    dash_keys.set_pass("d1", "하나")
    dash_keys.set_pass("d2", "둘")
    a = dash_keys.app_key("aes128")
    dash_keys.forget("d1")
    assert set(dash_keys.load()["pass"]) == {"d2"} and dash_keys.app_key("aes128") == a
