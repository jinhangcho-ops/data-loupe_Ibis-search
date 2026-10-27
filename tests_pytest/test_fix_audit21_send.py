"""감사 21-5: [보내기]·예약 알림의 표 글자가 칸 하나·전체 길이 상한 없이 나가 서비스(Notion·Discord·Slack·Teams)가 400으로 거절하던 것.
칸은 notify.table_text에서 자르고, 어댑터는 서비스 한도에서 자른 뒤 '… 잘림'을 붙인다. 밖으로 보내지 않고 보낼 본문만 잡아 본다."""
import json

import pytest

from integrations import notify
from integrations.adapters import notion, slack_out, transport, webhook


def table(width=5_000, cols=1, rows=50):
    return {"columns": [f"c{i}" for i in range(cols)], "rows": [{f"c{i}": "가" * width for i in range(cols)} for _ in range(rows)]}


def message(t):
    return notify.message({"title": "제목", "text": "내용", "table": t})


@pytest.fixture
def sent(monkeypatch):
    """transport.post·request를 바꿔 보낼 본문만 모은다(외부 접속 없음)."""
    got = []
    monkeypatch.setattr(transport, "post", lambda url, data, headers: got.append((url, data)) or b'{"ok": true}')
    monkeypatch.setattr(transport, "request", lambda method, url, data, headers: got.append((url, data)) or b"{}")
    return got


def test_cell_is_cut():
    lines = notify.table_text(table()).splitlines()
    assert len(lines) == 51 and all(len(line) <= notify.MAX_CELL + 1 for line in lines[1:]) and lines[1].endswith("…")


def test_notion_rich_at_most_100(sent):
    m = message(table())
    m["table"] = "가" * 300_000  # 칸을 잘라도 컬럼이 많으면 넘을 수 있다
    notion.send({"token": "t", "page": "0" * 32}, m)
    code = json.loads(sent[0][1])["children"][-1]["code"]["rich_text"]
    assert len(code) <= 100 and all(len(r["text"]["content"]) <= notion.TEXT_LIMIT for r in code) and code[-1]["text"]["content"].endswith("… 잘림")


def test_discord_content_at_most_2000(sent):
    m = message(table(width=200, cols=5))
    webhook.send({"url": "https://discord.com/api/webhooks/1/x", "content": "json", "template": webhook.PRESETS[2]["template"]}, m)
    content = json.loads(sent[0][1])["content"]
    assert len(content) <= 2_000 and content.endswith("… 잘림")


def test_teams_body_fits(sent):
    m = message(table(width=200, cols=5))
    webhook.send({"url": "https://x.webhook.office.com/webhookb2/a", "content": "json", "template": webhook.PRESETS[1]["template"]}, m)
    assert len(sent[0][1]) <= 28_000 and json.loads(sent[0][1])["text"].endswith("… 잘림")


def test_other_webhook_untouched(sent):
    m = message(table(width=200, cols=5))
    webhook.send({"url": "https://example.com/hook", "content": "json", "template": webhook.PRESETS[0]["template"]}, m)
    assert json.loads(sent[0][1])["table"] == m["table"]


def test_slack_text_at_most_40000(sent):
    m = message(table())
    m["table"] = "가" * 100_000
    slack_out.send({"token": "t", "channel": "C1"}, m)
    text = json.loads(sent[0][1])["text"]
    assert len(text) <= 40_000 and text.endswith("… 잘림\n```")
