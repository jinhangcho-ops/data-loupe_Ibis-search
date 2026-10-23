"""다른 앱과 잇는 어댑터. 코어(검색·지표·메시지 만들기)는 어느 앱인지 모르고, 어댑터만 그 앱의 API를 안다.
- 보내기(SENDERS): 종류마다 {label, image, fields, send(칸 값, 메시지), check(칸 값) → 확인 글}. 새 앱은 모듈 하나를 만들어 SENDERS에 더한다.
  메시지는 notify.message가 만든 {title, text, rows, duration, table, image(PNG bytes 또는 None)}.
- 받기: slack_bot(Slack Socket Mode). 받은 글은 commands.answer(코어)가 답한다.
비밀 값(토큰·주소)은 어댑터가 저장하지 않는다. 부르는 쪽이 credentials로 풀어 칸 값으로 넘긴다."""
from integrations.adapters import notion, slack_out, webhook

SENDERS = {"slack": slack_out.KIND, "notion": notion.KIND, "webhook": webhook.KIND}
