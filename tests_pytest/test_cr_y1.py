"""2차 감사 Y1: 미접속 기준 이탈이 영영 안 세어지던 것, [시험 보내기]에 Basic 비밀번호가 base64로 보이던 것."""
import base64
import datetime as dt

import polars as pl
from lazy import ibis

from collect import collect_config, collect_http, collect_job
from metrics import metric_calc as mc


def test_inactive_days_churn_counts_once_when_n_days_pass():
    # y는 1/31에만, k는 매달. N=45면 y는 2월 말 28일 → 3월 말 59일, 3월에 한 번 이탈.
    ev = [("y", 1, 31)] + [("k", m, 5) for m in range(1, 6)]
    t = ibis.memtable(pl.DataFrame({"uid": [e[0] for e in ev], "ts": [dt.date(2026, e[1], e[2]) for e in ev]}).to_arrow())
    got = {r["period"][:7]: r for r in mc.user_states(t, "uid", "ts", "month", inactive_days=45)}
    assert [got[f"2026-0{m}"]["churned"] for m in (2, 3, 4, 5)] == [0, 1, 0, 0]
    assert got["2026-03"]["churn_rate"] == 0.5  # 3월 시작 때 아직 이탈 아님: k, y
    assert got["2026-02"]["churn_rate"] == 0.0 and got["2026-04"]["churn_rate"] == 0.0


def test_first_page_hides_basic_auth_and_set_cookie(monkeypatch):
    config = collect_config.normalize({"mode": "api", "request": {"url": "https://{env:U}:{env:P}@api.example.com/x"}})
    seen = {}

    def fake_fetch(sent, timeout, hide):  # 네트워크 대신 받은 요청을 기록하고 쿠키를 돌려준다
        seen["sent"] = sent
        return collect_http.Reply(200, [["Set-Cookie", "sid=abc123"], ["Content-Type", "application/json"]], b"{}", sent["url"])

    monkeypatch.setattr(collect_http, "fetch", fake_fetch)
    out = collect_job.first_page(config, {"U": "alice", "P": "s3cret-pw"})
    assert any(k.lower() == "authorization" for k, _ in seen["sent"]["headers"])  # 실제로는 Basic 헤더를 보낸다
    shown = repr(out)
    assert base64.b64encode(b"alice:s3cret-pw").decode() not in shown and "s3cret" not in shown
    assert "abc123" not in shown
    assert ["Content-Type", "application/json"] in out["headers"] and ["Set-Cookie", "●●●"] in out["headers"]
    assert ["Authorization", "Basic ●●●"] in out["sent"]["headers"]
