"""env 파일로 설정하는 LLM 어댑터(Anthropic Messages, OpenAI 호환 Chat Completions). 실제로 부르지 않고 transport.post를 바꿔 끼운다.
키는 테스트 안에서 지어낸 값이다."""
import json

import pytest

from integrations.adapters import llm

KEY = "sk-made-up-key-123"


@pytest.fixture
def posted(monkeypatch):
    from integrations.adapters import transport
    out = []

    def fake(url, data, headers, timeout=15):
        out.append((url, json.loads(data), headers, timeout))
        if "anthropic" in url:
            return json.dumps({"content": [{"type": "text", "text": "select 1"}]}).encode()
        if "fail" in url:
            raise ValueError(f"보내지 못했습니다(상태 401): bad key {KEY}")
        return json.dumps({"choices": [{"message": {"content": "select 2"}}]}).encode()
    monkeypatch.setattr(transport, "post", fake)
    return out


def write_env(tmp_path, lines):
    path = tmp_path / "llm.env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_env_llm(tmp_path, folder, posted):
    a = llm.EnvLLM(write_env(tmp_path, ["LLM_PROVIDER=anthropic", f"LLM_API_KEY={KEY}", "LLM_MODEL=claude-x"]))
    assert a.name == "anthropic · claude-x" and a.complete("시스템", "질문") == "select 1"
    url, body, headers, timeout = posted[-1]
    assert url == "https://api.anthropic.com/v1/messages" and headers["x-api-key"] == KEY and timeout == llm.TIMEOUT
    assert body == {"model": "claude-x", "max_tokens": 1024, "system": "시스템", "messages": [{"role": "user", "content": "질문"}]}

    o = llm.EnvLLM(write_env(tmp_path, ["LLM_PROVIDER=openai", "LLM_MODEL=llama", "LLM_BASE_URL=http://localhost:11434/v1/"]))  # 키 없는 로컬
    assert o.complete("s", "q") == "select 2"
    url, body, headers, _ = posted[-1]
    assert url == "http://localhost:11434/v1/chat/completions" and "Authorization" not in headers
    assert body["messages"] == [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}]

    f = llm.EnvLLM(write_env(tmp_path, ["LLM_PROVIDER=openai", f"LLM_API_KEY={KEY}", "LLM_MODEL=m", "LLM_BASE_URL=https://fail.example.com/v1"]))
    with pytest.raises(ValueError) as e:
        f.complete("s", "q")
    assert KEY not in str(e.value) and "***" in str(e.value)  # 오류 문구에서 키를 가린다


@pytest.mark.parametrize("lines, message", [
    (["LLM_MODEL=m"], "LLM_PROVIDER"),
    (["LLM_PROVIDER=anthropic", "LLM_MODEL=m"], "LLM_API_KEY"),
    (["LLM_PROVIDER=openai"], "LLM_MODEL"),
    (["LLM_PROVIDER=openai", "LLM_MODEL=m", "LLM_BASE_URL=http://example.com"], "https"),
    (["LLM_PROVIDER=openai", "LLM_MODEL=m", "LLM_MAX_TOKENS=0"], "MAX_TOKENS"),
])
def test_env_llm_bad(tmp_path, lines, message):
    with pytest.raises(ValueError, match=message):
        llm.read_config(write_env(tmp_path, lines))


def test_llm_route(client, tmp_path, folder):
    path = write_env(tmp_path, ["LLM_PROVIDER=anthropic", f"LLM_API_KEY={KEY}", "LLM_MODEL=claude-x"])
    assert client.get("/api/llm").get_json() == {"env": "", "llm": "없음"}
    assert client.post("/api/llm", json={"env": path}).get_json() == {"env": path, "llm": "anthropic · claude-x"}
    assert KEY not in (folder / "llm.json").read_text(encoding="utf-8")  # 경로만 저장한다
    assert client.post("/api/llm", json={"env": str(tmp_path / "nope.env")}).status_code == 400
    assert client.post("/api/llm", json={"env": ""}).get_json()["llm"] == "없음"
