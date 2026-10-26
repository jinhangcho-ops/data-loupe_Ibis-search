"""15차 감사 B: 주소가 query에만 있는 연결(Databricks·Athena)의 대시보드 연결 열쇠, Slack 봇 답도 보낸 요청 기록에. 값은 시험 안에서 지어낸다."""
import json
import sys
import types

from core import dash_store
from core import settings
from integrations.adapters import slack_bot


def key(url):
    return dash_store.source_key({"kind": "url", "url": url})


def test_query_only_urls_get_different_keys_without_secrets():
    dev = key("databricks://?server_hostname=dev.cloud.example&http_path=/sql/1&access_token=dapiSECRET")
    prod = key("databricks://?http_path=/sql/1&server_hostname=prod.cloud.example")
    assert dev != prod and "SECRET" not in dev and "access_token" not in dev  # 예전에는 둘 다 url:databricks://:/
    assert key("databricks://?http_path=/sql/1&server_hostname=dev.cloud.example&access_token=other") == dev  # 비밀·순서가 달라도 같은 곳이면 같은 열쇠
    assert key("athena://?region_name=ap-northeast-2&s3_staging_dir=s3://a/") != key("athena://?region_name=us-east-1&s3_staging_dir=s3://b/")
    assert key("postgres://u:p@h:5432/db?sslmode=require") == "url:postgres://h:5432/db"  # host가 있는 주소는 그대로


def test_slack_reply_is_logged(folder, monkeypatch):
    sent = []
    hook = types.ModuleType("slack_sdk.webhook")
    hook.WebhookClient = lambda url: types.SimpleNamespace(send=lambda **k: sent.append(url))
    monkeypatch.setitem(sys.modules, "slack_sdk.webhook", hook)
    url = "https://hooks.slack.com/commands/T0001/12345/AbCdEfGhIjKlMnOpQrStUvWx"
    slack_bot.reply(url, "행 50개")
    line = json.loads((settings.PATH.parent / "egress_log.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert sent == [url] and line["host"] == "hooks.slack.com" and line["kind"] == "POST" and "AbCdEf" not in line["path"]
