"""Slack에서 받기 어댑터(봇): Socket Mode로 Slack에 연결해 /ibis 명령을 받고, 답은 answer(글 → 글, 코어의 commands.answer)가 만든다.
Socket Mode는 이 PC가 Slack으로 나가는 연결만 쓰므로 열어 둘 포트·공개 주소가 없다. 필요한 토큰: 봇 토큰(xoxb-)과 앱 토큰(xapp-, connections:write).
허용한 사용자(와, 정했으면 채널)에서 온 명령만 받고, 답은 요청한 사람에게만 보인다(ephemeral). slack_sdk는 봇을 켤 때 올린다."""
import collections
import datetime as dt
import threading

LOG_SIZE = 20  # 기억할 최근 요청 수(메모리)


def clients(bot_token, app_token):
    """(Web API 클라이언트, Socket Mode 클라이언트). 테스트는 이것을 바꿔 끼운다."""
    from slack_sdk.socket_mode.builtin.client import SocketModeClient
    from slack_sdk.web import WebClient
    web = WebClient(token=bot_token)
    return web, SocketModeClient(app_token=app_token, web_client=web)


def reply(response_url, text):
    """명령에 답한다(요청한 사람에게만 보임). response_url은 Slack이 명령마다 주는 답장 주소."""
    from slack_sdk.webhook import WebhookClient
    from integrations.adapters import slack_out, transport
    transport.log("POST", response_url)  # 답에 데이터 행이 실릴 수 있어 보낸 요청 기록에 남긴다(slack_sdk는 transport를 거치지 않는다)
    resp = WebhookClient(response_url).send(text=transport.cut(text, slack_out.TEXT_LIMIT), response_type="ephemeral")
    if resp.status_code != 200:  # WebhookClient는 4xx·5xx에도 예외를 내지 않는다
        raise ValueError(f"답장 실패: {resp.status_code} {str(resp.body)[:100]}")


def slack_error(e):
    """slack_sdk 오류를 한국어 글로(토큰은 들어 있지 않다)."""
    response = getattr(e, "response", None)
    code = response.get("error") if response is not None and hasattr(response, "get") else None
    return f"Slack 오류: {code}" if code else f"Slack에 연결하지 못했습니다: {e}"


def check(bot_token, app_token):
    """봇을 켜지 않고 두 토큰을 확인한다: 봇 토큰은 auth.test, 앱 토큰은 apps.connections.open(Socket Mode 주소를 받기만 한다)."""
    web, _ = clients(bot_token, app_token)
    try:
        who = web.auth_test()
    except Exception as e:
        raise ValueError("봇 토큰: " + slack_error(e)) from None
    try:
        from slack_sdk.web import WebClient
        WebClient(token=app_token).apps_connections_open(app_token=app_token)
    except Exception as e:
        raise ValueError(f"봇 토큰은 맞습니다(워크스페이스 {who.get('team')}). 앱 토큰: " + slack_error(e)) from None
    return f"두 토큰 모두 맞습니다. 워크스페이스 {who.get('team')}, 봇 {who.get('user')}."


class SlackBot:
    """Socket Mode 봇 하나. start()로 연결하고 stop()으로 끊는다. users·channels는 허용 목록(channels가 비면 모든 채널)."""

    def __init__(self, bot_token, app_token, users, channels, answer, make=None, send=None):
        self.users, self.channels, self.answer, self.send = set(users), set(channels), answer, send or reply
        self.web, self.socket = (make or clients)(bot_token, app_token)
        self.log = collections.deque(maxlen=LOG_SIZE)
        self.team = ""

    def start(self):
        try:
            self.team = self.web.auth_test().get("team", "")
        except Exception as e:
            raise ValueError("봇 토큰: " + slack_error(e)) from None
        self.socket.socket_mode_request_listeners.append(self.on_request)
        try:
            self.socket.connect()
        except Exception as e:
            raise ValueError("앱 토큰: " + slack_error(e)) from None

    def stop(self):
        try:
            self.socket.close()
        except Exception:
            pass

    def running(self):
        try:
            return bool(self.socket.is_connected())
        except Exception:
            return False

    def allowed(self, user, channel):
        return user in self.users and (not self.channels or channel in self.channels)

    def on_request(self, client, req):
        """Slack이 보낸 것 하나. 3초 안에 받았다고(ack) 해야 하므로 먼저 답하고, 명령은 따로 돌려 response_url로 답한다."""
        from slack_sdk.socket_mode.response import SocketModeResponse
        if req.type != "slash_commands":
            client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
            return
        p = req.payload
        user, channel, text = p.get("user_id", ""), p.get("channel_id", ""), p.get("text", "")
        ok = self.allowed(user, channel)
        entry = {"at": dt.datetime.now().isoformat(timespec="seconds"), "user": user, "channel": channel, "text": text[:200], "allowed": ok}
        self.log.append(entry)
        if not ok:
            client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id, payload={
                "text": "이 사용자·채널에서는 쓸 수 없습니다. 프로그램의 봇 설정에서 허용한 사용자(와 채널)만 씁니다."}))
            return
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id, payload={"text": f"처리 중: {text or '도움'}"}))
        threading.Thread(target=self.respond, args=(p.get("response_url", ""), text, entry), daemon=True).start()

    def respond(self, response_url, text, entry=None):
        """명령을 돌려 response_url로 답한다. Slack에는 마스킹된 안내 글만 가고, 실패한 까닭(전체 글)은 이 PC의 봇 기록(entry["error"], 메모리)에만 남긴다."""
        from integrations import commands
        commands.failure.error = ""
        try:
            out = self.answer(text)
        except Exception as e:  # 코어가 예상하지 못한 오류도 Slack에 알린다
            out = commands.error_text(e)
            commands.failure.error = commands.detail(e)
        if entry is not None and commands.failure.error:
            entry["error"] = commands.failure.error
        try:
            self.send(response_url, out)
        except Exception as e:  # 답장 주소가 만료되면(30분) 보낼 곳이 없어 이 PC의 봇 기록에만 남긴다
            if entry is not None:  # 명령이 실패한 까닭은 남기고 이어 붙인다
                entry["error"] = "; ".join(filter(None, [entry.get("error"), str(e) or type(e).__name__]))
