"""LLM 어댑터(NL2SQL이 묻는 곳). 설정은 env 파일 하나: 앱에는 그 경로만 저장하고(llm.json), 키는 물을 때마다 파일에서 읽어 쓴 뒤 버린다.
env 파일:
    LLM_PROVIDER=anthropic        # anthropic(Claude Messages API) 또는 openai(Chat Completions 호환: OpenAI·Azure·사내 게이트웨이·Ollama 등)
    LLM_API_KEY=...               # Ollama처럼 키가 없는 곳은 비워도 된다(openai만)
    LLM_MODEL=...
    LLM_BASE_URL=...              # 선택. 없으면 각 회사의 기본 주소. https만(이 PC의 localhost·127.0.0.1은 http도)
    LLM_MAX_TOKENS=1024           # 선택
경로를 정하지 않으면 NotConfigured(바깥으로 아무것도 보내지 않음)."""
import json
import urllib.parse

import settings
from adapters import transport

FILE = "llm.json"  # {"env": env 파일 경로}
PROVIDERS = {"anthropic": "https://api.anthropic.com", "openai": "https://api.openai.com/v1"}
ANTHROPIC_VERSION = "2023-06-01"
TIMEOUT = 120  # LLM 답을 기다리는 초
DEFAULT_MAX_TOKENS = 1024


class NotConfigured:
    """LLM이 없는 상태. 프롬프트는 만들어 볼 수 있지만 묻지는 못한다."""
    name = "없음"

    def complete(self, system, question):
        raise ValueError("LLM이 설정되어 있지 않습니다. LLM env 파일 경로를 넣고 [읽기]를 누르세요. [프롬프트 보기]로 보낼 내용은 볼 수 있습니다.")


def read_config(path):
    """env 파일을 읽어 검사한 설정 {provider, key, model, base, max_tokens}. 오류 문구에는 키를 넣지 않는다."""
    env = settings.read_env(path)
    provider = env.get("LLM_PROVIDER", "").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(f"env 파일의 LLM_PROVIDER는 {' 또는 '.join(PROVIDERS)}여야 합니다.")
    key, model = env.get("LLM_API_KEY", "").strip(), env.get("LLM_MODEL", "").strip()
    if not model:
        raise ValueError("env 파일에 LLM_MODEL이 있어야 합니다.")
    if provider == "anthropic" and not key:
        raise ValueError("env 파일에 LLM_API_KEY가 있어야 합니다.")
    base = (env.get("LLM_BASE_URL", "").strip() or PROVIDERS[provider]).rstrip("/")
    host = urllib.parse.urlsplit(base)
    local = host.hostname in ("localhost", "127.0.0.1", "::1")
    if host.scheme != "https" and not (host.scheme == "http" and local):
        raise ValueError("LLM_BASE_URL은 https://로 시작해야 합니다(이 PC의 localhost만 http 가능).")
    tokens = env.get("LLM_MAX_TOKENS", "").strip() or str(DEFAULT_MAX_TOKENS)
    if not tokens.isdigit() or not 1 <= int(tokens) <= 32000:
        raise ValueError("LLM_MAX_TOKENS는 1~32000 사이의 정수여야 합니다.")
    return {"provider": provider, "key": key, "model": model, "base": base, "max_tokens": int(tokens)}


class EnvLLM:
    """env 파일로 설정한 LLM. 물을 때마다 파일을 다시 읽는다(키를 메모리에 오래 두지 않고, 파일을 고치면 바로 반영)."""

    def __init__(self, path):
        self.path = path
        found = read_config(path)
        self.name = f"{found['provider']} · {found['model']}"

    def complete(self, system, question):
        c = read_config(self.path)
        try:
            if c["provider"] == "anthropic":
                body = {"model": c["model"], "max_tokens": c["max_tokens"], "system": system, "messages": [{"role": "user", "content": question}]}
                reply = json.loads(transport.post(f"{c['base']}/v1/messages", json.dumps(body, ensure_ascii=False).encode(),
                                                  {"x-api-key": c["key"], "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}, TIMEOUT))
                return "".join(part.get("text", "") for part in reply.get("content", []) if part.get("type") == "text")
            body = {"model": c["model"], "max_tokens": c["max_tokens"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": question}]}
            headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {c['key']}"} if c["key"] else {})}
            reply = json.loads(transport.post(f"{c['base']}/chat/completions", json.dumps(body, ensure_ascii=False).encode(), headers, TIMEOUT))
            return reply["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            raise ValueError("LLM의 답을 읽을 수 없습니다(모양이 예상과 다름).") from None
        except ValueError as e:
            raise ValueError(str(e).replace(c["key"], "***") if c["key"] else str(e)) from None


def env_path():
    found = (settings.read(FILE) or {}).get("env")
    return found if isinstance(found, str) and found else ""


def save_env_path(path):
    """env 파일 경로를 읽어 검사한 뒤 저장한다(빈 글자면 LLM을 쓰지 않음). 그 어댑터를 돌려준다."""
    path = path.strip() if isinstance(path, str) else ""
    found = EnvLLM(path) if path else NotConfigured()
    settings.write(FILE, {"env": path})
    return found


def client():
    """지금 쓰는 LLM 어댑터. 경로가 없으면 NotConfigured, 파일을 읽을 수 없으면 그 이유로 ValueError."""
    path = env_path()
    return EnvLLM(path) if path else NotConfigured()
