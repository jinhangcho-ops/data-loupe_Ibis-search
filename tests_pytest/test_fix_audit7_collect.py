"""7차 감사 7D(수집·연동): 고치기 전에는 실패하던 것들을 하나씩 재현한다. 파일 잠금은 Windows 단독 핸들로 실제로 건다."""
import ctypes
import sys
import threading

import pytest

from collect import collect_config
from collect import collect_job
from collect import collect_limits as limits
from collect.collect_http import Halt
from core import querying
from core import settings
from integrations import commands
from integrations.adapters import slack_bot

SECRET = "Kim Minsu 010-1234-5678"


def config(fmt):
    return collect_config.normalize({"mode": "api", "request": {"url": "https://example.com/x"}, "output": {"path": "out/a", "format": fmt}})


# ---------- 7D1 형식만 다른 구성은 같은 .part를 쓰므로 같은 잠금 ----------

def test_formats_sharing_part_folder_share_lock():
    csv, parquet = config("csv"), config("parquet")
    path_a, path_b = collect_job.output_path(csv), collect_job.output_path(parquet)
    assert collect_job.parts(path_a)["part"] == collect_job.parts(path_b)["part"]
    assert collect_job.lock_of(csv) == collect_job.lock_of(parquet)


def test_different_results_keep_different_locks():
    other = collect_config.normalize({"mode": "api", "request": {"url": "https://example.com/x"}, "output": {"path": "out/b", "format": "csv"}})
    assert collect_job.lock_of(config("csv")) != collect_job.lock_of(other)


# ---------- 7D2 사용 기록을 잠깐 못 읽는 것은 깨짐이 아니다 ----------

@pytest.mark.skipif(sys.platform != "win32", reason="단독 핸들은 Windows 방식")
def test_usage_file_briefly_held_is_not_broken():
    limits.save("g", {"per_minute": 100})
    limits.take("g", threading.Event())
    path = settings.PATH.parent / limits.USAGE
    kernel = ctypes.windll.kernel32
    kernel.CreateFileW.restype = ctypes.c_void_p
    handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)  # 읽기, 공유 없음
    timer = threading.Timer(0.1, lambda: kernel.CloseHandle(ctypes.c_void_p(handle)))
    timer.start()
    try:
        limits.take("g", threading.Event())  # 핸들이 풀릴 때까지 기다렸다 통과해야 한다
    finally:
        timer.join()
    assert limits.load() is not None


def test_broken_usage_file_is_still_broken():
    (settings.PATH.parent / limits.USAGE).write_text("{not json", encoding="utf-8")
    assert limits.load() is None
    with pytest.raises(Halt):
        limits.take("g", threading.Event())
    limits.reset("g")
    assert limits.load() == {}


# ---------- 7D3 Slack 오류 글에 DB가 넣은 데이터 값이 나가지 않는다 ----------

def test_slack_command_error_hides_data_values(monkeypatch):
    def boom(name):
        raise ValueError(f"Conversion Error: Could not convert string '{SECRET}' to INT32")
    monkeypatch.setattr(querying, "saved_search", boom)
    out = commands.answer("검색 매출")
    assert SECRET not in out and "010" not in out
    assert "데이터 값" in out


def test_slack_command_plain_error_still_shown(monkeypatch):
    def boom(name):
        raise ValueError("그런 검색이 없어요")
    monkeypatch.setattr(querying, "saved_search", boom)
    assert "그런 검색이 없어요" in commands.answer("검색 매출")


def test_slack_respond_unexpected_error_hides_data_values():
    sent = []
    def answer(text):
        raise RuntimeError(f"Invalid Input Error: '{SECRET}'")
    bot = slack_bot.SlackBot("b", "a", [], [], answer, make=lambda b, a: (None, None), send=lambda url, out: sent.append(out))
    bot.respond("url", "x")
    assert sent and SECRET not in sent[0]
