"""LLM 어댑터 자리(NL2SQL). 지금은 스텁만 있다: 바깥으로 아무것도 보내지 않고 "설정되지 않음"을 알린다.
실제 LLM을 붙이려면 complete(system, question) → 답 글을 가진 객체를 만들어 CLIENTS에 더하고 ACTIVE를 그 이름으로 바꾼다.
API 키 같은 비밀 값은 credentials(saved·session·env)로 두고, 어댑터는 저장하지 않는다."""


class NotConfigured:
    """LLM이 없는 상태. 프롬프트는 만들어 볼 수 있지만 묻지는 못한다."""
    name = "없음(스텁)"

    def complete(self, system, question):
        raise ValueError("LLM이 연결되어 있지 않습니다(NL2SQL은 뼈대만 있음). [프롬프트 보기]로 LLM에 보낼 내용을 확인할 수 있습니다.")


CLIENTS = {"stub": NotConfigured()}
ACTIVE = "stub"


def client():
    """지금 쓰는 LLM 어댑터."""
    return CLIENTS[ACTIVE]
