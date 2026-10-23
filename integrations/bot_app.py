"""Slack 봇의 설정·켜기·끄기 라우트. 봇 자체는 adapters.slack_bot, 답은 commands.answer(코어)가 만든다.
설정(slack_bot.json): {keep, bot_token, app_token, users: [사용자 ID], channels: [채널 ID]}. 토큰은 credentials의 보관 방식대로
(saved는 암호문, session은 메모리에만, env는 환경변수 이름). 봇은 프로그램이 켜져 있는 동안만 돌고 그 상태는 메모리(BOT)에만 둔다.
연결을 쓰지 않으므로 라우트는 모두 차례(turn)를 기다리지 않는다(명령에 답할 때는 commands가 차례를 잡는다)."""
import re
import threading

from flask import jsonify, request

from lazy import Later
from core import settings
from core import source
from core.server import FREE, app


commands = Later("integrations.commands")
credentials = Later("core.credentials")
slack_bot = Later("integrations.adapters.slack_bot")

FILE = "slack_bot.json"
SECRETS = {"bot_token": ("봇 토큰", "xoxb-"), "app_token": ("앱 토큰", "xapp-")}
SLACK_ID = re.compile(r"[A-Z0-9]{2,20}")
MAX_IDS = 50
BOT = {"bot": None}  # 켜 둔 봇(slack_bot.SlackBot)
lock = threading.Lock()
FREE.update({"/api/bot", "/api/bot/check", "/api/bot/start", "/api/bot/stop"})


def secret_key(field):
    return f"bot:{field}"


def config():
    found = settings.read(FILE)
    return found if isinstance(found, dict) else {}


def ids(value, label):
    """"U1, U2" 같은 글이나 목록을 Slack ID 목록으로. 없으면(None) 빈 목록."""
    items = [] if value is None else value if isinstance(value, list) else re.split(r"[\s,]+", value) if isinstance(value, str) else None
    if items is None:
        raise ValueError(f"{settings.josa(label, '은', '는')} 쉼표로 나눈 ID여야 합니다.")
    items = [str(i).strip().upper() for i in items if str(i).strip()]
    bad = [i for i in items if not SLACK_ID.fullmatch(i)]
    if bad:
        raise ValueError(f"{label}에 Slack ID가 아닌 값이 있습니다: {', '.join(bad[:5])} (예: U0123ABCD, C0123ABCD)")
    if len(items) > MAX_IDS:
        raise ValueError(f"{settings.josa(label, '은', '는')} {MAX_IDS}개까지입니다.")
    return list(dict.fromkeys(items))


def view():
    """화면에 줄 설정(토큰은 쓸 수 있는지만), 보관 방식들, 켜져 있는지, 최근 요청."""
    c, bot = config(), BOT["bot"]
    keep = credentials.keep_of(c)
    return jsonify(saved=bool(c), keep=keep, keeps=credentials.KEEPS, users=c.get("users", []), channels=c.get("channels", []),
                   **{f: credentials.shown(secret_key(f), keep, c.get(f)) if f in c else False for f in SECRETS},
                   running=bool(bot and bot.running()), team=bot.team if bot else "", log=list(bot.log)[::-1] if bot else [])


def tokens():
    """설정의 두 토큰을 푼다. 없으면 이유로 ValueError."""
    c = config()
    if not c:
        raise ValueError("먼저 봇 설정을 저장하세요.")
    keep, found = credentials.keep_of(c), {}
    for field, (label, _) in SECRETS.items():
        value = credentials.resolve(secret_key(field), keep, c.get(field))
        if value is None:
            raise ValueError(credentials.missing(keep, label, c.get(field, "")))
        found[field] = value
    return c, found


def stop():
    """켜 둔 봇을 끈다(없으면 아무 일도 하지 않는다). 프로그램을 닫을 때도 부른다."""
    with lock:
        bot, BOT["bot"] = BOT["bot"], None
    if bot:
        bot.stop()


@app.get("/api/bot")
def bot_state():
    return view()


@app.post("/api/bot")
def save_bot():
    """봇 설정을 저장한다. 토큰 칸을 비우면 같은 보관 방식으로 둔 값을 그대로 쓴다. 켜져 있으면 끈다(새 설정으로 다시 켠다)."""
    body, old = request.json, config()
    keep = credentials.check_keep(body.get("keep", credentials.DEFAULT_KEEP))
    item = {"keep": keep, "users": ids(body.get("users"), "허용할 사용자"), "channels": ids(body.get("channels"), "허용할 채널")}
    for field, (label, prefix) in SECRETS.items():
        value = body.get(field)
        if isinstance(value, str) and value.strip() and keep != "env" and not value.strip().startswith(prefix):
            raise ValueError(f"{label}은 {prefix}로 시작합니다.")
        kept = credentials.store(secret_key(field), keep, value, credentials.keep_of(old) if old else None, old.get(field))
        if kept is None:
            raise ValueError(f"'{label}' 칸을 입력하세요." + (" (환경변수 이름)" if keep == "env" else ""))
        item[field] = kept
    stop()
    settings.write(FILE, item)
    return view()


@app.post("/api/bot/check")
def check_bot():
    """봇을 켜지 않고 두 토큰을 확인한다."""
    _, found = tokens()
    try:
        return jsonify(message=slack_bot.check(found["bot_token"], found["app_token"]))
    except ValueError as e:
        raise ValueError(source.mask(str(e), list(found.values()))) from None


@app.post("/api/bot/start")
def start_bot():
    """봇을 켠다. 허용한 사용자가 없으면 켜지 않는다(아무도 쓸 수 없는 봇)."""
    c, found = tokens()
    if not c.get("users"):
        raise ValueError("허용할 사용자 ID를 하나 이상 넣고 저장하세요. 그 사람들의 명령만 받습니다.")
    stop()
    bot = slack_bot.SlackBot(found["bot_token"], found["app_token"], c["users"], c.get("channels", []), commands.answer)
    try:
        bot.start()
    except ValueError as e:
        bot.stop()
        raise ValueError(source.mask(str(e), list(found.values()))) from None
    with lock:
        BOT["bot"] = bot
    return view()


@app.post("/api/bot/stop")
def stop_bot():
    stop()
    return view()
